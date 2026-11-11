"""The judge scenarios: idempotent per name, resumable, capped, never waiting.

`specs/2026-11-11-judge-scenarios/validation.md` section 1. The outcomes (what
each scenario reconciles to) are proven against the real sandbox, not here; the
fake gateway returns ORD-0013's captured responses with fresh identifiers, so
these tests hold the state machine, the caps and the markers.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.app import app
from reckonai.config import get_settings
from reckonai.models import Order, RawPayload
from reckonai.paypal import capture
from reckonai.paypal import live as paypal_live
from reckonai.sim import data, judge, seed
from reckonai.sim import live as write_side

FIXTURE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "fixtures"
    / "paypal"
    / "ORD-0013.json"
)
SETTINGS = SimpleNamespace(judge_max_sets=25, judge_sets_per_client_per_day=2)


class Gateway:
    """Returns ORD-0013's real responses, re-identified per order."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.fail_on = fail_on
        self.created: list[str] = []
        self.refunds: list[dict[str, Any]] = []
        self.reads = 0
        self._before: dict[str, dict[str, Any]] = {}
        self._after: dict[str, dict[str, Any]] = {}
        self._refunded: set[str] = set()

    def _clone(self, ref: str, n: int, *, refunded: bool) -> dict[str, Any]:
        raw = FIXTURE.read_text(encoding="utf-8")
        doc = json.loads(raw)
        text = raw.replace(doc["order"]["id"], f"PPORD{n}")
        text = text.replace("ORD-0013-1", f"{ref}-1")
        text = text.replace("6AP63246WF1540214", f"CAP{n}")
        out = json.loads(text)
        if not refunded:
            for unit in out["order"]["purchase_units"]:
                unit["payments"].pop("refunds", None)
        return out

    def create_order(self, **kw: Any) -> capture.LiveOrder:
        if self.fail_on is not None and len(self.created) == self.fail_on:
            raise capture.CaptureUnavailable("sandbox down")
        ref = kw["order_ref"]
        n = len(self.created) + 1
        self.created.append(ref)
        doc = self._clone(ref, n, refunded=False)
        self._before[f"PPORD{n}"] = doc["order"]
        self._after[f"PPORD{n}"] = self._clone(ref, n, refunded=True)["order"]
        return capture.LiveOrder(
            order_id=f"PPORD{n}", captures=(f"CAP{n}",), document=doc
        )

    def refund(self, **kw: Any) -> dict[str, Any]:
        self.refunds.append(kw)
        self._refunded.add("PPORD" + str(kw["capture_id"]).removeprefix("CAP"))
        return {"id": f"RF{len(self.refunds)}"}

    def read_order(self, order_id: str, merchant_id: str) -> dict[str, Any]:
        self.reads += 1
        source = self._after if order_id in self._refunded else self._before
        return copy.deepcopy(source[order_id])


@pytest.fixture
def tenant_id(session: Session) -> uuid.UUID:
    built = seed.build(
        session,
        data.Dataset(orders=()),
        tenant_slug=f"jd-{uuid.uuid4().hex[:8]}",
        tenant_name="Judge",
    )
    session.flush()
    return built.tenant_id


def _orders(session: Session, tenant_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id)
        )
        or 0
    )


def _ensure(
    session: Session, tenant_id: uuid.UUID, gw: Gateway, name: str = "alice", **kw: Any
) -> dict[str, Any]:
    return judge.ensure(
        session, tenant_id, name, gw, settings=kw.pop("settings", SETTINGS), **kw
    )


def test_a_name_creates_six_orders_with_the_expected_refs(
    session: Session, tenant_id: uuid.UUID
) -> None:
    out = _ensure(session, tenant_id, Gateway())
    assert _orders(session, tenant_id) == 6
    assert [r["ref"] for r in out["scenarios"]] == [
        "JUDGE-ALICE-S1-CLEAN",
        "JUDGE-ALICE-S2-COMMISSION",
        "JUDGE-ALICE-S3-HELD",
        "JUDGE-ALICE-S4-LATE-REFUND",
        "JUDGE-ALICE-S5-NO-LEDGER",
        "JUDGE-ALICE-S6-DUPLICATE",
    ]
    assert "error" not in out


def test_the_delayed_scenario_is_requested_delayed(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _ensure(session, tenant_id, Gateway())
    order = session.scalars(
        select(Order).where(Order.external_ref == "JUDGE-ALICE-S3-HELD")
    ).one()
    assert order.disbursement_mode == "DELAYED"


def test_a_repeat_creates_nothing(session: Session, tenant_id: uuid.UUID) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    session.flush()
    before = (len(gw.created), len(gw.refunds), _orders(session, tenant_id))
    for _ in range(3):
        _ensure(session, tenant_id, gw)
        session.flush()
    assert (len(gw.created), len(gw.refunds), _orders(session, tenant_id)) == before


def test_a_different_name_gets_its_own_set(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw, "alice")
    _ensure(session, tenant_id, gw, "bob")
    assert _orders(session, tenant_id) == 12
    assert len(set(gw.created)) == 12


def test_a_failure_keeps_progress_and_the_next_call_resumes(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway(fail_on=2)
    out = _ensure(session, tenant_id, gw)
    assert out["complete"] is False and "sandbox down" in out["error"]
    assert _orders(session, tenant_id) == 2
    gw.fail_on = None
    _ensure(session, tenant_id, gw)
    assert _orders(session, tenant_id) == 6
    assert len(gw.created) == 6 and len(set(gw.created)) == 6, "none placed twice"


def test_a_network_error_keeps_the_orders_already_placed(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    real = gw.create_order

    def flaky(**kw: Any) -> capture.LiveOrder:
        if len(gw.created) == 2:
            raise httpx.ReadTimeout("slow")
        return real(**kw)

    gw.create_order = flaky  # type: ignore[method-assign]
    out = _ensure(session, tenant_id, gw)
    assert out["complete"] is False and out["error"] == "slow"
    assert _orders(session, tenant_id) == 2, "what was placed stays"
    gw.create_order = real  # type: ignore[method-assign]
    _ensure(session, tenant_id, gw)
    assert _orders(session, tenant_id) == 6


def test_no_paypal_refund_is_made_before_the_order_has_arrived(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    assert gw.refunds == [], "S5 waits for its order to arrive; S4 makes none"


def test_the_paypal_refund_waits_for_the_order_then_happens_once(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    out = judge.ingest_missing(session, tenant_id, "alice", gw)
    assert all(r["paypal_side_ingested"] for r in out["scenarios"])
    assert {r["path"] for r in out["scenarios"]} == {"fallback"}

    out = _ensure(session, tenant_id, gw)
    assert len(gw.refunds) == 1, "S5's PayPal refund, once its order is in"
    assert out["complete"] is True
    _ensure(session, tenant_id, gw)
    assert len(gw.refunds) == 1


def test_ingest_missing_only_reads_what_has_not_arrived(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    judge.ingest_missing(session, tenant_id, "alice", gw)
    reads = gw.reads
    judge.ingest_missing(session, tenant_id, "alice", gw)
    assert gw.reads == reads


def test_one_failed_fetch_does_not_undo_the_others(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    real = gw.read_order
    gw.read_order = lambda order_id, merchant_id: (  # type: ignore[method-assign]
        (_ for _ in ()).throw(paypal_live.LiveFetchError("timeout"))
        if order_id == "PPORD3"
        else real(order_id, merchant_id)
    )
    out = judge.ingest_missing(session, tenant_id, "alice", gw)
    arrived = [r["paypal_side_ingested"] for r in out["scenarios"]]
    assert arrived == [True, True, False, True, True, True]
    assert "S3-HELD" in out["error"]
    gw.read_order = real  # type: ignore[method-assign]
    assert judge.ingest_missing(session, tenant_id, "alice", gw)["complete"] is False
    assert "error" not in judge.ingest_missing(session, tenant_id, "alice", gw)


def test_the_late_refund_needs_the_order_and_happens_once(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    _ensure(session, tenant_id, gw)
    with pytest.raises(judge.JudgeError, match="not arrived"):
        judge.deliver_late_refund(session, tenant_id, "alice", gw)
    judge.ingest_missing(session, tenant_id, "alice", gw)
    _ensure(session, tenant_id, gw)
    refunds = len(gw.refunds)

    first = judge.deliver_late_refund(session, tenant_id, "alice", gw)
    assert first["late_refund"] == "delivered"
    assert len(gw.refunds) == refunds + 1
    again = judge.deliver_late_refund(session, tenant_id, "alice", gw)
    assert again["late_refund"] == "already delivered"
    assert len(gw.refunds) == refunds + 1


@pytest.mark.parametrize("bad", ["", "a b", "x" * 13, "al;ce", "../x"])
def test_a_bad_name_is_refused_before_anything_happens(
    session: Session, tenant_id: uuid.UUID, bad: str
) -> None:
    gw = Gateway()
    with pytest.raises(judge.JudgeError):
        _ensure(session, tenant_id, gw, bad)
    assert gw.created == []


def test_the_total_cap_refuses_a_new_name_but_not_an_existing_one(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    cap = SimpleNamespace(judge_max_sets=1, judge_sets_per_client_per_day=9)
    _ensure(session, tenant_id, gw, "alice", settings=cap)
    with pytest.raises(judge.CapReached):
        _ensure(session, tenant_id, gw, "bob", settings=cap)
    assert _ensure(session, tenant_id, gw, "alice", settings=cap)["name"] == "ALICE"
    assert len(gw.created) == 6


def test_the_per_client_cap_counts_new_sets_only(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gw = Gateway()
    for n in ("a1", "a2"):
        _ensure(session, tenant_id, gw, n, client="ip-1")
    with pytest.raises(judge.CapReached):
        _ensure(session, tenant_id, gw, "a3", client="ip-1")
    _ensure(session, tenant_id, gw, "a1", client="ip-1")
    _ensure(session, tenant_id, gw, "a3", client="ip-2")


def test_everything_is_labelled_simulated(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _ensure(session, tenant_id, Gateway())
    session.flush()
    marketplace = session.scalars(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id, RawPayload.source == "marketplace"
        )
    ).all()
    assert marketplace and {p.endpoint for p in marketplace} == {
        "SIMULATED POST /sim/marketplace/live"
    }


# --- the HTTP surface -------------------------------------------------------


@pytest.fixture
def http(session: Session, database_url: str) -> Iterator[TestClient]:
    seed.build(
        session,
        data.Dataset(orders=()),
        tenant_slug=data.TENANT_SLUG,
        tenant_name="Judge",
    )
    session.flush()
    gateway = Gateway()
    app.dependency_overrides[judge._session] = lambda: session
    app.dependency_overrides[write_side.get_gateway] = lambda: gateway
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _enable(monkeypatch: pytest.MonkeyPatch, on: bool = True, **env: str) -> None:
    monkeypatch.setenv("JUDGE_SCENARIOS_ENABLED", "true" if on else "false")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_the_routes_are_hidden_unless_enabled(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, on=False)
    assert http.post("/sim/judge/scenarios", json={"name": "alice"}).status_code == 404
    assert http.get("/sim/judge/scenarios/alice").status_code == 404
    assert http.post("/sim/judge/scenarios/alice/ingest-missing").status_code == 404
    assert http.post("/sim/judge/scenarios/alice/late-refund").status_code == 404


def test_the_routes_need_no_secret_and_run_the_set(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    first = http.post("/sim/judge/scenarios", json={"name": "alice"})
    assert first.status_code == 200 and len(first.json()["scenarios"]) == 6
    again = http.post("/sim/judge/scenarios", json={"name": "alice"})
    assert again.json() == first.json(), "a repeat changes nothing"
    assert http.get("/sim/judge/scenarios/alice").json() == first.json()
    fallback = http.post("/sim/judge/scenarios/alice/ingest-missing")
    assert all(r["paypal_side_ingested"] for r in fallback.json()["scenarios"])


def test_the_routes_accept_a_name_and_nothing_else(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    for body in (
        {"name": "alice", "gross": "1.00"},
        {"name": "alice", "seller_ref": "x"},
        {},
    ):
        assert http.post("/sim/judge/scenarios", json=body).status_code == 422


@pytest.mark.parametrize("bad", ["a b", "x" * 13, "al;ce"])
def test_a_bad_name_is_a_422(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _enable(monkeypatch)
    assert http.post("/sim/judge/scenarios", json={"name": bad}).status_code == 422


def test_past_the_cap_a_new_name_is_a_429_and_an_old_one_still_works(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, JUDGE_MAX_SETS="1")
    assert http.post("/sim/judge/scenarios", json={"name": "alice"}).status_code == 200
    assert http.post("/sim/judge/scenarios", json={"name": "bob"}).status_code == 429
    assert http.post("/sim/judge/scenarios", json={"name": "alice"}).status_code == 200


def test_the_existing_write_routes_still_need_their_secret(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch)
    monkeypatch.setenv("SIM_WRITE_ENABLED", "true")
    monkeypatch.setenv("SIM_WRITE_SECRET", "s")
    get_settings.cache_clear()
    response = http.post("/sim/marketplace/live/orders", json={})
    assert response.status_code == 403


def test_the_enabled_probe_answers_either_way(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, on=False)
    off = http.get("/sim/judge/enabled")
    assert off.status_code == 200 and off.json() == {"enabled": False}
    _enable(monkeypatch)
    assert http.get("/sim/judge/enabled").json() == {"enabled": True}


def test_a_spoofed_forwarded_header_does_not_mint_a_new_client(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable(monkeypatch, JUDGE_SETS_PER_CLIENT_PER_DAY="1")
    first = http.post(
        "/sim/judge/scenarios", json={"name": "one"},
        headers={"X-Forwarded-For": "1.1.1.1"},
    )
    assert first.status_code == 200
    second = http.post(
        "/sim/judge/scenarios", json={"name": "two"},
        headers={"X-Forwarded-For": "2.2.2.2"},
    )
    assert second.status_code == 429, "the same caller, however the header reads"
