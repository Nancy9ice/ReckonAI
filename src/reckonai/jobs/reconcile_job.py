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
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import NamedTuple

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai import reconcile
from reckonai.jobs import investigation_job, queue, window
from reckonai.models import (
    ProcessedEvent,
    ReconciliationException,
    ReconciliationRun,
)
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


def _dates_prefix(tenant_id: uuid.UUID, win: window.Window) -> str:
    return (
        f"recon:{tenant_id}:{win.start.date().isoformat()}"
        f":{win.end.date().isoformat()}"
    )


def idempotency_key(tenant_id: uuid.UUID, win: window.Window) -> str:
    """The key that makes one window, in one state of the evidence, run once.

    Composed from the tenant, the window's dates and the watermark the run
    reads (`win.evidence_since`, or `none` before any run has completed).
    Dates rather than full timestamps for the window: a window is a date
    range, and two runs over the same dates are the same piece of work however
    their bounds were spelled.

    **The watermark is what lets a second run on the same dates exist at all**
    (`specs/2026-11-11-live-ingestion/requirements.md` section 4). Two requests
    that see the same watermark build the same key and the second is refused at
    the constraint; once a run completes the watermark moves, so the key
    changes. That alone would allow an endless chain of runs, because every
    completed run moves the watermark, which is why `decide_run` gates a
    repeat on new evidence as well. Both are required.
    """
    mark = win.evidence_since.isoformat() if win.evidence_since else "none"
    return f"{_dates_prefix(tenant_id, win)}:{mark}"


class RunVerdict(StrEnum):
    """Whether a run over these dates may be queued now."""

    PERMITTED = "permitted"

    #: This exact key already ran: the same dates in the same state of the
    #: evidence. The backstop; the constraint is what decides.
    ALREADY_DONE = "already_done"

    #: The dates have a run and nothing has been retrieved since it started.
    NOTHING_NEW = "nothing_new"

    #: The dates have a run, there is new evidence, and the last run finished
    #: inside the cooldown.
    COOLING_DOWN = "cooling_down"


class RunDecision(NamedTuple):
    """The verdict, and the window to enqueue with its watermark stamped in.

    The watermark is read once, here, and travels in the job payload. Were it
    read again at execution, two jobs queued against the same state of the
    evidence would build different keys once the first completed, and both
    would run.
    """

    verdict: RunVerdict
    window: window.Window


def decide_run(
    session: Session,
    tenant_id: uuid.UUID,
    win: window.Window,
    *,
    now: datetime,
    cooldown_seconds: int,
) -> RunDecision:
    """The same-day re-run rule.

    A run over dates that already have a run is permitted only if evidence has
    been retrieved since the last completed run started; dates with no run are
    always permitted, which keeps the nightly re-evaluation of
    `awaiting_evidence` working with no new evidence at all. A key in the
    pre-watermark format counts as a run over those dates.

    One function for the cron and for run-now, so there is one definition.
    """
    watermark = window.last_completed_watermark(session, tenant_id)
    keyed = window.Window(start=win.start, end=win.end, evidence_since=watermark)
    key = idempotency_key(tenant_id, keyed)

    def marker_exists(*conditions: ColumnElement[bool]) -> bool:
        return (
            session.scalars(
                select(ProcessedEvent.id)
                .where(ProcessedEvent.tenant_id == tenant_id, *conditions)
                .limit(1)
            ).first()
            is not None
        )

    if marker_exists(ProcessedEvent.event_id == key):
        return RunDecision(RunVerdict.ALREADY_DONE, keyed)

    prefix = _dates_prefix(tenant_id, win)
    if not marker_exists(
        or_(
            ProcessedEvent.event_id == prefix,
            ProcessedEvent.event_id.like(f"{prefix}:%"),
        )
    ):
        return RunDecision(RunVerdict.PERMITTED, keyed)

    if not window.has_new_evidence(session, tenant_id, watermark, until=now):
        return RunDecision(RunVerdict.NOTHING_NEW, keyed)

    last_finished = session.scalars(
        select(func.max(ReconciliationRun.finished_at)).where(
            ReconciliationRun.tenant_id == tenant_id,
            ReconciliationRun.status == "completed",
            # This window's own runs: a run over other dates finishing a minute
            # ago must not hold this one back.
            ReconciliationRun.window_start == win.start,
            ReconciliationRun.window_end == win.end,
        )
    ).first()
    if (
        cooldown_seconds > 0
        and last_finished is not None
        and now - last_finished < timedelta(seconds=cooldown_seconds)
    ):
        return RunDecision(RunVerdict.COOLING_DOWN, keyed)

    return RunDecision(RunVerdict.PERMITTED, keyed)


def payload_for(win: window.Window, **extra: object) -> dict[str, object]:
    """A job payload for a window, carrying the watermark `decide_run` read.

    `evidence_since` is written even when it is `None`: an explicit null means
    "no run had completed", and an absent key means "an older payload, read the
    watermark at execution". Those are different claims.
    """
    return {
        "window_start": win.start.isoformat(),
        "window_end": win.end.isoformat(),
        "evidence_since": (
            win.evidence_since.isoformat() if win.evidence_since else None
        ),
        **extra,
    }


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
    if "evidence_since" not in payload:
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
    stamped = payload.get("evidence_since")
    return window.Window(
        start=datetime.fromisoformat(str(payload["window_start"])),
        end=datetime.fromisoformat(str(payload["window_end"])),
        evidence_since=datetime.fromisoformat(str(stamped)) if stamped else None,
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
