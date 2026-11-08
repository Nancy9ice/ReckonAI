"""The FastAPI application: liveness and readiness.

Roadmap Phase 1 delivered both. Phase 2 added a third route, `GET
/money/example`, which was the smallest thing that proved `Money` serializes as
JSON strings through the real application rather than only in a unit test
(specs/tech-stack.md section 2).

**That probe was removed in Block A**, as Phase 4 required: it was a probe and
not product, and leaving it meant a deployed URL answering `104.05` for no
reason. The property it proved is now asserted against the simulated
marketplace's real endpoints, which carry money in every response - see
`tests/conftest.py`'s `decode_strictly` and `tests/test_sim_api.py`.

Phase 14 adds the dashboard, as a separate Render static site.
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
from reckonai.sim import router as sim_router

# This component's share of the connection budget. The cap it is drawn against
# is `reckonai.db.USABLE_CONNECTIONS`; see that module and the phase's
# requirements.md D6.
WEB_POOL_SIZE = 5
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
            # Render supplies this; locally and in CI there is no external URL,
            # and "none" is logged rather than the field being dropped, so the
            # line's shape does not change between environments and stays
            # parseable by the same expression.
            url=settings.render_external_url or "none",
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

# The simulated marketplace, under `/sim`
# (specs/2026-11-08-block-a-marketplace-connector/requirements.md D1).
#
# **Mounted in this application rather than deployed as a second service**, so
# there is one process, one deploy and one demo URL. It is the system under
# integration, not part of the product: the connector reads it over HTTP as it
# would any third party, pointed at `MARKETPLACE_BASE_URL`.
app.include_router(sim_router, prefix="/sim")


@app.get("/")
def index() -> dict[str, str]:
    """A service index, so the deployed URL does not answer 404 in a browser.

    Added after the first successful deploy, where every route worked and `/`
    returned `{"detail": "Not Found"}` - which reads as a broken deployment to
    anyone opening the URL, including a judge.

    It carries no financial data and names no record. Phase 14 adds the React
    dashboard as a separate Render static site, so this stays the API's own
    index rather than something the UI will later displace.
    """
    return {
        "service": "reckonai",
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "readiness": "/health/ready",
        # The simulated marketplace's own index, so the deployed URL leads
        # somewhere rather than requiring the paths to be known in advance.
        # The index itself reads no database - see `sim.router`.
        "marketplace": "/sim/marketplace",
    }


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
