"""Enqueueing and claiming, over the `job` table.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D5, D6, D7, and
specs/tech-stack.md section 4.

**The claim is `SELECT ... FOR UPDATE SKIP LOCKED`**, which is what makes
concurrent claiming safe without a broker. Render runs two workers
concurrently for about 90 seconds on every rolling deploy (observed
2026-11-08), so this is the condition production is already in rather than one
to construct in a test.

**Nothing here decides anything financial.** A job is an instruction to go and
look; the handler does the work and the engine computes the figures.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from reckonai.models.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    RETRY_DELAY_SECONDS,
    Job,
    ProcessedEvent,
)

#: How long a `claimed` job may sit before another worker may take it.
#:
#: Chosen against the only job type there is: a reconciliation run over the
#: seeded tenant completes in well under a second, and Render's rolling deploy
#: overlaps two workers for about 90 seconds. Five minutes is therefore far
#: longer than any healthy claim and short enough that a lost job is picked up
#: within one cron interval.
#:
#: **This is not a heartbeat and does not pretend to be.** It cannot tell a
#: dead worker from a very slow one, so a job slower than this may be run
#: twice - which is survivable here precisely because the dedupe marker makes
#: a second run of the same window write nothing (D5).
STALE_CLAIM_SECONDS = 300


def enqueue(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    payload: dict[str, object],
    run_after: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Job:
    """Add one job to the queue.

    Flushed rather than committed: the caller owns the transaction, because an
    enqueue is frequently one half of something - the scheduled run's job and
    its dedupe marker, for instance, which must land together or not at all.
    """
    job = Job(
        tenant_id=tenant_id,
        job_type=job_type,
        payload=payload,
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        run_after=run_after if run_after is not None else datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def claim(session: Session, *, limit: int = 1) -> Sequence[Job]:
    """Claim up to `limit` eligible jobs, skipping any another worker holds.

    **`with_for_update(skip_locked=True)` is the whole point.** Two workers
    issuing this at the same instant get disjoint sets: the second skips the
    rows the first has locked rather than blocking on them, which is what
    `FOR UPDATE` alone would do - and blocking would serialise the workers
    into one.

    The status change is committed by the caller, and that commit is what
    releases the lock, so a worker that dies mid-job leaves a `claimed` row
    rather than a locked one.

    **A stale claim is reclaimed here, and that is what makes the no-heartbeat
    decision sound.** Code review found the original form unrecoverable:
    nothing moved a row from `claimed` back to `queued`, so a process that
    died between the claim and the handler's own error handling - or hit the
    status guard in `handlers.dispatch`, or failed to commit its failure -
    left a job no worker would ever pick up again, with no error recorded and
    its window silently never reconciled. `run_after` was cited as the
    recovery path and could not be one, because the claim never returned to
    the status `run_after` is filtered on.

    So eligibility is either of:

      - `queued` with `run_after` in the past, or
      - `claimed` with `claimed_at` older than `STALE_CLAIM_SECONDS`.

    Both are capped by `attempts < max_attempts`, which the `CHECK
    (attempts <= max_attempts)` constraint requires - without it a repeatedly
    reclaimed job would increment past its cap and fail the flush rather than
    its own job. A stale job already at its cap is terminated by
    `reap_stale_claims` instead.

    Ordered by `run_after` so the oldest due work goes first; without the
    ordering a retried job could starve behind a steady stream of new ones.
    """
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=STALE_CLAIM_SECONDS)

    statement = (
        select(Job)
        .where(
            Job.attempts < Job.max_attempts,
            or_(
                and_(Job.status == "queued", Job.run_after <= now),
                and_(
                    Job.status == "claimed",
                    Job.claimed_at.is_not(None),
                    Job.claimed_at < stale_before,
                ),
            ),
        )
        .order_by(Job.run_after)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )

    jobs = list(session.scalars(statement))

    for job in jobs:
        job.status = "claimed"
        job.claimed_at = now
        job.attempts += 1

    session.flush()
    return jobs


def complete(session: Session, job: Job) -> None:
    """Mark a claimed job done.

    `last_error` is deliberately **not** cleared: a job that failed twice and
    then succeeded is more useful to read than one that looks like it always
    worked, and nothing branches on the column.
    """
    job.status = "done"
    session.flush()


def fail(session: Session, job: Job, error: BaseException) -> None:
    """Record a failure, and either schedule a retry or give up.

    Retry in R7's narrowed form: an attempt count, a fixed `run_after` delay,
    and a terminal `failed` state with the error recorded. Not a tuned
    backoff - nothing in this MVP has the traffic to tune one against.

    **The message, never a traceback.** A traceback can carry row contents,
    and this column is read by a dashboard.
    """
    job.last_error = f"{type(error).__name__}: {error}"

    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "queued"
        job.run_after = datetime.now(UTC) + timedelta(seconds=RETRY_DELAY_SECONDS)

    session.flush()


def mark_processed(
    session: Session, *, tenant_id: uuid.UUID, event_id: str, job_type: str
) -> ProcessedEvent:
    """Insert the dedupe marker, or raise `IntegrityError` if it is already there.

    **The raise is the mechanism, not a failure mode to avoid.** A handler that
    queried for an existing marker and then inserted one has a window between
    the two; the `UNIQUE` constraint has none. So this does not check first,
    and a caller that wants "process once" lets the exception roll the
    transaction back.

    Called **inside** the handler's transaction, alongside the rows the job
    writes. That co-location is the reason the queue lives in Postgres at all
    (specs/tech-stack.md section 4): one `COMMIT` covers the marker and the
    financial effect, so no crash can leave a marker claiming work was done
    whose output was rolled back.
    """
    marker = ProcessedEvent(
        tenant_id=tenant_id, event_id=event_id, job_type=job_type
    )
    session.add(marker)
    session.flush()
    return marker


def reap_stale_claims(session: Session) -> Sequence[Job]:
    """Terminate `claimed` jobs that are stale *and* out of attempts.

    `claim` reclaims a stale job while it has attempts left. This is the other
    half: a job whose worker died on its final attempt would otherwise sit
    `claimed` forever, below `claim`'s `attempts < max_attempts` filter and
    invisible to every worker.

    It records why, because a job that simply stopped being worked is the
    hardest kind to diagnose later.
    """
    stale_before = datetime.now(UTC) - timedelta(seconds=STALE_CLAIM_SECONDS)

    statement = (
        select(Job)
        .where(
            Job.status == "claimed",
            Job.attempts >= Job.max_attempts,
            Job.claimed_at.is_not(None),
            Job.claimed_at < stale_before,
        )
        .with_for_update(skip_locked=True)
    )

    jobs = list(session.scalars(statement))

    for job in jobs:
        job.status = "failed"
        job.last_error = (
            "ClaimExpired: the worker holding this job stopped without "
            "recording an outcome, and no attempts remain"
        )

    session.flush()
    return jobs
