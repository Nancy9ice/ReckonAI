"""The two providers, and how one is selected.

`requirements.md` D12. `settings.agent_provider` chooses; one investigation uses
one provider start to finish. There is **no fallback between them** - a refusal
ends the investigation and is reported as failed, which is
specs/tech-stack.md section 5's decision and is not reversed by a second
provider being available.
"""

from __future__ import annotations

from reckonai.agent.providers.anthropic import AnthropicProvider
from reckonai.agent.providers.base import (
    Provider,
    ProviderError,
    ProviderResult,
)
from reckonai.agent.providers.openai import OpenAIProvider
from reckonai.config import Settings

__all__ = [
    "AnthropicProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "ProviderResult",
    "provider_for",
]


def provider_for(settings: Settings) -> Provider:
    """The provider the settings select, with its credential attached.

    The key is required here rather than at import, through
    `require_agent_api_key`, which raises a `ConfigurationError` naming the
    missing variable. That is the point at which a key is genuinely needed -
    the web service and the worker read the same `Settings` and make no model
    call.
    """
    api_key = settings.require_agent_api_key().get_secret_value()

    if settings.agent_provider == "anthropic":
        return AnthropicProvider(api_key)
    return OpenAIProvider(api_key)
