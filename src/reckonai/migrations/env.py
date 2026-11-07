"""Alembic environment.

Two decisions here are load-bearing and both differ from the generated template.

The database URL comes from `reckonai.config.Settings`, never from
`alembic.ini`. One source of truth for which database a migration runs against,
and it is the same source the application itself reads - including the driver
normalisation, so a migration cannot connect through a different DBAPI than the
service does.

The engine is unpooled. A migration is a short-lived process that opens one
connection and exits, and the connection budget in specs/tech-stack.md section
11 counts a pre-deploy migration against the same cap as the web service.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from reckonai.config import get_settings
from reckonai.db import create_short_lived_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Phase 1 has no tables, so there is nothing to autogenerate against and
# `alembic revision --autogenerate` would produce an empty revision either way.
# Phase 2 introduces the first declarative Base (Money, tenant, seller, order,
# order_item, commission_rule) and sets this to that `Base.metadata`, which is
# what turns autogenerate on.
target_metadata = None


def _database_url() -> str:
    return get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    """Emit SQL without connecting, for reviewing a migration before it runs."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    engine = create_short_lived_engine(_database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
