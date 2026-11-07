"""The liveness and readiness routes, including the behaviour they exist for."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from reckonai import __version__
from reckonai.app import app
from reckonai.db import create_short_lived_engine

#: A port nothing listens on, so the connection is refused immediately rather
#: than hanging for a TCP timeout.
_UNREACHABLE_URL = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/absent"


@pytest.fixture
def client(database_url: str) -> TestClient:
    # Used as a context manager so the lifespan handler runs and the engine is
    # built the way it is in a deployed process.
    with TestClient(app) as test_client:
        yield test_client


def test_the_root_route_is_not_a_404(client: TestClient) -> None:
    """The deployed URL must not read as a broken service in a browser."""
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "reckonai"
    assert body["version"] == __version__
    # The paths it advertises have to actually exist.
    for path in (body["docs"], body["health"], body["readiness"]):
        assert client.get(path).status_code == 200


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_ready_reports_ready_against_a_live_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "version": __version__}


def test_liveness_survives_a_database_outage(client: TestClient) -> None:
    """The reason /health and /health/ready are separate routes.

    Render restarts a service whose health check keeps failing. If liveness
    opened a database connection, connection pressure and restarts would feed
    each other: the check fails, Render restarts, the new process rebuilds its
    pool, the check fails again. A restart adds no connections, so the loop
    never converges.

    This asserts the two halves of that: liveness stays 200 while the database
    is unreachable, and readiness does not pretend everything is fine.
    """
    original = app.state.engine
    app.state.engine = create_short_lived_engine(_UNREACHABLE_URL)
    try:
        liveness = client.get("/health")
        readiness = client.get("/health/ready")
    finally:
        app.state.engine.dispose()
        app.state.engine = original

    assert liveness.status_code == 200, "liveness must not depend on the database"
    assert readiness.status_code == 503
    assert readiness.json() == {
        "status": "unavailable",
        "reason": "database_unreachable",
    }


def test_readiness_failure_leaks_no_credential(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed probe logs an exception type, never the connection URL.

    SQLAlchemy's error text can carry the URL, password included
    (specs/tech-stack.md section 10).
    """
    original = app.state.engine
    app.state.engine = create_short_lived_engine(_UNREACHABLE_URL)
    try:
        with caplog.at_level("WARNING"):
            client.get("/health/ready")
    finally:
        app.state.engine.dispose()
        app.state.engine = original

    logged = caplog.text
    assert "reckonai.health.not_ready" in logged
    assert "nobody" not in logged
