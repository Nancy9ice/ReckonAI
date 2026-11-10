"""Routes under `/api`. Reads only here; decisions and investigations are added
by their own groups and live in this router too."""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from reckonai.agent import grid_assistant
from reckonai.api import evidence, export, queries, schemas
from reckonai.api.deps import SessionDep, TenantDep
from reckonai.config import ConfigurationError, get_settings

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/runs", response_model=list[schemas.RunOut])
def runs(session: SessionDep, tenant_id: TenantDep) -> list[schemas.RunOut]:
    return [
        schemas.RunOut.model_validate(r, from_attributes=True)
        for r in queries.list_runs(session, tenant_id)
    ]


@router.get("/exceptions", response_model=list[schemas.ExceptionOut])
def exceptions(
    session: SessionDep, tenant_id: TenantDep, run_id: uuid.UUID | None = None
) -> list[schemas.ExceptionOut]:
    """The queue, highest priority first.

    Without `run_id`, the newest finding per order and code across all runs;
    with it, exactly that run's exceptions (the run summary's click-through).
    """
    return queries.exceptions(session, tenant_id, run_id)


@router.get("/exceptions/{exception_id}", response_model=schemas.ExceptionDetail)
def exception_detail(
    exception_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep
) -> schemas.ExceptionDetail:
    found = queries.exception_detail(session, tenant_id, exception_id)
    if found is None:
        raise HTTPException(status_code=404, detail="no such exception")
    return found


@router.post(
    "/exceptions/{exception_id}/decision",
    response_model=schemas.ExceptionDetail,
    status_code=201,
)
def decide(
    exception_id: uuid.UUID,
    body: schemas.DecisionIn,
    session: SessionDep,
    tenant_id: TenantDep,
) -> schemas.ExceptionDetail:
    """Record an approve or a reject. Moves no money (`mission.md` P6).

    Append-only: deciding again adds a row, and the newest is the effective one.
    """
    if queries.record_decision(session, tenant_id, exception_id, body) is None:
        raise HTTPException(status_code=404, detail="no such exception")
    session.commit()
    found = queries.exception_detail(session, tenant_id, exception_id)
    assert found is not None
    return found


@router.post(
    "/exceptions/{exception_id}/investigate",
    response_model=schemas.JobOut,
    status_code=202,
)
def investigate(
    exception_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep
) -> schemas.JobOut:
    """Queue an investigation. Returns at once; the worker runs it.

    The agent is read-only (`mission.md` P6), so this changes nothing about the
    books; it adds an investigation row when the job completes.
    """
    job = queries.request_investigation(session, tenant_id, exception_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such exception")
    session.commit()
    return queries.job_out(job)


#: A ceiling on paid model calls from this process, because the route is
#: unauthenticated (requirements D5) and every call spends the operator's
#: credit. A sliding window, in memory: it bounds one process, which is the
#: honest scope of what it can promise.
ASSISTANT_CALLS_PER_MINUTE = 20
_assistant_calls: deque[float] = deque()


def _assistant_slot() -> bool:
    now = time.monotonic()
    while _assistant_calls and now - _assistant_calls[0] > 60:
        _assistant_calls.popleft()
    if len(_assistant_calls) >= ASSISTANT_CALLS_PER_MINUTE:
        return False
    _assistant_calls.append(now)
    return True


@router.post("/grid-assistant", response_model=schemas.GridAssistantOut)
def grid_assistant_route(body: schemas.GridAssistantIn) -> schemas.GridAssistantOut:
    """Turn a sentence into a grid state. Display only; reads no records.

    The browser sends the grid's own schema and state (AG Grid's AI Toolkit);
    nothing from the database is involved, so this route takes no session.
    """
    if not _assistant_slot():
        raise HTTPException(
            status_code=429, detail="too many assistant requests; try again shortly"
        )
    try:
        result = grid_assistant.ask(
            get_settings(), body.request, body.state, body.grid_schema
        )
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except grid_assistant.GridAssistantError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return schemas.GridAssistantOut(**result)


@router.post("/runs", response_model=schemas.RunRequestOut, status_code=202)
def request_run(session: SessionDep, tenant_id: TenantDep) -> schemas.RunRequestOut:
    """Queue a reconciliation over the whole dataset (or say why not)."""
    result = queries.request_run(session, tenant_id)
    session.commit()
    return result


@router.get("/jobs/{job_id}", response_model=schemas.JobOut)
def job_status(
    job_id: uuid.UUID, session: SessionDep, tenant_id: TenantDep
) -> schemas.JobOut:
    job = queries.get_job(session, tenant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return queries.job_out(job)


@router.get("/coverage", response_model=schemas.CoverageOut)
def coverage(session: SessionDep, tenant_id: TenantDep) -> schemas.CoverageOut:
    """What was checked and found clean, and what is parked awaiting evidence."""
    return queries.coverage(session, tenant_id)


@router.get("/evidence/{ref:path}/raw")
def evidence_raw(ref: str, session: SessionDep, tenant_id: TenantDep) -> Any:
    """The provider's own response behind a record, as retained."""
    body = evidence.raw_body(session, tenant_id, ref)
    if body is None:
        raise HTTPException(
            status_code=404, detail="no retained payload for that record"
        )
    return body


@router.get("/evidence/{ref:path}", response_model=schemas.EvidenceOut)
def evidence_record(
    ref: str, session: SessionDep, tenant_id: TenantDep
) -> schemas.EvidenceOut:
    """The record a cited reference names, or 404 if it names nothing."""
    found = evidence.describe(session, tenant_id, ref)
    if found is None:
        raise HTTPException(status_code=404, detail="that reference names no record")
    return found


def _csv(body: str, name: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/export/exceptions.csv")
def export_exceptions(
    session: SessionDep, tenant_id: TenantDep, run_id: uuid.UUID | None = None
) -> Response:
    return _csv(export.queue_csv(session, tenant_id, run_id), "reckonai-exceptions.csv")


@router.get("/export/decisions.csv")
def export_decisions(session: SessionDep, tenant_id: TenantDep) -> Response:
    """The decision log, every row ever recorded: the audit trail."""
    return _csv(export.decisions_csv(session, tenant_id), "reckonai-decisions.csv")
