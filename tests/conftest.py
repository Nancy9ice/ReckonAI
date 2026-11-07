"""Shared fixtures.

Two deliberate choices about what happens when there is no database.

A missing `DATABASE_URL` is a **hard failure in CI** and an explanatory **skip**
locally. A silent skip everywhere would mean a green CI run that tested nothing,
which is the failure mode this project can least afford in its deployment gate.

The engine fixture also **asserts the server's major version**, so no test that
touches a database can pass against the wrong one. See the module comment on
that fixture for why that is not paranoia.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from reckonai.config import get_settings
from reckonai.db import create_short_lived_engine, server_major_version

#: The major version compose, CI and the Render instance all pin.
REQUIRED_SERVER_MAJOR = 16

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_NO_URL_MESSAGE = (
    "DATABASE_URL is not set. Start the local database and export it:\n"
    "  docker compose up -d --wait\n"
    '  export DATABASE_URL="postgres://reckonai:devpassword@localhost:5432/reckonai"'
)


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The configured database URL, driver normalised by Settings."""
    if not os.environ.get("DATABASE_URL"):
        if _running_in_ci():
            pytest.fail(_NO_URL_MESSAGE)
        pytest.skip(_NO_URL_MESSAGE, allow_module_level=True)

    get_settings.cache_clear()
    return get_settings().sqlalchemy_url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """An unpooled engine, guarded on the server's major version.

    The guard lives here rather than in an autouse fixture on purpose: a test
    that needs no database - the conventions gate, for instance - must still run
    when there is none, so the check attaches to the engine rather than to the
    session.
    """
    engine = create_short_lived_engine(database_url)

    found = server_major_version(engine)
    if found != REQUIRED_SERVER_MAJOR:
        engine.dispose()
        pytest.fail(
            f"Connected to PostgreSQL major version {found}, expected "
            f"{REQUIRED_SERVER_MAJOR}. Something other than the compose "
            "container is probably bound to this port - check "
            "`brew services list` for a running postgresql@17 or @18. "
            "Testing against the wrong major version would defeat the "
            "local/CI/Render parity that requirements.md D3 depends on."
        )

    yield engine
    engine.dispose()
