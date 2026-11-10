"""Request dependencies: a session, and the tenant.

**The tenant is the seeded demo tenant, resolved server-side**
(`specs/2026-11-10-queue-approval-dashboard/requirements.md` D10). There is no
authentication in the MVP, so a request names no tenant and cannot choose one;
every query still filters by the `tenant_id` this returns.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from reckonai.models import Tenant
from reckonai.sim.data import TENANT_SLUG


def get_session(request: Request) -> Iterator[Session]:
    """One session per request, rolled back unless a route commits."""
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_tenant_id(session: SessionDep) -> uuid.UUID:
    tenant_id = session.scalar(select(Tenant.id).where(Tenant.slug == TENANT_SLUG))
    if tenant_id is None:
        raise HTTPException(status_code=503, detail="the demo tenant is not seeded")
    return tenant_id


TenantDep = Annotated[uuid.UUID, Depends(get_tenant_id)]
