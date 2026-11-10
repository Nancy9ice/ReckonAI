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

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    RETRY_DELAY_SECONDS,
    Job,
    ProcessedEvent,
)


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
    releases the lock. So a worker that dies mid-job leaves a `claimed` row
    rather than a locked one, recoverable through `run_after` (D6's stated
    reason for not building a heartbeat).

    Eligibility is `queued` and `run_after` in the past. Ordered by
    `run_after` so the oldest due work goes first; without the ordering a
    retried job could starve behind a steady stream of new ones.
    """
    now = datetime.now(UTC)

    statement = (
        select(Job)
        .where(Job.status == "queued", Job.run_after <= now)
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
