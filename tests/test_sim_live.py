"""The simulated marketplace's write side.

`specs/2026-11-11-live-ingestion/requirements.md` F10 to F15. The end-to-end
tests reconcile what the write side produced: a clean live order is clean, and
each planted fault is reported as the code the seed's own lever produces.
"""

from __future__ import annotations

import copy
import json
import pathlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai import reconcile
from reckonai.app import app
from reckonai.config import get_settings
from reckonai.models import Order, RawPayload, Refund, SellerLedgerEntry
from reckonai.paypal import capture
from reckonai.paypal import live as paypal_live
from reckonai.sim import data, live, seed

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "paypal"
SELLER = data.B  # ORD-0013's payee
REF = "ORD-0013"


def _fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / f"{REF}.json").read_text(encoding="utf-8"))


class FakeGateway:
    """Stands in for the sandbox, returning ORD-0013's real captured responses."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.created = 0
        self.orders: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []

    def create_order(self, **kwargs: Any) -> capture.LiveOrder:
        if self.fail:
            raise capture.CaptureUnavailable("sandbox down")
        self.created += 1
        self.orders.append(kwargs)
        fixture = _fixture()
        before = copy.deepcopy(fixture["order"])
        for unit in before["purchase_units"]:
            unit["payments"].pop("refunds", None)
        document = {**fixture, "order": before}
        cap = before["purchase_units"][0]["payments"]["captures"][0]["id"]
        return capture.LiveOrder(
            order_id=before["id"], captures=(cap,), document=document
        )

    def refund(self, **kwargs: Any) -> dict[str, Any]:
        self.refunds.append(kwargs)
        return {"id": "RF-FAKE"}

    def read_order(self, order_id: str, merchant_id: str) -> dict[str, Any]:
        return _fixture()["order"]


@pytest.fixture
def tenant_id(session: Session) -> uuid.UUID:
    built = seed.build(
        session,
        data.Dataset(orders=()),
        tenant_slug=f"lw-{uuid.uuid4().hex[:8]}",
        tenant_name="Write side",
    )
    session.flush()
    return built.tenant_id


def _place(
    session: Session, tenant_id: uuid.UUID, gateway: FakeGateway, **overrides: Any
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "order_ref": REF,
        "seller_ref": SELLER,
        "gross": "110.00",
        "fee": "11.00",
        "ingest_now": True,
    }
    fields.update(overrides)
    return live.place_order(session, tenant_id, live.PlaceOrderIn(**fields), gateway)


def _codes(session: Session, tenant_id: uuid.UUID) -> set[str]:
    return {str(f.code) for f in reconcile.exceptions(session, tenant_id)}


# --- what it writes --------------------------------------------------------


def test_everything_it_writes_is_labelled_simulated(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _place(session, tenant_id, FakeGateway(), ingest_now=False)
    session.flush()

    payloads = session.scalars(
        select(RawPayload).where(RawPayload.tenant_id == tenant_id)
    ).all()
    assert payloads
    assert {p.source for p in payloads} == {"marketplace"}, "never provider evidence"
    assert {p.endpoint for p in payloads} == {live.SIM_ENDPOINT}


def test_the_seed_is_not_labelled(session: Session) -> None:
    """The label is a property of the write side, not of marketplace rows."""
    built = seed.build(
        session,
        data.CLEAN,
        tenant_slug=f"sd-{uuid.uuid4().hex[:8]}",
        tenant_name="Seeded",
    )
    session.flush()
    endpoints = set(
        session.scalars(
            select(RawPayload.endpoint).where(
                RawPayload.tenant_id == built.tenant_id,
                RawPayload.source == "marketplace",
            )
        )
    )
    assert endpoints == {None}


def test_a_paypal_failure_writes_nothing(
    session: Session, tenant_id: uuid.UUID
) -> None:
    with pytest.raises(capture.CaptureUnavailable):
        _place(session, tenant_id, FakeGateway(fail=True))
    assert not session.scalars(
        select(Order.id).where(Order.tenant_id == tenant_id)
    ).first()


@pytest.mark.parametrize(
    "overrides",
    [
        {"seller_ref": "nobody"},
        {"fee": "110.00"},
        {"gross": "abc"},
        {"gross": "-5"},
    ],
)
def test_a_request_it_cannot_honour_is_refused_before_paypal(
    session: Session, tenant_id: uuid.UUID, overrides: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    with pytest.raises(live.WriteError):
        _place(session, tenant_id, gateway, **overrides)
    assert gateway.created == 0


def test_an_order_ref_cannot_be_reused(session: Session, tenant_id: uuid.UUID) -> None:
    gateway = FakeGateway()
    _place(session, tenant_id, gateway, ingest_now=False)
    session.flush()
    with pytest.raises(live.WriteError, match="already exists"):
        _place(session, tenant_id, gateway, ingest_now=False)
    assert gateway.created == 1


# --- reconciling what it wrote ---------------------------------------------


def test_a_clean_live_order_produces_no_exception(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _place(session, tenant_id, FakeGateway())
    session.flush()
    assert _codes(session, tenant_id) == set()


def test_a_planted_duplicate_credit_is_found(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _place(session, tenant_id, FakeGateway(), duplicate_credit=True)
    session.flush()
    assert _codes(session, tenant_id) == {"duplicate_ledger_entry"}


def test_a_planted_commission_error_is_found(
    session: Session, tenant_id: uuid.UUID
) -> None:
    _place(session, tenant_id, FakeGateway(), ledger_commission="21.00")
    session.flush()
    assert _codes(session, tenant_id) == {"commission_mismatch"}


def test_a_reflected_refund_is_clean(session: Session, tenant_id: uuid.UUID) -> None:
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()

    live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF,
            paypal_capture_id=placed["paypal_capture_ids"][0],
            amount="30.00",
            ingest_now=True,
        ),
        gateway,
    )
    session.flush()

    assert gateway.refunds[0]["amount"] == "30.00"
    assert _codes(session, tenant_id) == set()


def test_a_refund_the_ledger_never_heard_of_is_found(
    session: Session, tenant_id: uuid.UUID
) -> None:
    """F1, planted on a live transaction."""
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()

    live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF,
            paypal_capture_id=placed["paypal_capture_ids"][0],
            amount="30.00",
            reflect_in_ledger=False,
            ingest_now=True,
        ),
        gateway,
    )
    session.flush()

    assert _codes(session, tenant_id) == {"missing_ledger_adjustment"}


def test_a_marketplace_only_refund_is_awaiting_evidence_not_an_exception(
    session: Session, tenant_id: uuid.UUID
) -> None:
    """F5 on a live transaction: PayPal's refund is late, not absent."""
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()

    out = live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF,
            paypal_capture_id=placed["paypal_capture_ids"][0],
            amount="30.00",
            marketplace_only=True,
        ),
        gateway,
    )
    session.flush()

    assert gateway.refunds == [], "no PayPal refund is made"
    assert out["paypal_refund_id"] is None and out["marketplace_only"] is True
    assert _codes(session, tenant_id) == set()
    assert "awaiting_evidence" in {
        str(f.code) for f in reconcile.run(session, tenant_id)
    }


@pytest.mark.parametrize(
    "overrides", [{"ingest_now": True}, {"reflect_in_ledger": False}]
)
def test_marketplace_only_refuses_what_it_cannot_honour(
    session: Session, tenant_id: uuid.UUID, overrides: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()
    with pytest.raises(live.WriteError, match="marketplace_only"):
        live.refund_order(
            session,
            tenant_id,
            live.RefundIn(
                order_ref=REF,
                paypal_capture_id=placed["paypal_capture_ids"][0],
                amount="30.00",
                marketplace_only=True,
                **overrides,
            ),
            gateway,
        )
    assert gateway.refunds == []


def test_a_late_paypal_refund_settles_what_the_marketplace_recorded(
    session: Session, tenant_id: uuid.UUID
) -> None:
    """The late-evidence story: book first, deliver PayPal's side afterwards."""
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()
    capture_id = placed["paypal_capture_ids"][0]
    live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF, paypal_capture_id=capture_id, amount="30.00",
            marketplace_only=True,
        ),
        gateway,
    )
    session.flush()

    def booked() -> int | None:
        return session.scalar(
            select(func.count()).select_from(Refund).where(
                Refund.tenant_id == tenant_id, Refund.external_id.like("RF-%")
            )
        )

    assert booked() == 1

    out = live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF, paypal_capture_id=capture_id, amount="30.00",
            paypal_only=True, ingest_now=True,
        ),
        gateway,
    )
    session.flush()

    assert gateway.refunds[0]["amount"] == "30.00"
    assert out["marketplace_refund"] is None
    assert booked() == 1, "no second marketplace refund is booked"
    assert _codes(session, tenant_id) == set()


def test_a_delayed_order_is_requested_and_recorded_as_delayed(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gateway = FakeGateway()
    _place(session, tenant_id, gateway, disbursement_mode="DELAYED")
    session.flush()
    assert gateway.orders[0]["disbursement_mode"] == "DELAYED"
    order = session.scalars(select(Order).where(Order.tenant_id == tenant_id)).one()
    assert order.disbursement_mode == "DELAYED"


def test_an_unknown_disbursement_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        live.PlaceOrderIn(
            order_ref="X", seller_ref=SELLER, gross="10.00", fee="1.00",
            disbursement_mode="SOON",  # type: ignore[arg-type]
        )
    with pytest.raises(capture.CaptureUnavailable, match="disbursement mode"):
        capture.capture_live_order(
            payees=[("M", "10.00", "1.00")], disbursement_mode="SOON"
        )


def test_a_refund_cannot_exceed_the_order(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()
    with pytest.raises(live.WriteError):
        live.refund_order(
            session,
            tenant_id,
            live.RefundIn(
                order_ref=REF,
                paypal_capture_id=placed["paypal_capture_ids"][0],
                amount="999.00",
            ),
            gateway,
        )
    assert gateway.refunds == [], "PayPal is not called for a refund it would refuse"


def test_an_adhoc_ledger_entry_is_written_and_labelled(
    session: Session, tenant_id: uuid.UUID
) -> None:
    out = live.record_ledger_entry(
        session,
        tenant_id,
        live.LedgerEntryIn(
            seller_ref=SELLER, category="adjustment", direction="credit", amount="5.00"
        ),
    )
    session.flush()
    assert out["simulated"] is True
    assert (
        session.scalar(
            select(func.count()).select_from(SellerLedgerEntry).where(
                SellerLedgerEntry.tenant_id == tenant_id
            )
        )
        == 1
    )


# --- the guard (F15) -------------------------------------------------------


@pytest.fixture
def http(
    session: Session, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> Iterator[TestClient]:
    seed.build(
        session,
        data.Dataset(orders=()),
        tenant_slug=data.TENANT_SLUG,
        tenant_name="Write side",
    )
    session.flush()
    app.dependency_overrides[live._session] = lambda: session
    app.dependency_overrides[live.get_gateway] = lambda: FakeGateway()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, enabled: bool, secret: str | None
) -> None:
    monkeypatch.setenv("SIM_WRITE_ENABLED", "true" if enabled else "false")
    if secret is None:
        monkeypatch.delenv("SIM_WRITE_SECRET", raising=False)
    else:
        monkeypatch.setenv("SIM_WRITE_SECRET", secret)
    get_settings.cache_clear()


BODY = {"order_ref": REF, "seller_ref": SELLER, "gross": "110.00", "fee": "11.00"}


def test_the_write_side_is_hidden_unless_enabled(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, enabled=False, secret="s")
    response = http.post(
        "/sim/marketplace/live/orders", json=BODY, headers={"X-Sim-Secret": "s"}
    )
    assert response.status_code == 404


def test_enabled_with_no_secret_is_refused_not_open(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, enabled=True, secret=None)
    response = http.post("/sim/marketplace/live/orders", json=BODY)
    assert response.status_code == 503


@pytest.mark.parametrize("header", [None, "wrong"])
def test_a_missing_or_wrong_secret_is_forbidden(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, header: str | None
) -> None:
    _configure(monkeypatch, enabled=True, secret="right")
    headers = {"X-Sim-Secret": header} if header else {}
    response = http.post("/sim/marketplace/live/orders", json=BODY, headers=headers)
    assert response.status_code == 403


def test_the_right_secret_places_an_order(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, enabled=True, secret="right")
    response = http.post(
        "/sim/marketplace/live/orders", json=BODY, headers={"X-Sim-Secret": "right"}
    )
    assert response.status_code == 201
    assert response.json()["simulated"] is True


def test_a_refused_request_is_unprocessable_not_a_server_error(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, enabled=True, secret="right")
    response = http.post(
        "/sim/marketplace/live/orders",
        json={**BODY, "seller_ref": "nobody"},
        headers={"X-Sim-Secret": "right"},
    )
    assert response.status_code == 422


# --- review findings ---------------------------------------------------------


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_a_non_finite_amount_is_refused_not_a_server_error(
    session: Session, tenant_id: uuid.UUID, amount: str
) -> None:
    with pytest.raises(live.WriteError):
        _place(session, tenant_id, FakeGateway(), gross=amount, ingest_now=False)


def test_a_refund_that_cannot_be_read_back_is_not_made_at_paypal(
    session: Session, tenant_id: uuid.UUID
) -> None:
    """The capture was never ingested, so the read-back would fail. That is
    discovered before PayPal is touched, not after a refund has been made."""
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway, ingest_now=False)
    session.flush()

    with pytest.raises(live.WriteError, match="has not been ingested"):
        live.refund_order(
            session,
            tenant_id,
            live.RefundIn(
                order_ref=REF,
                paypal_capture_id=placed["paypal_capture_ids"][0],
                amount="30.00",
                ingest_now=True,
            ),
            gateway,
        )

    assert gateway.refunds == []


def test_a_failed_read_back_after_the_refund_keeps_the_booking(
    session: Session, tenant_id: uuid.UUID
) -> None:
    gateway = FakeGateway()
    placed = _place(session, tenant_id, gateway)
    session.flush()

    def refused(order_id: str, merchant_id: str) -> dict[str, Any]:
        raise paypal_live.LiveDenied("403")

    gateway.read_order = refused  # type: ignore[method-assign]

    out = live.refund_order(
        session,
        tenant_id,
        live.RefundIn(
            order_ref=REF,
            paypal_capture_id=placed["paypal_capture_ids"][0],
            amount="30.00",
            ingest_now=True,
        ),
        gateway,
    )
    session.flush()

    assert gateway.refunds, "the refund was made at PayPal"
    assert out["ingested"] is False and "403" in out["ingest_error"]
    from reckonai.models import Refund

    booked = session.scalars(
        select(Refund).where(
            Refund.tenant_id == tenant_id,
            Refund.external_id == out["marketplace_refund"],
        )
    ).first()
    assert booked is not None, "and it is booked, so a retry would not refund twice"
