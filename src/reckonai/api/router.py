"""Routes under `/api`. Reads only here; decisions and investigations are added
by their own groups and live in this router too."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from reckonai.agent import grid_assistant
from reckonai.api import queries, schemas
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


@router.post("/grid-assistant", response_model=schemas.GridAssistantOut)
def grid_assistant_route(body: schemas.GridAssistantIn) -> schemas.GridAssistantOut:
    """Turn a sentence into a grid state. Display only; reads no records.

    The browser sends the grid's own schema and state (AG Grid's AI Toolkit);
    nothing from the database is involved, so this route takes no session.
    """
    try:
        result = grid_assistant.ask(
            get_settings(), body.request, body.state, body.grid_schema
        )
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except grid_assistant.GridAssistantError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return schemas.GridAssistantOut(**result)
