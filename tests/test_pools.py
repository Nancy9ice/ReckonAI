"""The connection budget, asserted rather than claimed.

specs/tech-stack.md section 11: three Render components share one Postgres
instance. The cap is 100, flat across every Basic tier, less the 10 Render
reserves, so the application's budget is 90.

At 90 usable connections, SQLAlchemy's defaults would in fact fit - three
components at 15 each is 45. So the check that carries the weight is not the
arithmetic below but `test_pool_parameters_are_required`: the factory refuses to
build without them, which makes a default impossible rather than merely
unlikely. The budget test stays because it is nearly free and because it fails
if nobody records a cap at all, which is a real way to lose track of this before
Phase 9 adds concurrency.
"""

from __future__ import annotations

import pytest

from reckonai.app import WEB_MAX_OVERFLOW, WEB_POOL_SIZE
from reckonai.db import (
    POSTGRES_CONNECTION_CAP,
    POSTGRES_RESERVED_CONNECTIONS,
    USABLE_CONNECTIONS,
    create_app_engine,
    create_short_lived_engine,
)

_DUMMY_URL = "postgresql+psycopg://user:pass@127.0.0.1:5432/none"


def test_pool_parameters_are_required() -> None:
    """A component that does not state its share of the budget cannot start."""
    with pytest.raises(TypeError):
        create_app_engine(_DUMMY_URL)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        create_app_engine(_DUMMY_URL, pool_size=5)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        create_app_engine(_DUMMY_URL, max_overflow=5)  # type: ignore[call-arg]


def test_pool_parameters_are_validated() -> None:
    with pytest.raises(ValueError):
        create_app_engine(_DUMMY_URL, pool_size=0, max_overflow=5)

    with pytest.raises(ValueError):
        create_app_engine(_DUMMY_URL, pool_size=5, max_overflow=-1)


def test_the_web_engine_claims_the_declared_allocation() -> None:
    engine = create_app_engine(
        _DUMMY_URL, pool_size=WEB_POOL_SIZE, max_overflow=WEB_MAX_OVERFLOW
    )
    try:
        assert engine.pool.size() == WEB_POOL_SIZE
        # An engine is built lazily, so this asserts configuration rather than
        # live connections; nothing above opens a socket.
        assert engine.pool._max_overflow == WEB_MAX_OVERFLOW
    finally:
        engine.dispose()


def test_a_short_lived_engine_holds_no_pool() -> None:
    """A cron invocation or a migration opens one connection and exits."""
    engine = create_short_lived_engine(_DUMMY_URL)
    try:
        assert type(engine.pool).__name__ == "NullPool"
    finally:
        engine.dispose()


def test_the_declared_budget_fits_the_plan() -> None:
    assert USABLE_CONNECTIONS == POSTGRES_CONNECTION_CAP - POSTGRES_RESERVED_CONNECTIONS

    # The worker's allocation joins this sum in Group 5, which is where
    # worker.py and its constants arrive.
    web_peak = WEB_POOL_SIZE + WEB_MAX_OVERFLOW

    assert web_peak < USABLE_CONNECTIONS
