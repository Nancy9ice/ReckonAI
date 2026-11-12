"""The handler: investigate one persisted exception, off the request path.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D2.

A request-scoped call is the wrong shape: the agent takes 6-8 seconds on
`gpt-4.1` and is specified for adaptive thinking on Haiku. The API enqueues and
returns; the worker runs this; the UI polls.

**A provider failure is a recorded investigation, not a failed job.**
`agent.investigate` already turns a refused or unreachable model into a
`failed` investigation row, which the UI shows. The job fails only on what the
agent cannot record - a missing credential, or an exception that no longer
reproduces - and those retry like any other failed job.

The agent is read-only (P6), so a retried job writes at most one more
investigation row, never a second effect on the books.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from reckonai import findings
from reckonai.agent.investigate import investigate
from reckonai.agent.providers import provider_for
from reckonai.config import ConfigurationError, Settings, get_settings
from reckonai.jobs import queue
from reckonai.logging import event
from reckonai.models import (
    Investigation,
    Job,
    ReconciliationException,
)

logger = logging.getLogger("reckonai.jobs")

JOB_TYPE = "investigation"

#: Verdicts that settle an exception for now. A `failed` investigation does not:
#: it produced no report, so the next scheduled run tries again.
SETTLED_VERDICTS = ("reported", "insufficient_evidence")


def payload_for(exception_id: uuid.UUID) -> dict[str, object]:
    return {"exception_id": str(exception_id)}


def execute(
    session: Session, *, tenant_id: uuid.UUID, payload: dict[str, object]
) -> None:
    exception_id = uuid.UUID(str(payload["exception_id"]))
    row = session.scalar(
        select(ReconciliationException).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.id == exception_id,
        )
    )
    if row is None:
        raise LookupError(f"no exception {exception_id} for this tenant")

    settings = get_settings()
    investigate(
        session,
        tenant_id,
        order_ref=row.order_external_ref,
        code=row.code,
        provider=provider_for(settings),
        model=settings.agent_model,
    )


def latest_job(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> Job | None:
    """The newest investigation job for one exception, in any state."""
    return session.scalar(
        select(Job)
        .where(
            Job.tenant_id == tenant_id,
            Job.job_type == JOB_TYPE,
            Job.payload["exception_id"].astext == str(exception_id),
        )
        .order_by(Job.created_at.desc(), Job.run_after.desc())
        .limit(1)
    )


def request(
    session: Session, tenant_id: uuid.UUID, exception_id: uuid.UUID
) -> tuple[Job, bool] | None:
    """Queue an investigation, or find the one already in flight.

    Returns `(job, created)`; `created` is False where a `queued` or `claimed`
    job already existed, so a double click, a bulk request and the nightly run
    cannot queue the same paid investigation twice. `None` if the exception is
    not this tenant's.
    """
    # Serialises concurrent requests for one exception for the rest of this
    # transaction, so the check and the enqueue are a single step.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"investigate:{exception_id}"},
    )
    exists = session.scalar(
        select(ReconciliationException.id).where(
            ReconciliationException.tenant_id == tenant_id,
            ReconciliationException.id == exception_id,
        )
    )
    if exists is None:
        return None
    current = latest_job(session, tenant_id, exception_id)
    if current is not None and current.status in ("queued", "claimed"):
        return current, False
    job = queue.enqueue(
        session,
        tenant_id=tenant_id,
        job_type=JOB_TYPE,
        payload=payload_for(exception_id),
    )
    return job, True


def is_settled(session: Session, row: ReconciliationException) -> bool:
    """Whether this exception's newest investigation reached a conclusion."""
    verdict = session.scalar(
        select(Investigation.verdict)
        .where(
            Investigation.tenant_id == row.tenant_id,
            Investigation.order_external_ref == row.order_external_ref,
            Investigation.code == row.code,
        )
        .order_by(Investigation.started_at.desc())
        .limit(1)
    )
    return verdict in SETTLED_VERDICTS


@dataclass(frozen=True)
class AutoResult:
    queued: int
    already_settled: int
    in_flight: int
    over_limit: int
    skipped_reason: str | None = None


def open_exceptions(
    session: Session, tenant_id: uuid.UUID
) -> list[ReconciliationException]:
    """Every open exception, by the one definition the queue uses.

    `findings.newest_rows`: the newest row per `(order, code)` across all runs,
    less any a later run re-examined and stopped reporting. Sharing it is what
    stops the sweep paying for an investigation of a finding the queue no
    longer shows.
    """
    return findings.newest_rows(session, tenant_id, None)


def auto_investigate(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> AutoResult:
    """Queue an investigation for every open exception that needs one.

    Called from the scheduled run, inside its transaction, so the jobs and the
    run they follow commit together. **It sweeps all open exceptions, not only
    the ones this run concluded**: a run is bound to its date window, so an
    older exception the window no longer reaches would otherwise stay
    uninvestigated for ever. The reconciliation itself is unchanged and still
    windowed; only what gets investigated is wider. **It enqueues and nothing
    else**: the worker investigates, one job at a time, and the agent is
    read-only (P6).

    Needs one means no settled investigation yet; a previous `failed` one does
    not count, so a credential fixed overnight is picked up the next night.
    Capped at `settings.auto_investigate_max` because each is a paid call.
    **Skipped entirely, with a warning, when no model credential is
    configured**: queueing jobs that can only fail would fill the queue with
    expected errors that hide a real one.
    """
    settings = settings or get_settings()
    limit = settings.auto_investigate_max
    if limit == 0:
        return AutoResult(0, 0, 0, 0, "disabled")
    try:
        settings.require_agent_api_key()
    except ConfigurationError as error:
        logger.warning(
            event("reckonai.jobs.auto_investigate.skipped", reason=str(error))
        )
        return AutoResult(0, 0, 0, 0, "no credential")

    rows = open_exceptions(session, tenant_id)

    queued = settled = in_flight = over = 0
    for row in rows:
        if is_settled(session, row):
            settled += 1
            continue
        if queued >= limit:
            over += 1
            continue
        found = request(session, tenant_id, row.id)
        if found is None:
            continue
        _job, created = found
        if created:
            queued += 1
        else:
            in_flight += 1

    logger.info(
        event(
            "reckonai.jobs.auto_investigate",
            queued=queued,
            already_settled=settled,
            in_flight=in_flight,
            over_limit=over,
        )
    )
    return AutoResult(queued, settled, in_flight, over)
