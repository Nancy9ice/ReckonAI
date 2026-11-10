"""The entry point the Render Cron Job invokes.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D8, D9.

**It enqueues; it does not execute.** The worker executes. That split is what
makes the cron job cheap and the concurrency safe: a cron invocation that did
the work itself would be a third process writing financial rows, racing the
worker it was meant to feed, and `SKIP LOCKED` protects the queue rather than
whoever bypasses it.

Run it with:

    uv run python -m reckonai.jobs.schedule --window-days 2

and on Render with `--no-sync`, for the reason `render.yaml` documents: without
it `uv run` re-syncs the environment first and undoes the build's `--no-dev`.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from reckonai.config import get_settings
from reckonai.db import create_app_engine
from reckonai.jobs import queue, reconcile_job, window
from reckonai.logging import configure_logging, event
from reckonai.models import Tenant

logger = logging.getLogger("reckonai.jobs.schedule")

#: How wide a scheduled window is by default.
#:
#: Two days, not one. specs/tech-stack.md section 8 records the reporting APIs
#: as eventually consistent and roughly a day behind, so a one-day window
#: closes before the evidence for its last hours has arrived. The second day
#: costs nothing: re-reconciling a day whose verdicts are unchanged appends
#: identical rows, and the dedupe marker stops the *same* window running
#: twice.
DEFAULT_WINDOW_DAYS = 2


def window_ending(now: datetime, *, days: int) -> window.Window:
    """The window of `days` ending at midnight after today.

    Bounds are snapped to midnight UTC, so two invocations on the same day
    produce the same window and therefore the same idempotency key. An
    unsnapped `now` would make every hourly cron tick a new window, and the
    dedupe marker would never match.
    """
    end = datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)
    return window.Window(start=end - timedelta(days=days), end=end)


def enqueue_runs(session: Session, *, days: int) -> list[uuid.UUID]:
    """Enqueue one run per tenant, skipping any window already reconciled.

    **One job per tenant**, rather than one job that loops over tenants: a
    tenant whose reconciliation fails must not stop another tenant's, and the
    retry and the error belong to the tenant they happened to.
    """
    win = window_ending(datetime.now(UTC), days=days)
    enqueued: list[uuid.UUID] = []

    for tenant_id in session.scalars(select(Tenant.id).order_by(Tenant.slug)):
        # The marker is what makes this skip rather than queue a job the
        # handler would refuse. Enqueueing regardless would be correct but
        # noisy: every hourly tick would add a job that fails on the
        # constraint, and a queue full of expected failures hides a real one.
        if _already_reconciled(session, tenant_id, win):
            logger.info(
                event(
                    "reckonai.jobs.schedule.skipped",
                    tenant_id=str(tenant_id),
                    window_start=win.start.date().isoformat(),
                    window_end=win.end.date().isoformat(),
                )
            )
            continue

        job = queue.enqueue(
            session,
            tenant_id=tenant_id,
            job_type=reconcile_job.JOB_TYPE,
            payload={
                "window_start": win.start.isoformat(),
                "window_end": win.end.isoformat(),
            },
        )
        enqueued.append(job.id)
        logger.info(
            event(
                "reckonai.jobs.schedule.enqueued",
                job_id=str(job.id),
                tenant_id=str(tenant_id),
                window_start=win.start.date().isoformat(),
                window_end=win.end.date().isoformat(),
            )
        )

    return enqueued


def _already_reconciled(
    session: Session, tenant_id: uuid.UUID, win: window.Window
) -> bool:
    """Whether this tenant's window already has its dedupe marker.

    A read, so it races: two cron invocations could both see no marker. That
    is safe rather than tolerated - the handler inserts the marker inside its
    own transaction, so the second job fails on the constraint and writes
    nothing. This check keeps the queue readable; the constraint is what keeps
    it correct.
    """
    from reckonai.models import ProcessedEvent

    key = reconcile_job.idempotency_key(tenant_id, win)
    existing = session.scalars(
        select(ProcessedEvent.id).where(
            ProcessedEvent.tenant_id == tenant_id,
            ProcessedEvent.event_id == key,
        )
    ).first()
    return existing is not None


def main(argv: list[str] | None = None) -> int:
    """Enqueue the scheduled runs. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="reckonai.jobs.schedule",
        description="Enqueue a date-windowed reconciliation run per tenant.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=(
            "How many days the window covers, ending at midnight after today. "
            f"Default {DEFAULT_WINDOW_DAYS}, because the reporting APIs run "
            "about a day behind."
        ),
    )
    args = parser.parse_args(argv)

    if args.window_days < 1:
        parser.error("--window-days must be at least 1")

    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_app_engine(settings.sqlalchemy_url, pool_size=1, max_overflow=1)
    try:
        with Session(engine) as session:
            try:
                enqueued = enqueue_runs(session, days=args.window_days)
                session.commit()
            except IntegrityError:
                # Two cron invocations overlapping. The constraint did its job;
                # this is not an error worth a non-zero exit, which on Render
                # would show as a failed scheduled run.
                session.rollback()
                logger.info(event("reckonai.jobs.schedule.raced"))
                return 0

        logger.info(event("reckonai.jobs.schedule.done", enqueued=len(enqueued)))
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
