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
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reckonai.models import Order, RawPayload
from reckonai.paypal import capture
from reckonai.sim import data, judge, seed

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
