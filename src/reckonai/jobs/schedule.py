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
#:
#: **The schedule is daily, and this is why.** The Cron Job was hourly, with a
#: comment claiming that meant a late PayPal record was picked up within the
#: hour. Code review found that false: `window_ending` snaps both bounds to
#: midnight UTC and `idempotency_key` uses only their dates, so all 24 ticks
#: in a UTC day compute the same key, the first one does the work and the
#: other 23 log a skip. The hourly schedule bought nothing and the comment
#: said otherwise.
#:
#: Keeping the key date-grained and running once a day is the honest version.
#: Making the key hourly instead would reconcile the same two dates 24 times a
#: day, appending 24 sets of identical rows for Block F's queue to read
#: through - which is worse than a day's latency on a provider that is itself
#: about a day behind.
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
    """Enqueue one run per tenant, unless `decide_run` says not to.

    A window already reconciled is skipped unless evidence has arrived since
    (the same-day re-run rule, `reconcile_job.decide_run`).

    **One job per tenant, committed per tenant**, rather than one job that
    loops over tenants: a tenant whose reconciliation fails must not stop
    another tenant's, and the retry and the error belong to the tenant they
    happened to. The per-tenant commit is what makes that true at enqueue time
    as well as at execution time.
    """
    win = window_ending(datetime.now(UTC), days=days)
    enqueued: list[uuid.UUID] = []

    for tenant_id in session.scalars(select(Tenant.id).order_by(Tenant.slug)):
        # The marker is what makes this skip rather than queue a job the
        # handler would refuse. Enqueueing regardless would be correct but
        # noisy: every hourly tick would add a job that fails on the
        # constraint, and a queue full of expected failures hides a real one.
        decision = reconcile_job.decide_run(
            session,
            tenant_id,
            win,
            now=datetime.now(UTC),
            cooldown_seconds=get_settings().run_cooldown_seconds,
        )
        if decision.verdict is not reconcile_job.RunVerdict.PERMITTED:
            logger.info(
                event(
                    "reckonai.jobs.schedule.skipped",
                    reason=str(decision.verdict),
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
            # The nightly run investigates what it finds, within the cap
            # `settings.auto_investigate_max` (see `investigation_job`).
            payload=reconcile_job.payload_for(decision.window, auto_investigate=True),
        )
        # **Committed per tenant, and that is the point of the loop.** Code
        # review found the isolation claim below false at enqueue time: one
        # commit for every tenant meant any failure part-way discarded every
        # tenant's enqueue, not just the failing one.
        session.commit()
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


def _recover(session: Session, *, hours: int) -> bool:
    """Missed-delivery recovery, which must never stop the nightly run.

    Returns whether it worked. A failure is logged at error level and `main`
    exits non-zero after the run is enqueued, so a recovery that quietly never
    runs shows as a failed cron job instead of a green one.
    """
    from reckonai.jobs import recover
    from reckonai.paypal import live

    client = None
    try:
        client = live.LiveClient(live.credentials())
        now = datetime.now(UTC)
        recover.recover(
            session, client, since=now - timedelta(hours=hours), until=now
        )
        return True
    except Exception as exc:  # noqa: BLE001 - deliberate: the run goes ahead
        session.rollback()
        logger.error(
            event(
                "reckonai.jobs.schedule.recover_failed",
                error=type(exc).__name__,
                detail=str(exc)[:200],
            )
        )
        return False
    finally:
        if client is not None:
            client.close()


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
    parser.add_argument(
        "--recover-hours",
        type=int,
        default=0,
        help=(
            "Before enqueueing, recover webhook events PayPal recorded in the "
            "last N hours that were not delivered (live-ingestion F9). 0 skips "
            "it. A failure here is logged and never blocks the run."
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
            # **No `IntegrityError` branch here**, which there used to be.
            # Code review found it could not fire for the reason it stated:
            # this function inserts only `job` rows, and those carry no unique
            # constraint two overlapping cron invocations could collide on.
            # The race is real but lands elsewhere - both invocations enqueue,
            # and the second job's handler meets the `processed_event`
            # constraint, which `reconcile_job.AlreadyReconciled` now reports
            # as work already done rather than as a failure to retry.
            recovered = True
            if args.recover_hours > 0:
                recovered = _recover(session, hours=args.recover_hours)
            enqueued = enqueue_runs(session, days=args.window_days)

        logger.info(event("reckonai.jobs.schedule.done", enqueued=len(enqueued)))
        if not recovered:
            return 1
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
