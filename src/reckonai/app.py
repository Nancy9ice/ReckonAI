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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from reckonai import __version__, spa
from reckonai.api import router as api_router
from reckonai.config import get_settings
from reckonai.db import check_connection, create_app_engine
from reckonai.logging import configure_logging, event
from reckonai.sim import router as sim_router
from reckonai.webhooks import router as webhooks_router

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

# The product's own read API (Block F). Distinct from `/sim`, which feeds data in.
app.include_router(api_router)

# PayPal webhook deliveries (live-ingestion F1). Public by necessity: PayPal
# cannot authenticate to us, so the signature check is the only gate.
app.include_router(webhooks_router)


# The dashboard's built assets, when there is a build. The page itself is
# served by `/` below, which falls back to the JSON index without one.
_dist = spa.dist_dir()
if _dist is not None and (_dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/", response_model=None)
def index() -> dict[str, str] | FileResponse:
    """A service index, so the deployed URL does not answer 404 in a browser.

    Added after the first successful deploy, where every route worked and `/`
    returned `{"detail": "Not Found"}` - which reads as a broken deployment to
    anyone opening the URL, including a judge.

    It carries no financial data and names no record.

    **With a frontend build present, `/` is the dashboard** (Block F, D1), and
    this index moves to `/service` so it stays reachable. Without a build - CI,
    or local API work - `/` is still this index, never a 404.
    """
    dist = spa.dist_dir()
    if dist is not None:
        return FileResponse(dist / "index.html")
    return service_index()


@app.get("/service")
def service() -> dict[str, str]:
    """The service index, always, whether or not a dashboard is built."""
    return service_index()


def service_index() -> dict[str, str]:
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
        "api": "/api/exceptions",
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
