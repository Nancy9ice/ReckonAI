"""The handler: reconcile a window, and persist what it concluded.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D1, D2, D5.

**One transaction covers the dedupe marker, the run row and every exception
row.** That is the reason specs/tech-stack.md section 4 puts the queue in
Postgres at all: split the marker and the financial write across two stores,
and a crash between them leaves a marker claiming work was done whose output
was rolled back. Here a crash rolls back all of it, and the next run redoes
the window.

**Nothing is recomputed on read** (D2). The run executes the engine once and
stores the verdicts; Block F renders the stored rows. A dashboard that
re-derived a verdict from stored inputs would be a second code path reaching a
financial conclusion, which specs/mission.md section 4 forbids.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai import reconcile
from reckonai.jobs import investigation_job, queue, window
from reckonai.models import ReconciliationException, ReconciliationRun
from reckonai.recon.classifier import Finding, exceptions_only

JOB_TYPE = "reconciliation_run"


class AlreadyReconciled(Exception):
    """This tenant's window has already been reconciled.

    Raised instead of letting the `processed_event` `IntegrityError` escape as
    an ordinary failure. Code review found that it was being treated as one:
    `queue.fail` rescheduled it, retried to the cap, and left a terminal
    `failed` row carrying an `IntegrityError` - the "queue full of expected
    failures hides a real one" outcome the pre-check in `schedule.py` exists
    to avoid, arrived at by a different route.

    A window already done is a job that needs no further work, so
    `handlers.dispatch` completes it rather than retrying it. The constraint is
    still what decides; this only changes how the answer is read.
    """


def idempotency_key(tenant_id: uuid.UUID, win: window.Window) -> str:
    """The key that makes one window reconcilable once.

    Composed from the tenant and the window bounds, which is what a provider
    event id would be if there were one (D5). Dates rather than full
    timestamps: a window is a date range, and two runs over the same dates
    are the same piece of work however their bounds were spelled.
    """
    return (
        f"recon:{tenant_id}:{win.start.date().isoformat()}"
        f":{win.end.date().isoformat()}"
    )


def run_window(
    session: Session, *, tenant_id: uuid.UUID, win: window.Window
) -> ReconciliationRun:
    """Reconcile one window and persist the run and its findings.

    Flushes rather than commits: the caller owns the transaction, because the
    dedupe marker has to land in the same one.
    """
    started = datetime.now(UTC)

    run_row = ReconciliationRun(
        tenant_id=tenant_id,
        window_start=win.start,
        window_end=win.end,
        evidence_since=win.evidence_since,
        started_at=started,
        status="running",
    )
    session.add(run_row)
    session.flush()

    # Selection needs the matcher's own pairing of payments to orders, so the
    # cases are assembled here and the clauses applied over them rather than
    # over the tables - see `window.py`'s module docstring.
    cases, _marketplace_only, provider_only = reconcile.load_order_cases(
        session, tenant_id
    )
    selection = window.select_cases(
        cases,
        provider_only,
        window=win,
        times=window.payload_times(session, tenant_id),
        provider_times=window.provider_payment_times(session, tenant_id),
        in_window=window.orders_in_window(session, tenant_id, win),
    )

    # **`reconcile.run` is called whole and its output filtered**, rather than
    # the window being threaded into the comparison logic. Block C's handoff
    # keeps `reconcile.run(session, tenant_id)` a plain function over a session
    # with no scheduling, no windowing and no clock, and Block E calls that
    # same function for a recompute. One classification path, used by both.
    #
    # The cost is that an out-of-window order is classified and then discarded:
    # measured in queries this is one extra assembly pass over a tenant's
    # orders, which at the dataset's eighteen is not worth a second code path
    # that could disagree with the first.
    findings = [
        finding
        for finding in reconcile.run(session, tenant_id)
        if finding.order_ref in selection.all_refs
    ]

    for finding in findings:
        session.add(_row_for(finding, tenant_id=tenant_id, run_id=run_row.id))

    run_row.order_count = len(selection.order_refs)
    run_row.finding_count = len(findings)
    # **Counted through `exceptions_only`, never `len(findings)`.**
    # `awaiting_evidence` is a finding and not an exception, so the naive count
    # would fail the false-positive gate while being entirely correct about how
    # many findings there were (Block C's handoff).
    run_row.exception_count = len(exceptions_only(tuple(findings)))
    run_row.finished_at = datetime.now(UTC)
    run_row.status = "completed"

    session.flush()
    return run_row


def execute(
    session: Session, *, tenant_id: uuid.UUID, payload: dict[str, object]
) -> ReconciliationRun:
    """Run a window once, refusing a repeat of one already done.

    The marker is inserted **before** the work rather than after. Either order
    is safe inside one transaction, and inserting first means a duplicate is
    refused without the reconciliation being executed first and thrown away.

    A repeat raises `IntegrityError` from the constraint, which rolls the
    transaction back and leaves the first run's rows untouched.
    """
    win = _window_from(payload)
    if win.evidence_since is None:
        win = window.Window(
            start=win.start,
            end=win.end,
            evidence_since=window.last_completed_watermark(session, tenant_id),
        )

    key = idempotency_key(tenant_id, win)
    try:
        queue.mark_processed(
            session, tenant_id=tenant_id, event_id=key, job_type=JOB_TYPE
        )
    except IntegrityError as exc:
        raise AlreadyReconciled(key) from exc

    run_row = run_window(session, tenant_id=tenant_id, win=win)

    # Only runs the scheduler asked for. A person's "Run reconciliation" does
    # not spend model credit on their behalf; they choose what to investigate.
    if payload.get("auto_investigate") is True:
        investigation_job.auto_investigate(session, tenant_id)
    return run_row


def _window_from(payload: dict[str, object]) -> window.Window:
    """Read the window off a job payload.

    **No monetary value crosses this boundary** - a job carries identifiers
    and dates, and the amounts are read from the canonical tables by the
    engine. So `datetime.fromisoformat` is all the parsing this needs.
    """
    return window.Window(
        start=datetime.fromisoformat(str(payload["window_start"])),
        end=datetime.fromisoformat(str(payload["window_end"])),
    )


def _row_for(
    finding: Finding, *, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> ReconciliationException:
    """One `Finding` as one row, field for field.

    Not a summary and not a projection. `tests/test_reconciliation_rows.py`
    round-trips a finding through this and asserts nothing was lost, so the
    table and the frozen contract cannot drift apart.
    """
    return ReconciliationException(
        tenant_id=tenant_id,
        run_id=run_id,
        code=str(finding.code),
        order_external_ref=finding.order_ref,
        detail=finding.detail,
        is_exception=finding.is_exception,
        impact=finding.impact.quantize(),
        expected=finding.expected.quantize() if finding.expected else None,
        reported=finding.reported.quantize() if finding.reported else None,
        evidence_refs=list(finding.evidence_refs),
        evidence_basis=str(finding.evidence_basis),
        seller_id=finding.seller_id,
        confirmed=finding.confirmed,
    )
