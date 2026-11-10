"""The handler registry, and the dispatch one claimed job goes through.

`specs/2026-11-09-queue-scheduled-run/requirements.md` D7.

A registry keyed on `job_type` rather than a branch inside the worker loop, so
Block F adds a job type by registering one here instead of editing the loop
that every other job type also runs through.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from reckonai.jobs import queue, reconcile_job
from reckonai.jobs.reconcile_job import AlreadyReconciled
from reckonai.logging import event
from reckonai.models import Job

logger = logging.getLogger("reckonai.jobs")

#: A handler takes the session it must do its work in, the tenant, and the
#: job's payload. It returns nothing: what a job produced is in the rows it
#: wrote, not in a value the loop would have to interpret.
Handler = Callable[[Session, uuid.UUID, dict[str, object]], None]


def _handle_reconciliation_run(
    session: Session, tenant_id: uuid.UUID, payload: dict[str, object]
) -> None:
    reconcile_job.execute(session, tenant_id=tenant_id, payload=payload)


HANDLERS: dict[str, Handler] = {
    reconcile_job.JOB_TYPE: _handle_reconciliation_run,
}


def dispatch(session: Session, job: Job) -> bool:
    """Run one claimed job, and record how it ended.

    Returns whether it succeeded, which the worker uses only for logging - the
    job's own row is the authoritative record either way.

    **The claim must already be committed when this is called**, and the
    worker loop commits it. That boundary is not stylistic: if the claim and
    the handler shared one transaction, a failing handler's rollback would
    also undo the claim *and* the attempt increment, so the job would return
    to the queue looking untried and retry forever. The attempt count is what
    makes a failure terminal, so it has to be durable before the attempt is
    made.

    **The handler's rows and the job's terminal status are one transaction**,
    committed here. A crash between them would otherwise leave the work done
    and the job still claimed, which no later worker would pick up.

    On failure the session is rolled back *first*, so the error write is not
    itself inside the poisoned transaction. The rollback expires the job
    instance rather than detaching it, and the committed claim is what it
    re-reads - which is why `attempts` survives the failure it is counting.
    """
    if job.status != "claimed":
        raise RuntimeError(
            f"job {job.id} is {job.status}, not claimed; dispatch requires a "
            "committed claim (see this function's docstring)"
        )

    handler = HANDLERS.get(job.job_type)

    if handler is None:
        # Unreachable through the `CHECK` on `job_type`, which is the point of
        # having it - but a type added to the vocabulary and not registered
        # would land here, and a job that no handler claims must fail loudly
        # rather than be claimed and silently dropped.
        queue.fail(session, job, LookupError(f"no handler for {job.job_type}"))
        session.commit()
        logger.error(event("reckonai.jobs.unhandled", job_type=job.job_type))
        return False

    try:
        handler(session, job.tenant_id, dict(job.payload))
        queue.complete(session, job)
        session.commit()
    except AlreadyReconciled as exc:
        # Not a failure: the window is done, so the job is done. Retrying it
        # would hit the same constraint three more times and leave a terminal
        # `failed` row that reads like a defect.
        session.rollback()
        queue.complete(session, job)
        session.commit()
        logger.info(
            event(
                "reckonai.jobs.already_done",
                job_id=str(job.id),
                job_type=job.job_type,
                key=str(exc),
            )
        )
        return True
    except Exception as exc:
        session.rollback()
        queue.fail(session, job, exc)
        session.commit()
        logger.warning(
            event(
                "reckonai.jobs.failed",
                job_id=str(job.id),
                job_type=job.job_type,
                attempts=job.attempts,
                status=job.status,
                error=type(exc).__name__,
            )
        )
        return False

    logger.info(
        event(
            "reckonai.jobs.completed",
            job_id=str(job.id),
            job_type=job.job_type,
            attempts=job.attempts,
        )
    )
    return True
