"""The persisted run: the frozen contract as rows, and the dedupe guarantee.

`specs/2026-11-09-queue-scheduled-run/validation.md` sections 2 and 3.

These tests use the rolled-back `session` fixture. Nothing here needs two
transactions: the dedupe property is a constraint, which fires inside one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.jobs import reconcile_job, window
from reckonai.models import (
    ReconciliationException,
    ReconciliationRun,
    Seller,
    Tenant,
)
from reckonai.money import Money
from reckonai.recon.classifier import Finding
from reckonai.recon.vocabulary import EvidenceBasis, ExceptionCode

WINDOW = window.Window(
    start=datetime(2026, 11, 1, tzinfo=UTC),
    end=datetime(2026, 11, 11, tzinfo=UTC),
)


# ---------------------------------------------------------------------------
# Section 2 - the exception table is the frozen contract
# ---------------------------------------------------------------------------


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "code": ExceptionCode.COMMISSION_MISMATCH,
        "order_ref": "ORD-0014",
        "detail": "The marketplace retained 6.00 where PayPal recorded 16.00.",
        "impact": Money(Decimal("10.00"), "USD"),
        "evidence_refs": ("ORD-0014", "ITEM-0014-A"),
        "evidence_basis": EvidenceBasis.PROVIDER_REPORTED,
        "expected": Money(Decimal("16.00"), "USD"),
        "reported": Money(Decimal("6.00"), "USD"),
        "confirmed": True,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_a_finding_round_trips_without_losing_a_field(
    session: Session, tenant: Tenant, seller: Seller
) -> None:
    """validation.md section 2. The table and the frozen contract cannot drift.

    Asserted field by field against `as_dict()` rather than by spot-checking
    the interesting ones: the contract's whole point is that Block F renders
    every key, so a column silently dropped on the way in is a value missing
    from the dashboard.
    """
    run_row = ReconciliationRun(
        tenant_id=tenant.id,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
        started_at=datetime.now(UTC),
        status="running",
    )
    session.add(run_row)
    session.flush()

    finding = _finding(seller_id=seller.id)
    session.add(
        reconcile_job._row_for(finding, tenant_id=tenant.id, run_id=run_row.id)
    )
    session.flush()
    session.expire_all()

    row = session.scalars(
        select(ReconciliationException).where(
            ReconciliationException.run_id == run_row.id
        )
    ).one()

    contract = finding.as_dict()

    assert row.code == contract["code"]
    assert row.order_external_ref == contract["order_ref"]
    assert row.detail == contract["detail"]
    assert row.is_exception == contract["is_exception"]
    assert row.evidence_basis == contract["evidence_basis"]
    assert row.evidence_refs == contract["evidence_refs"]
    assert str(row.seller_id) == contract["seller_id"]
    assert row.confirmed == contract["confirmed"]

    # Amounts cross the contract as strings with their currency, and must come
    # back as the same figures - not as floats, and not having lost a currency.
    assert {
        "amount": str(row.impact.amount.quantize(Decimal("0.01"))),
        "currency": row.impact.currency,
    } == contract["impact"]
    assert row.expected is not None and row.reported is not None
    assert row.expected.currency == "USD"
    assert row.expected.amount == Decimal("16.00")
    assert row.reported.amount == Decimal("6.00")


def test_a_non_comparison_finding_stores_neither_figure(
    session: Session, tenant: Tenant
) -> None:
    """`awaiting_evidence` compares nothing, so both columns stay NULL.

    The `money_pair_check` constraints are what refuse a half-written pair -
    an amount with no currency is the mistake specs/tech-stack.md section 9.3
    calls a hard failure.
    """
    run_row = ReconciliationRun(
        tenant_id=tenant.id,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
        started_at=datetime.now(UTC),
        status="running",
    )
    session.add(run_row)
    session.flush()

    finding = _finding(
        code=ExceptionCode.AWAITING_EVIDENCE,
        impact=Money.zero("USD"),
        expected=None,
        reported=None,
        confirmed=False,
    )
    session.add(
        reconcile_job._row_for(finding, tenant_id=tenant.id, run_id=run_row.id)
    )
    session.flush()
    session.expire_all()

    row = session.scalars(select(ReconciliationException)).one()

    assert row.expected is None
    assert row.reported is None
    assert row.impact.amount == Decimal("0.00")
    assert row.is_exception is False, (
        "awaiting_evidence is a finding and not an exception; storing it as "
        "one would fail the false-positive gate while being correct"
    )


def test_an_exception_row_cannot_name_another_tenants_run(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    """The composite foreign key, which is why `run_id` is not a plain FK."""
    run_row = ReconciliationRun(
        tenant_id=other_tenant.id,
        window_start=WINDOW.start,
        window_end=WINDOW.end,
        started_at=datetime.now(UTC),
        status="running",
    )
    session.add(run_row)
    session.flush()

    session.add(
        reconcile_job._row_for(
            _finding(), tenant_id=tenant.id, run_id=run_row.id
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_run_cannot_claim_more_exceptions_than_findings(
    session: Session, tenant: Tenant
) -> None:
    """The constraint that would catch the two counts being filled in the
    wrong order - the exact trap Block C's handoff names."""
    session.add(
        ReconciliationRun(
            tenant_id=tenant.id,
            window_start=WINDOW.start,
            window_end=WINDOW.end,
            started_at=datetime.now(UTC),
            status="completed",
            finding_count=2,
            exception_count=5,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_a_run_window_must_be_ordered(session: Session, tenant: Tenant) -> None:
    """A window ending before it starts selects nothing and looks like a
    clean run, which is the worst way for a date mistake to present."""
    session.add(
        ReconciliationRun(
            tenant_id=tenant.id,
            window_start=WINDOW.end,
            window_end=WINDOW.start,
            started_at=datetime.now(UTC),
            status="running",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# Section 1 criterion 3 - one window is reconciled once
# ---------------------------------------------------------------------------


def test_the_same_window_cannot_be_reconciled_twice(
    session: Session, tenant: Tenant
) -> None:
    """validation.md criterion 3, through the path the MVP actually has.

    The key is composed from the tenant and the window dates (D5), so this is
    the duplicate-delivery property proven on real work rather than on a
    synthetic event id.
    """
    # Stamped, as the enqueuers do: both jobs were queued against the same state
    # of the evidence, so they share a key even though the first completing moves
    # the watermark. An unstamped payload would read the watermark at execution.
    payload = reconcile_job.payload_for(WINDOW)

    first = reconcile_job.execute(session, tenant_id=tenant.id, payload=payload)
    assert first.status == "completed"

    # `AlreadyReconciled` wraps the constraint violation rather than replacing
    # it: the database is still what refuses the duplicate (`__cause__` is the
    # `IntegrityError`), and the wrapper is what lets `handlers.dispatch` tell
    # "already done" from "failed and should retry".
    with pytest.raises(reconcile_job.AlreadyReconciled) as raised:
        reconcile_job.execute(session, tenant_id=tenant.id, payload=payload)

    assert isinstance(raised.value.__cause__, IntegrityError)


def test_the_key_ignores_the_time_of_day(tenant: Tenant) -> None:
    """Two runs over the same dates are the same piece of work.

    Without this the hourly cron job would compose a new key every tick and
    the dedupe marker would never match, which is a dedupe that silently does
    nothing.
    """
    morning = window.Window(
        start=datetime(2026, 11, 1, 6, tzinfo=UTC),
        end=datetime(2026, 11, 11, 6, tzinfo=UTC),
    )
    evening = window.Window(
        start=datetime(2026, 11, 1, 21, tzinfo=UTC),
        end=datetime(2026, 11, 11, 21, tzinfo=UTC),
    )

    assert reconcile_job.idempotency_key(
        tenant.id, morning
    ) == reconcile_job.idempotency_key(tenant.id, evening)


def test_different_windows_are_different_work(tenant: Tenant) -> None:
    other = window.Window(
        start=WINDOW.start + timedelta(days=1), end=WINDOW.end
    )
    assert reconcile_job.idempotency_key(
        tenant.id, WINDOW
    ) != reconcile_job.idempotency_key(tenant.id, other)


def test_an_empty_window_is_a_successful_run(
    session: Session, tenant: Tenant
) -> None:
    """validation.md section 3: an empty window is not an error.

    A tenant with no orders in the window has nothing wrong with it, and a
    run that failed instead would make an absence look like a fault.
    """
    empty = window.Window(
        start=datetime(2019, 1, 1, tzinfo=UTC),
        end=datetime(2019, 1, 8, tzinfo=UTC),
    )

    run_row = reconcile_job.run_window(
        session, tenant_id=tenant.id, win=empty
    )

    assert run_row.status == "completed"
    assert run_row.order_count == 0
    assert run_row.finding_count == 0
    assert run_row.exception_count == 0
    assert run_row.finished_at is not None


def test_a_run_records_the_window_it_covered(
    session: Session, tenant: Tenant
) -> None:
    """D1: a reader must not have to infer the window from what was found."""
    run_row = reconcile_job.run_window(session, tenant_id=tenant.id, win=WINDOW)

    assert run_row.window_start == WINDOW.start
    assert run_row.window_end == WINDOW.end
    assert run_row.evidence_since is None, (
        "there is no completed earlier run, so clause 2 has no watermark"
    )


def test_the_watermark_is_the_last_completed_runs_start(
    session: Session, tenant: Tenant
) -> None:
    """D3, as corrected by code review: `started_at`, not `finished_at`.

    Evidence that arrives *while* a run executes is invisible to that run,
    because the payload times are read near its beginning. Taking the
    watermark from `finished_at` would then also put that evidence behind the
    next run's watermark, so it would never be read at all. `started_at`
    makes that window empty, at the cost of re-covering ground.

    A failed run must not move the watermark either, since it cannot be
    trusted to have covered its window.
    """
    started = datetime(2026, 11, 9, 11, 55, tzinfo=UTC)
    finished = datetime(2026, 11, 9, 12, tzinfo=UTC)

    session.add_all(
        [
            ReconciliationRun(
                tenant_id=tenant.id,
                window_start=WINDOW.start,
                window_end=WINDOW.end,
                started_at=started,
                finished_at=finished,
                status="completed",
            ),
            ReconciliationRun(
                tenant_id=tenant.id,
                window_start=WINDOW.start,
                window_end=WINDOW.end,
                started_at=finished + timedelta(hours=1),
                finished_at=finished + timedelta(hours=1, minutes=1),
                status="failed",
            ),
        ]
    )
    session.flush()

    assert window.last_completed_watermark(session, tenant.id) == started


def test_the_watermark_is_per_tenant(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    """One marketplace's run must not advance another's watermark, or the
    second tenant skips evidence it never saw."""
    session.add(
        ReconciliationRun(
            tenant_id=other_tenant.id,
            window_start=WINDOW.start,
            window_end=WINDOW.end,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            status="completed",
        )
    )
    session.flush()

    assert window.last_completed_watermark(session, tenant.id) is None


def test_two_tenants_may_reconcile_the_same_window(
    session: Session, tenant: Tenant, other_tenant: Tenant
) -> None:
    """The dedupe marker is tenant-scoped, and this is why it has to be."""
    payload = {
        "window_start": WINDOW.start.isoformat(),
        "window_end": WINDOW.end.isoformat(),
    }

    reconcile_job.execute(session, tenant_id=tenant.id, payload=payload)
    reconcile_job.execute(session, tenant_id=other_tenant.id, payload=payload)

    runs = session.scalars(select(ReconciliationRun)).all()
    assert {run.tenant_id for run in runs} == {tenant.id, other_tenant.id}


def test_a_run_is_appended_rather_than_replacing_an_earlier_one(
    session: Session, tenant: Tenant
) -> None:
    """D1's append-only property, over the two runs R10 is about.

    An order parked as `awaiting_evidence` and later cleared leaves two rows
    that differ, and that difference is the only evidence the re-evaluation
    happened. A current-state table would overwrite exactly the row that
    proves the mechanism works.
    """
    first = reconcile_job.run_window(session, tenant_id=tenant.id, win=WINDOW)
    second = reconcile_job.run_window(
        session,
        tenant_id=tenant.id,
        win=window.Window(
            start=WINDOW.start, end=WINDOW.end, evidence_since=datetime.now(UTC)
        ),
    )

    runs = session.scalars(
        select(ReconciliationRun).where(ReconciliationRun.tenant_id == tenant.id)
    ).all()

    assert len(runs) == 2
    assert {run.id for run in runs} == {first.id, second.id}
    assert first.status == "completed", "the earlier run is not rewritten"


def test_a_job_payload_carries_no_monetary_value() -> None:
    """D5 and specs/tech-stack.md section 2, as a property of the parser.

    A `Decimal` does not survive a JSON round trip as itself, so a money
    figure in a job payload would come back as a float or a string nobody
    checked. The window parser reads dates and nothing else, and this asserts
    it stays that way.
    """
    parsed = reconcile_job._window_from(
        {
            "window_start": "2026-11-01T00:00:00+00:00",
            "window_end": "2026-11-11T00:00:00+00:00",
            "amount": "999.99",
        }
    )

    assert parsed.start == datetime(2026, 11, 1, tzinfo=UTC)
    assert parsed.end == datetime(2026, 11, 11, tzinfo=UTC)
    assert not hasattr(parsed, "amount")
