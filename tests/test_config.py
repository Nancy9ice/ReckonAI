"""Configuration: driver normalisation, redaction, and failing loudly.

The driver normalisation is the one here that earns its keep. Render's
`fromDatabase` hands over a scheme SQLAlchemy cannot use as given, and getting
it wrong is an opaque startup failure on a deployed service rather than
something a local run would ever show.
"""

from __future__ import annotations

import pytest

from reckonai.config import ConfigurationError, Settings, get_settings


@pytest.mark.parametrize(
    "supplied",
    [
        # What Render's connectionString actually looks like. SQLAlchemy 2.0
        # rejects "postgres://" outright, and plain "postgresql://" selects
        # psycopg2 - which this project does not install.
        "postgres://user:secret@db.example:5432/reckonai",
        "postgresql://user:secret@db.example:5432/reckonai",
        # Already normalised; must pass through unchanged.
        "postgresql+psycopg://user:secret@db.example:5432/reckonai",
    ],
)
def test_the_driver_is_normalised_to_psycopg(supplied: str) -> None:
    settings = Settings(database_url=supplied)

    assert settings.sqlalchemy_url.startswith("postgresql+psycopg://")
    # Normalising the scheme must not disturb anything after it.
    assert settings.sqlalchemy_url.endswith("user:secret@db.example:5432/reckonai")


def test_an_unrecognised_scheme_is_refused() -> None:
    with pytest.raises(ValueError):
        Settings(database_url="mysql://user:secret@db.example/reckonai")


def test_the_url_is_redacted_in_repr_and_str() -> None:
    settings = Settings(database_url="postgres://user:hunter2@db.example/reckonai")

    for rendered in (repr(settings), str(settings)):
        assert "hunter2" not in rendered
        assert "<redacted>" in rendered


def test_a_missing_database_url_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No default, and no localhost fallback.

    A fallback would let a deployed service start against the wrong database and
    fail somewhere further away from the cause.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    with pytest.raises(ConfigurationError) as excinfo:
        get_settings()

    assert "DATABASE_URL" in str(excinfo.value)
    get_settings.cache_clear()


def test_the_render_external_url_is_read_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render injects this into web services; it is absent everywhere else."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/reckonai")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://reckonai-api.onrender.com")
    get_settings.cache_clear()

    assert get_settings().render_external_url == "https://reckonai-api.onrender.com"
    get_settings.cache_clear()


def test_the_render_external_url_is_none_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/reckonai")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    get_settings.cache_clear()

    assert get_settings().render_external_url is None
    get_settings.cache_clear()
