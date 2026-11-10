"""The investigation agent's configuration: provider, model, and the keys.

Block E, Group 1. The model and the provider live in settings rather than in
code (specs/tech-stack.md section 5, amended twice on 2026-11-10) so that
Block G's eval can score both models and both providers on the same six seeded
faults with no code change. These tests pin the defaults and the one pairing
mistake that would corrupt the audit trail rather than crash.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from reckonai.config import ConfigurationError, Settings

DATABASE_URL = "postgresql+psycopg://user:secret@db.example:5432/reckonai"


def _settings(**overrides: object) -> Settings:
    return Settings(database_url=DATABASE_URL, **overrides)  # type: ignore[arg-type]


def test_the_default_is_haiku_on_anthropic() -> None:
    """The dated 2026-11-10 amendment, pinned.

    Asserted on the settings object rather than on a constant in the agent
    package, because the whole point of the amendment is that no code holds
    the model id.
    """
    settings = _settings()

    assert settings.agent_provider == "anthropic"
    assert settings.agent_model == "claude-haiku-5-5"


def test_both_keys_are_optional() -> None:
    """The web service and the worker read this object and make no model call.

    A required key here would make every component refuse to start over a
    credential only the agent uses.
    """
    settings = _settings()

    assert settings.anthropic_api_key is None
    assert settings.openai_api_key is None


def test_the_provider_can_be_switched_with_its_model() -> None:
    settings = _settings(agent_provider="openai", agent_model="gpt-5")

    assert settings.agent_provider == "openai"
    assert settings.agent_model == "gpt-5"


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        # The mistake that actually happens: switch the provider, forget the
        # model. The request would reach OpenAI while the stored audit trail
        # named a Claude model.
        ("openai", "claude-haiku-5-5"),
        ("anthropic", "gpt-5"),
    ],
)
def test_a_provider_paired_with_another_providers_model_is_refused(
    provider: str, model: str
) -> None:
    """specs/tech-stack.md section 5.2 requires the model id to be compared.

    That comparison is meaningless if the two settings can disagree, so the
    disagreement is refused at construction rather than discovered in a
    persisted report.
    """
    with pytest.raises(ValidationError):
        _settings(agent_provider=provider, agent_model=model)


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(agent_provider="gemini", agent_model="gemini-3")


# ---------------------------------------------------------------------------
# The key is asserted at the point of use, not at startup
# ---------------------------------------------------------------------------


def test_the_selected_providers_key_is_returned() -> None:
    settings = _settings(
        anthropic_api_key=SecretStr("sk-ant-test"),
        openai_api_key=SecretStr("sk-openai-test"),
    )

    assert settings.require_agent_api_key().get_secret_value() == "sk-ant-test"


def test_the_other_providers_key_does_not_satisfy_the_requirement() -> None:
    """A set OPENAI_API_KEY must not make an Anthropic run look configured.

    Worth pinning because both fields being present and optional makes this
    the plausible failure: the run would reach the client with no credential
    and fail somewhere less legible.
    """
    settings = _settings(
        agent_provider="anthropic", openai_api_key=SecretStr("sk-openai-test")
    )

    with pytest.raises(ConfigurationError) as excinfo:
        settings.require_agent_api_key()

    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_a_missing_key_names_the_variable_and_the_provider() -> None:
    settings = _settings(agent_provider="openai", agent_model="gpt-5")

    with pytest.raises(ConfigurationError) as excinfo:
        settings.require_agent_api_key()

    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "openai" in message


def test_the_key_stays_redacted_in_a_repr() -> None:
    """A credential must not reach a log through a settings dump."""
    settings = _settings(anthropic_api_key=SecretStr("sk-ant-do-not-log"))

    assert "sk-ant-do-not-log" not in repr(settings)
    assert "sk-ant-do-not-log" not in str(settings)
