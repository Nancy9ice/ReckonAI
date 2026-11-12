"""A finding a later run re-examined and stopped reporting is retracted.

A run writes a row per finding and nothing for an order that comes out clean, so
without this the newest row for an `(order, code)` stayed the newest for ever: a
fixed fault, or late evidence, showed as open on the queue and the on-hold list.
The rule is narrow on purpose (see `queries._drop_retracted`): only a *completed*
run that started *later* and whose *window contains the order* retracts.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai import demo_data
from reckonai.api import export
from reckonai.api.deps import get_session, get_tenant_id
from reckonai.app import app
from reckonai.jobs import reconcile_job, window
from reckonai.models import (
    ExceptionDecision,
    Order,
    ReconciliationException,
    ReconciliationRun,
    Refund,
    SellerLedgerEntry,
    Tenant,
)
from reckonai.sim import data

WIDE = window.Window(
    start=datetime(2026, 10, 12, tzinfo=UTC), end=datetime(2026, 11, 21, tzinfo=UTC)
)
#: Ends before ORD-0016 (Nov 17) and ORD-0017 (Nov 18) were placed.
EARLY = window.Window(
    start=datetime(2026, 10, 12, tzinfo=UTC), end=datetime(2026, 11, 10, tzinfo=UTC)
)


@pytest.fixture
def tenant_id(session: Session) -> uuid.UUID:
    demo_data.reset(session)
    session.flush()
    demo_data.ensure(session)
    session.flush()
    found = session.scalar(select(Tenant.id).where(Tenant.slug == data.TENANT_SLUG))
    assert found is not None
    reconcile_job.run_window(session, tenant_id=found, win=WIDE)
    session.flush()
    return found


@pytest.fixture
def http(session: Session, tenant_id: uuid.UUID) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_tenant_id] = lambda: tenant_id
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _run_again(session: Session, tenant_id: uuid.UUID, win: window.Window) -> None:
    reconcile_job.run_window(session, tenant_id=tenant_id, win=win)
    session.flush()


def _queue_refs(http: TestClient) -> set[str]:
    return {r["order_ref"] for r in http.get("/api/exceptions").json()}


def _parked(http: TestClient) -> set[str]:
    return {p["order_ref"] for p in http.get("/api/coverage").json()["parked_items"]}


def _clean(http: TestClient) -> set[str]:
    return {c["order_ref"] for c in http.get("/api/coverage").json()["clean_orders"]}


def _make_0017_clean(session: Session, tenant_id: uuid.UUID) -> None:
    """ORD-0017's refund was recorded and PayPal's had not arrived. Remove the
    marketplace's refund so a correct run has nothing to say about the order,
    which is the same shape as PayPal's record arriving and the pair matching."""
    order = session.scalars(
        select(Order).where(
            Order.tenant_id == tenant_id, Order.external_ref == "ORD-0017"
        )
    ).one()
    for entry in session.scalars(
        select(SellerLedgerEntry).where(
            SellerLedgerEntry.tenant_id == tenant_id,
            SellerLedgerEntry.order_id == order.id,
            SellerLedgerEntry.entry_category.in_(["refund", "commission_reversal"]),
        )
    ):
        session.delete(entry)
    for refund in session.scalars(
        select(Refund).where(
            Refund.tenant_id == tenant_id, Refund.external_id.like("RFND-0017%")
        )
    ):
        session.delete(refund)
    session.flush()


def _fix_0016(session: Session, tenant_id: uuid.UUID) -> None:
    """ORD-0016 is credited twice; remove the surplus entry."""
    entry = session.scalars(
        select(SellerLedgerEntry).where(
            SellerLedgerEntry.tenant_id == tenant_id,
            SellerLedgerEntry.external_ref.like("%ORD-0016%dup"),
        )
    ).one()
    session.delete(entry)
    session.flush()


# --- late evidence ----------------------------------------------------------


def test_a_waiting_finding_is_cleared_once_a_later_run_stops_reporting_it(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    assert "ORD-0017" in _parked(http)
    _make_0017_clean(session, tenant_id)
    _run_again(session, tenant_id, WIDE)

    assert "ORD-0017" not in _parked(http)
    assert "ORD-0017" in _clean(http), "an order a run looked at and found clean"
    cov = http.get("/api/coverage").json()
    assert (
        cov["with_exceptions"] + cov["parked"] + cov["clean"] + cov["not_examined"]
        == cov["orders_total"]
    ), "every order is still accounted for exactly once"


# --- a fixed fault ----------------------------------------------------------


def test_a_fixed_exception_leaves_the_queue_and_the_summary(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    before = http.get("/api/summary").json()["open"]["count"]
    assert "ORD-0016" in _queue_refs(http)
    _fix_0016(session, tenant_id)
    _run_again(session, tenant_id, WIDE)

    assert "ORD-0016" not in _queue_refs(http)
    assert http.get("/api/summary").json()["open"]["count"] == before - 1
    assert "ORD-0016" in _clean(http)


def test_a_decision_is_kept_in_the_log_after_the_finding_is_retracted(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    row = session.scalars(
        select(ReconciliationException).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.order_external_ref == "ORD-0016",
            ReconciliationException.is_exception.is_(True),
        )
    ).first()
    assert row is not None
    session.add(
        ExceptionDecision(
            tenant_id=tenant_id, exception_id=row.id, outcome="approved", approver="A"
        )
    )
    session.flush()
    _fix_0016(session, tenant_id)
    _run_again(session, tenant_id, WIDE)

    assert "ORD-0016" not in _queue_refs(http)
    log = list(csv.reader(io.StringIO(export.decisions_csv(session, tenant_id))))
    assert any("ORD-0016" in line for line in log[1:]), "the audit trail is unchanged"


# --- what must NOT be retracted --------------------------------------------


def test_a_run_whose_window_does_not_contain_the_order_retracts_nothing(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    _fix_0016(session, tenant_id)
    _make_0017_clean(session, tenant_id)
    _run_again(session, tenant_id, EARLY)  # ends before both orders were placed

    assert "ORD-0016" in _queue_refs(http)
    assert "ORD-0017" in _parked(http)


def test_a_run_that_did_not_complete_retracts_nothing(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    session.add(
        ReconciliationRun(
            tenant_id=tenant_id,
            window_start=WIDE.start,
            window_end=WIDE.end,
            status="failed",
            started_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    )
    session.flush()
    assert "ORD-0016" in _queue_refs(http)
    assert "ORD-0017" in _parked(http)


def test_a_fault_that_is_still_there_is_not_retracted(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    before = _queue_refs(http)
    parked = _parked(http)
    _run_again(session, tenant_id, WIDE)
    assert _queue_refs(http) == before
    assert _parked(http) == parked


def test_a_specific_run_still_shows_what_it_found(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    """Past checks: opening an older run shows that run as it left it."""
    first = session.scalars(
        select(ReconciliationRun)
        .where(ReconciliationRun.tenant_id == tenant_id)
        .order_by(ReconciliationRun.started_at)
    ).first()
    assert first is not None
    _fix_0016(session, tenant_id)
    _run_again(session, tenant_id, WIDE)

    listing = http.get(f"/api/exceptions?run_id={first.id}").json()
    old = {r["order_ref"] for r in listing}
    assert "ORD-0016" in old
    assert "ORD-0016" not in _queue_refs(http)


# --- a run only retracts what it could have seen ----------------------------


def _first_run(session: Session, tenant_id: uuid.UUID) -> ReconciliationRun:
    run = session.scalars(
        select(ReconciliationRun)
        .where(ReconciliationRun.tenant_id == tenant_id)
        .order_by(ReconciliationRun.started_at)
    ).first()
    assert run is not None
    run.finished_at = run.started_at + timedelta(minutes=2)
    session.flush()
    return run


def _completed_run_after(
    session: Session, tenant_id: uuid.UUID, started: datetime
) -> None:
    session.add(
        ReconciliationRun(
            tenant_id=tenant_id,
            window_start=WIDE.start,
            window_end=WIDE.end,
            status="completed",
            started_at=started,
            finished_at=started + timedelta(minutes=1),
        )
    )
    session.flush()


def test_a_run_that_started_before_the_first_finished_retracts_nothing(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    """It never saw the first run's data, so its silence proves nothing."""
    first = _first_run(session, tenant_id)
    _completed_run_after(session, tenant_id, first.started_at + timedelta(minutes=1))
    assert "ORD-0016" in _queue_refs(http)
    assert "ORD-0017" in _parked(http)


def test_a_run_that_started_after_the_first_finished_does_retract(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    """The control for the test above: same silent run, started later."""
    first = _first_run(session, tenant_id)
    _completed_run_after(session, tenant_id, first.started_at + timedelta(minutes=5))
    assert "ORD-0016" not in _queue_refs(http)
    assert "ORD-0017" not in _parked(http)


# --- the nightly sweep and the queue agree ----------------------------------


def test_the_investigation_sweep_does_not_see_a_retracted_finding(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    from reckonai.jobs import investigation_job

    def swept() -> set[str]:
        rows = investigation_job.open_exceptions(session, tenant_id)
        return {r.order_external_ref for r in rows}

    assert "ORD-0016" in swept() and "ORD-0016" in _queue_refs(http)
    _fix_0016(session, tenant_id)
    _run_again(session, tenant_id, WIDE)

    assert "ORD-0016" not in swept(), "no paid investigation of a finding nobody sees"
    assert swept() == _queue_refs(http), "one definition of open"


# --- the assumption the rule rests on ---------------------------------------


def test_a_run_examines_every_order_placed_in_its_window(
    http: TestClient, session: Session, tenant_id: uuid.UUID
) -> None:
    """Retraction assumes this. If the selection is ever narrowed (a cap, a status
    filter), a completed run would clear findings for orders it never looked at,
    so this must fail first."""
    mid = window.Window(
        start=datetime(2026, 11, 12, tzinfo=UTC), end=datetime(2026, 11, 16, tzinfo=UTC)
    )
    _run_again(session, tenant_id, mid)
    run = session.scalars(
        select(ReconciliationRun)
        .where(ReconciliationRun.tenant_id == tenant_id)
        .order_by(ReconciliationRun.started_at.desc())
    ).first()
    assert run is not None

    inside = window.orders_in_window(session, tenant_id, mid)
    assert {"ORD-0013", "ORD-0014"} <= inside, "the seeded faults inside the window"
    reported = {
        r["order_ref"] for r in http.get(f"/api/exceptions?run_id={run.id}").json()
    }
    assert {"ORD-0013", "ORD-0014"} <= reported, "found by this run, not an older one"
    assert "ORD-0016" not in reported, "outside the window, so this run never looked"
