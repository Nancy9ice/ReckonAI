"""Engine construction, with the connection budget made explicit.

specs/tech-stack.md section 11: three Render components share one Postgres
instance, so pool sizes are set per component rather than left at SQLAlchemy's
defaults. The factory below takes its pool parameters as *required* keyword
arguments, which is what makes a default impossible rather than merely
discouraged - see the phase's validation.md C10.

The cap is 100 connections, flat across every Basic tier, less the 10 Render
reserves for its own use. The allocation each component claims lives next to
that component's entrypoint, not here.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import NullPool

#: Maximum connections the Render Postgres plan permits. Flat across Basic
#: tiers, so this does not move if the instance tier does.
POSTGRES_CONNECTION_CAP = 100

#: Render reserves these for direct and internal use; the application's real
#: budget is the difference.
POSTGRES_RESERVED_CONNECTIONS = 10

#: What the application may actually claim across all components.
USABLE_CONNECTIONS = POSTGRES_CONNECTION_CAP - POSTGRES_RESERVED_CONNECTIONS


def create_app_engine(
    url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout: int = 30,
    pool_pre_ping: bool = True,
) -> Engine:
    """Build a pooled engine for a long-lived component.

    `pool_size` and `max_overflow` have no defaults on purpose: a component that
    does not state its share of the connection budget should not start.

    `pool_pre_ping` defaults on because a managed Postgres closes idle
    connections. Without it a stale pooled connection surfaces as a failed
    request rather than as a transparent reconnect.
    """
    if pool_size < 1:
        raise ValueError("pool_size must be at least 1")
    if max_overflow < 0:
        raise ValueError("max_overflow cannot be negative")

    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=pool_pre_ping,
        future=True,
    )


def create_short_lived_engine(url: str) -> Engine:
    """Build an unpooled engine for a process that opens one connection and exits.

    Used by Render Cron Job invocations and by Alembic. A pool would hold
    connections open on behalf of a process that is about to die.
    """
    return create_engine(url, poolclass=NullPool, future=True)


def check_connection(engine: Engine) -> None:
    """Execute the cheapest possible query, raising if the database is unreachable.

    Deliberately returns nothing: callers decide what a failure means. The
    readiness probe turns it into a 503; a test turns it into a failure.
    """
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def server_major_version(engine: Engine) -> int:
    """Return the connected server's major version, for the parity guard.

    Three Postgres majors are installed on at least one development machine, so
    a stray service could bind 5432 ahead of the compose container and the suite
    would pass against the wrong version, silently. See this phase's notes.md.
    """
    with engine.connect() as connection:
        raw = connection.execute(text("SHOW server_version")).scalar_one()
    return int(str(raw).split(".", 1)[0])
