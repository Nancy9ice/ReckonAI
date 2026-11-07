"""The baseline migration applies and reverses.

A first revision that cannot be reversed is a first revision that was never
tested, and every later phase would inherit that.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    # env.py reads the URL from Settings, so nothing is set on the Config here.
    # That is the point of leaving sqlalchemy.url empty in alembic.ini.
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def at_base(alembic_config: Config) -> Iterator[Config]:
    """Run each test from a known-empty state, and leave the database at head."""
    command.downgrade(alembic_config, "base")
    yield alembic_config
    command.upgrade(alembic_config, "head")


def _version_rows(engine: Engine) -> list[str]:
    query = text("SELECT version_num FROM alembic_version")
    with engine.connect() as connection:
        return [row[0] for row in connection.execute(query)]


def test_upgrade_applies_the_head_revision(
    at_base: Config, engine: Engine
) -> None:
    command.upgrade(at_base, "head")

    head = ScriptDirectory.from_config(at_base).get_current_head()
    assert _version_rows(engine) == [head]


def test_upgrade_creates_no_table_but_its_own(
    at_base: Config, engine: Engine
) -> None:
    """The baseline is empty by design; Phase 2 adds the first real table."""
    command.upgrade(at_base, "head")

    with engine.connect() as connection:
        tables = sorted(
            row[0]
            for row in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
        )

    assert tables == ["alembic_version"]


def test_downgrade_reverses_the_baseline(at_base: Config, engine: Engine) -> None:
    """Alembic keeps `alembic_version` across a downgrade and empties it.

    Asserting an empty schema here would fail against correct behaviour - the
    table survives `downgrade base` by design, and only the row is removed.
    """
    command.upgrade(at_base, "head")
    command.downgrade(at_base, "base")

    assert _version_rows(engine) == []


def test_the_cycle_is_repeatable(at_base: Config, engine: Engine) -> None:
    head = ScriptDirectory.from_config(at_base).get_current_head()

    command.upgrade(at_base, "head")
    command.downgrade(at_base, "base")
    command.upgrade(at_base, "head")

    assert _version_rows(engine) == [head]
