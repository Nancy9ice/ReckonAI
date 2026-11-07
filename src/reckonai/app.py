"""The FastAPI application: liveness, readiness, and nothing else yet.

Roadmap Phase 1 is a deployable spine. No financial endpoint exists here, and no
monetary value is serialized, so there is no JSON encoder decision to make yet -
Phase 2 owns that when `Money` exists (specs/tech-stack.md section 2).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from reckonai import __version__
from reckonai.config import get_settings
from reckonai.db import check_connection, create_app_engine
from reckonai.logging import configure_logging, event

# This component's share of the connection budget. The cap it is drawn against
# is `reckonai.db.USABLE_CONNECTIONS`; see that module and the phase's
# requirements.md D6.
WEB_POOL_SIZE = 10
WEB_MAX_OVERFLOW = 5

logger = logging.getLogger("reckonai.web")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.engine = create_app_engine(
        settings.sqlalchemy_url,
        pool_size=WEB_POOL_SIZE,
        max_overflow=WEB_MAX_OVERFLOW,
    )
    logger.info(
        event(
            "reckonai.web.start",
            environment=settings.environment,
            version=__version__,
            pool_size=WEB_POOL_SIZE,
            max_overflow=WEB_MAX_OVERFLOW,
        )
    )

    try:
        yield
    finally:
        app.state.engine.dispose()
        logger.info(event("reckonai.web.stop"))


app = FastAPI(
    title="ReckonAI",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. Touches no database, on purpose.

    Render restarts a service whose health check keeps failing. If this probe
    opened a database connection, connection pressure and restarts would feed
    each other: the check fails, Render restarts, the fresh process rebuilds its
    pool, the check fails again. A restart adds no connections, so the loop does
    not converge. Readiness below carries the database question instead.
    """
    return {"status": "ok", "version": __version__}


# Defined with `def` rather than `async def` so FastAPI runs it in a threadpool:
# the driver call below is blocking, and awaiting it on the event loop would
# stall every other request while the database is slow.
@app.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness. Executes SELECT 1, and never answers 200 on a failed probe."""
    try:
        check_connection(app.state.engine)
    except SQLAlchemyError as exc:
        # The exception text can carry the connection URL, so it is logged as a
        # type rather than interpolated into the response or the log line
        # (specs/tech-stack.md section 10).
        logger.warning(
            event("reckonai.health.not_ready", error=type(exc).__name__)
        )
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "reason": "database_unreachable"},
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ready", "version": __version__},
    )
