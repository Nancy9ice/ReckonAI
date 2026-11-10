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

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.agent.investigate import investigate
from reckonai.agent.providers import provider_for
from reckonai.config import get_settings
from reckonai.models import ReconciliationException

JOB_TYPE = "investigation"


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
