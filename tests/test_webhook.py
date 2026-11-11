"""The PayPal webhook receiver and its handler job.

`specs/2026-11-11-live-ingestion/requirements.md` F1 to F5. Every row of the
case table is a test: bad signature, duplicate, unknown type, unknown merchant,
`403` on the re-fetch, `5xx` on the re-fetch.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from collections.abc import Iterator

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.api.deps import get_session
from reckonai.app import app
from reckonai.jobs import paypal_event_job
from reckonai.models import Capture, Job, RawPayload, Seller, Tenant
from reckonai.paypal import live, webhook
from reckonai.sim import data, seed
from reckonai.webhooks import get_verifier

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "paypal"
CREDS = live.Credentials(
    client_id="cid", secret="secret", base="https://api-m.sandbox.paypal.com", bn="bn"
)
HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api-m.sandbox.paypal.com/v1/notifications/certs/x",
    "paypal-transmission-id": "tid",
    "paypal-transmission-sig": "sig",
    "paypal-transmission-time": "2026-11-11T01:00:00Z",
}


class FakeVerifier:
    def __init__(self, answer: bool | Exception) -> None:
        self.answer = answer
        self.calls = 0

    def verify(self, headers: object, event: object) -> bool:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def client(session: Session, database_url: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _use(verifier: FakeVerifier) -> None:
    app.dependency_overrides[get_verifier] = lambda: verifier


def _event(
    *,
    order_id: str,
    merchant: str | None,
    event_type: str = "PAYMENT.CAPTURE.COMPLETED",
    event_id: str | None = None,
) -> dict:
    resource: dict = {
        "id": "CAP-1",
        "supplementary_data": {"related_ids": {"order_id": order_id}},
    }
    if merchant:
        resource["payee"] = {"merchant_id": merchant}
    return {
        "id": event_id or f"WH-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "resource": resource,
    }


def _post(client: TestClient, body: dict | str) -> httpx.Response:
    content = body if isinstance(body, str) else json.dumps(body)
    return client.post("/webhooks/paypal", content=content, headers=HEADERS)


def _jobs(session: Session, tenant_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(Job).where(
            Job.tenant_id == tenant_id, Job.job_type == "paypal_event"
        )
    ) or 0


@pytest.fixture
def routed(session: Session) -> tuple[uuid.UUID, str]:
    """A tenant whose seller has a merchant id nothing else uses."""
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"wh-{uuid.uuid4().hex[:8]}",
        tenant_name="Hook",
    )
    session.flush()
    merchant = f"M-{uuid.uuid4().hex[:10]}"
    seller = session.scalars(
        select(Seller).where(Seller.tenant_id == built.tenant_id)
    ).first()
    assert seller is not None
    seller.paypal_merchant_id = merchant
    session.flush()
    return built.tenant_id, merchant


def test_a_verified_routed_event_is_stored_and_queued(
    client: TestClient, session: Session, routed: tuple[uuid.UUID, str]
) -> None:
    tenant_id, merchant = routed
    _use(FakeVerifier(True))
    event = _event(order_id="ORD-X", merchant=merchant)

    response = _post(client, event)

    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    assert _jobs(session, tenant_id) == 1
    stored = session.scalars(
        select(RawPayload).where(
            RawPayload.tenant_id == tenant_id,
            RawPayload.source_record_type == "webhook_event",
        )
    ).one()
    assert stored.body["id"] == event["id"], "the event is retained as delivered"


def test_a_bad_signature_stores_nothing(
    client: TestClient, session: Session, routed: tuple[uuid.UUID, str]
) -> None:
    tenant_id, merchant = routed
    _use(FakeVerifier(False))

    response = _post(client, _event(order_id="ORD-X", merchant=merchant))

    assert response.status_code == 401
    assert _jobs(session, tenant_id) == 0
    assert (
        session.scalars(
            select(RawPayload).where(
                RawPayload.tenant_id == tenant_id,
                RawPayload.source_record_type == "webhook_event",
            )
        ).first()
        is None
    )


def test_an_unreachable_verifier_asks_for_redelivery(client: TestClient) -> None:
    _use(FakeVerifier(webhook.VerifierUnavailable("down")))
    assert _post(client, _event(order_id="O", merchant="M")).status_code == 503


@pytest.mark.parametrize("body", ["not json", "[]", '{"id": "x"}'])
def test_a_malformed_body_is_refused_before_verification(
    client: TestClient, body: str
) -> None:
    verifier = FakeVerifier(True)
    _use(verifier)
    assert _post(client, body).status_code == 400
    assert verifier.calls == 0


def test_an_unhandled_event_type_is_acknowledged_without_a_job(
    client: TestClient, session: Session, routed: tuple[uuid.UUID, str]
) -> None:
    tenant_id, merchant = routed
    _use(FakeVerifier(True))
    response = _post(
        client,
        _event(order_id="O", merchant=merchant, event_type="CUSTOMER.DISPUTE.CREATED"),
    )
    assert response.json() == {"status": "ignored_type"}
    assert _jobs(session, tenant_id) == 0


def test_an_unknown_merchant_is_unrouted_and_stores_nothing(
    client: TestClient, session: Session
) -> None:
    _use(FakeVerifier(True))
    before = session.scalar(select(func.count()).select_from(Job)) or 0
    response = _post(client, _event(order_id="O", merchant="NOBODY"))
    assert response.json() == {"status": "unrouted"}
    assert (session.scalar(select(func.count()).select_from(Job)) or 0) == before


def test_a_merchant_shared_by_two_tenants_is_not_guessed(
    client: TestClient, session: Session
) -> None:
    merchant = f"M-{uuid.uuid4().hex[:10]}"
    for slug in ("a", "b"):
        tenant = Tenant(slug=f"dup-{slug}-{uuid.uuid4().hex[:6]}", name=slug)
        session.add(tenant)
        session.flush()
        session.add(
            Seller(
                tenant_id=tenant.id,
                external_ref=f"v-{uuid.uuid4().hex[:6]}",
                name="S",
                paypal_merchant_id=merchant,
                partner_fee_enabled=True,
                delay_funds_enabled=False,
            )
        )
    session.flush()
    _use(FakeVerifier(True))
    response = _post(client, _event(order_id="O", merchant=merchant))
    assert response.json() == {"status": "unrouted"}


def test_an_event_without_a_payee_is_unrouted(client: TestClient) -> None:
    _use(FakeVerifier(True))
    response = _post(client, _event(order_id="O", merchant=None))
    assert response.json() == {"status": "unrouted"}


def test_an_event_without_an_order_id_is_acknowledged_and_logged(
    client: TestClient,
) -> None:
    _use(FakeVerifier(True))
    event = _event(order_id="O", merchant="M")
    event["resource"]["supplementary_data"] = {}
    assert _post(client, event).json() == {"status": "no_order"}


# --- the verifier itself --------------------------------------------------


def _verifier(
    answer: dict | int, *, webhook_id: str | None = "WH-ID"
) -> webhook.Verifier:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t"})
        if isinstance(answer, int):
            return httpx.Response(answer, json={})
        return httpx.Response(200, json=answer)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return webhook.Verifier(CREDS, webhook_id, http=http)


def test_only_success_is_acceptance() -> None:
    event = _event(order_id="O", merchant="M")
    assert _verifier({"verification_status": "SUCCESS"}).verify(HEADERS, event)
    # PayPal answers 200 for a failure: the body decides, not the status.
    assert not _verifier({"verification_status": "FAILURE"}).verify(HEADERS, event)
    assert not _verifier({}).verify(HEADERS, event)


def test_no_webhook_id_means_nothing_is_accepted() -> None:
    event = _event(order_id="O", merchant="M")
    verifier = _verifier({"verification_status": "SUCCESS"}, webhook_id=None)
    assert not verifier.verify(HEADERS, event)


def test_missing_signature_headers_are_not_verifiable() -> None:
    event = _event(order_id="O", merchant="M")
    verifier = _verifier({"verification_status": "SUCCESS"})
    assert not verifier.verify({}, event)


def test_a_server_error_from_paypal_is_unavailable_not_refusal() -> None:
    event = _event(order_id="O", merchant="M")
    with pytest.raises(webhook.VerifierUnavailable):
        _verifier(503).verify(HEADERS, event)


# --- the handler job ------------------------------------------------------


class StubClient:
    """Stands in for `LiveClient`: returns a fixture order, or raises."""

    def __init__(self, order: dict | Exception) -> None:
        self.order = order
        self.reads = 0

    def fetch_order(self, order_id: str, merchants: list[str]) -> tuple[dict, str]:
        self.reads += 1
        if isinstance(self.order, Exception):
            raise self.order
        return self.order, merchants[0]


def _payload(tenant_merchant: str, order_id: str, event_id: str) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "order_id": order_id,
        "merchant_id": tenant_merchant,
    }


def test_the_job_refetches_and_writes_the_records(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"wh-{uuid.uuid4().hex[:8]}",
        tenant_name="Hook",
    )
    session.flush()
    order = json.loads((FIXTURES / "ORD-0001.json").read_text())["order"]
    capture_id = order["purchase_units"][0]["payments"]["captures"][0]["id"]
    monkeypatch.setattr(
        paypal_event_job, "make_client", lambda: StubClient(order)
    )

    paypal_event_job.execute(
        session,
        tenant_id=built.tenant_id,
        payload=_payload("M", order["id"], "WH-1"),
    )
    session.flush()

    assert session.scalars(
        select(Capture).where(
            Capture.tenant_id == built.tenant_id, Capture.external_id == capture_id
        )
    ).one()


def test_a_second_delivery_of_one_event_has_one_effect(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"wh-{uuid.uuid4().hex[:8]}",
        tenant_name="Hook",
    )
    session.flush()
    order = json.loads((FIXTURES / "ORD-0001.json").read_text())["order"]
    stub = StubClient(order)
    monkeypatch.setattr(paypal_event_job, "make_client", lambda: stub)
    payload = _payload("M", order["id"], "WH-DUP")

    paypal_event_job.execute(session, tenant_id=built.tenant_id, payload=payload)
    session.flush()
    with pytest.raises(paypal_event_job.AlreadyProcessed):
        paypal_event_job.execute(session, tenant_id=built.tenant_id, payload=payload)

    assert stub.reads == 1, "the duplicate is refused before any PayPal call"


@pytest.mark.parametrize(
    "failure", [live.LiveDenied("403"), live.LiveUnavailable("503")]
)
def test_a_refused_or_failed_refetch_fails_the_job_and_writes_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    """A `403` is a fact about our permissions, never an absent field."""
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"wh-{uuid.uuid4().hex[:8]}",
        tenant_name="Hook",
    )
    session.flush()
    before = session.scalar(
        select(func.count()).select_from(Capture).where(
            Capture.tenant_id == built.tenant_id
        )
    )
    monkeypatch.setattr(
        paypal_event_job, "make_client", lambda: StubClient(failure)
    )

    with pytest.raises(type(failure)):
        paypal_event_job.execute(
            session,
            tenant_id=built.tenant_id,
            payload=_payload("M", "ORD", "WH-FAIL"),
        )

    after = session.scalar(
        select(func.count()).select_from(Capture).where(
            Capture.tenant_id == built.tenant_id
        )
    )
    assert after == before
