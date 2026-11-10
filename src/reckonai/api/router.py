"""Routes under `/api`. Reads only here; decisions and investigations are added
by their own groups and live in this router too."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from reckonai.api import queries, schemas
from reckonai.api.deps import SessionDep, TenantDep

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
