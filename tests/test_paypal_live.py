"""Live retrieval, against a stub transport.

`specs/2026-11-11-live-ingestion/requirements.md` F6 and F7. Each client rule is
tested in the failing direction as well: a `403` is never an absent field, a
`5xx` is never retried here, and a fee is never read from a post-refund order.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from collections.abc import Callable

import httpx2 as httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import Capture, PlatformFee, Refund
from reckonai.money import Money
from reckonai.paypal import live, replay
from reckonai.sim import data, seed

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "paypal"
CREDS = live.Credentials(
    client_id="cid", secret="secret", base="https://api-m.sandbox.paypal.com", bn="bn"
)


def _doc(ref: str) -> dict:
    return json.loads((FIXTURES / f"{ref}.json").read_text(encoding="utf-8"))


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[live.LiveClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok"})
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(wrapped))
    return live.LiveClient(CREDS, http=http), seen


def _order(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {})


def test_the_first_candidate_that_may_read_it_wins() -> None:
    order = _doc("ORD-0001")["order"]
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        who = request.headers["PayPal-Auth-Assertion"]
        calls.append(who)
        return _order(403) if len(calls) == 1 else _order(200, order)

    client, _ = _client(handler)
    got, merchant = client.fetch_order(order["id"], ["SELLER-A", "SELLER-B"])
    assert got["id"] == order["id"]
    assert merchant == "SELLER-B"
    assert calls[0] != calls[1], "each candidate is read as that seller"


def test_every_refusal_is_denied_never_an_empty_result() -> None:
    client, _ = _client(lambda r: _order(403))
    with pytest.raises(live.LiveDenied):
        client.fetch_order("ORD", ["A", "B"])


def test_a_server_error_is_not_retried_here() -> None:
    client, seen = _client(lambda r: _order(503))
    with pytest.raises(live.LiveUnavailable):
        client.fetch_order("ORD", ["A", "B"])
    reads = [r for r in seen if r.url.path.startswith("/v2/")]
    assert len(reads) == 1, "no blind retry, and no moving on to the next seller"


def test_a_transport_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client, _ = _client(handler)
    with pytest.raises(live.LiveUnavailable):
        client.fetch_order("ORD", ["A"])


def test_an_unexpected_status_is_an_error_not_a_refusal() -> None:
    client, _ = _client(lambda r: _order(400))
    with pytest.raises(live.LiveFetchError) as raised:
        client.fetch_order("ORD", ["A"])
    assert not isinstance(raised.value, live.LiveDenied)


def test_a_non_sandbox_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from reckonai.config import get_settings

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "x")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "y")
    monkeypatch.setenv("PAYPAL_API_BASE", "https://api-m.paypal.com")
    get_settings.cache_clear()
    try:
        with pytest.raises(Exception, match="non-sandbox"):
            live.credentials()
    finally:
        get_settings.cache_clear()


def _seeded(session: Session) -> uuid.UUID:
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"lv-{uuid.uuid4().hex[:8]}",
        tenant_name="Live",
    )
    session.flush()
    return built.tenant_id


def test_a_live_order_goes_through_the_replay_normalizer(session: Session) -> None:
    tenant_id = _seeded(session)
    fixture = _doc("ORD-0001")
    order = fixture["order"]
    merchant = order["purchase_units"][0]["payee"]["merchant_id"]
    client, _ = _client(lambda r: _order(200, order))

    fetched, _who = client.fetch_order(order["id"], [merchant])
    doc = live.build_document(session, tenant_id, fetched)
    replay.ingest_document(session, doc, tenant_id=tenant_id)
    session.flush()

    expected = order["purchase_units"][0]["payments"]["captures"][0]
    capture = session.scalars(
        select(Capture).where(
            Capture.tenant_id == tenant_id, Capture.external_id == expected["id"]
        )
    ).one()
    assert capture.net_amount == Money(
        expected["seller_receivable_breakdown"]["net_amount"]["value"], "USD"
    )
    assert doc["disbursement_mode"] == "INSTANT"
    assert doc["releases"] == []


def test_an_order_first_seen_after_a_refund_is_not_ingested(
    session: Session,
) -> None:
    """Rule 1 in the failing direction: the later read would record 3.00 where
    PayPal took 11.00, so the order is refused rather than ingested."""
    tenant_id = _seeded(session)
    refunded = _doc("ORD-0013")["order"]
    with pytest.raises(live.SnapshotUnavailable):
        live.build_document(session, tenant_id, refunded)


def test_a_stored_snapshot_protects_the_fee_across_a_later_refund(
    session: Session,
) -> None:
    tenant_id = _seeded(session)
    fixture = _doc("ORD-0013")

    # First sight: the order as it was at capture, before any refund.
    before = copy.deepcopy(fixture)
    before["order"] = fixture["order_at_capture"]
    live_first = live.build_document(session, tenant_id, before["order"])
    replay.ingest_document(session, live_first, tenant_id=tenant_id)
    session.flush()

    # Later: the refund arrives and the order is read again.
    after = live.build_document(session, tenant_id, fixture["order"])
    replay.ingest_document(session, after, tenant_id=tenant_id)
    session.flush()

    fee = session.scalars(
        select(PlatformFee).where(PlatformFee.tenant_id == tenant_id)
    ).first()
    assert fee is not None
    assert fee.amount == Money("11.00", "USD")
    assert session.scalars(select(Refund).where(Refund.tenant_id == tenant_id)).first()


# --- the events list, for missed-delivery recovery --------------------------


def test_events_follow_the_next_link_only_on_the_same_host() -> None:
    pages = {
        "first": {
            "events": [{"id": "WH-1"}],
            "links": [
                {
                    "rel": "next",
                    "href": f"{CREDS.base}/v1/notifications/webhooks-events?p=2",
                }
            ],
        },
        "second": {
            "events": [{"id": "WH-2"}],
            "links": [{"rel": "next", "href": "https://evil.example/next"}],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = "second" if "p=2" in str(request.url) else "first"
        return httpx.Response(200, json=pages[key])

    client, seen = _client(handler)
    from datetime import UTC, datetime

    got = client.events(
        datetime(2026, 11, 8, tzinfo=UTC), datetime(2026, 11, 11, tzinfo=UTC)
    )

    assert [e["id"] for e in got] == ["WH-1", "WH-2"]
    assert not any("evil.example" in str(r.url) for r in seen)


def test_a_refused_event_list_is_denied_never_an_empty_list() -> None:
    from datetime import UTC, datetime

    client, _ = _client(lambda r: _order(403))
    with pytest.raises(live.LiveDenied):
        client.events(
            datetime(2026, 11, 8, tzinfo=UTC), datetime(2026, 11, 11, tzinfo=UTC)
        )
