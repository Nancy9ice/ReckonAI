"""Natural-language control of the queue grid, through the configured provider.

`specs/2026-11-10-queue-approval-dashboard/requirements.md` D11.

The browser asks AG Grid's AI Toolkit for the grid's JSON schema and current
state, and sends them here with the user's sentence. This module asks the
model to return a new grid *state*: filters, sorting, grouping, which columns
show. **It changes how the queue is displayed and nothing else.** No amount is
sent for the model to reason about, none is returned, and no record is read or
written - `mission.md` P1 and P6 hold for this feature by construction.

The provider is the one `settings.agent_provider` selects, and there is no
failover: an outage is an error the user sees, as everywhere else in the
product.

The credential is read here, on the server. It never reaches the browser.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from reckonai.config import Settings

#: What the toolkit's `setState` accepts for `propertiesToIgnore`.
PROPERTIES: Final = (
    "aggregation",
    "filter",
    "sort",
    "pivot",
    "columnVisibility",
    "columnSizing",
    "rowGroup",
)

MAX_TOKENS = 4_000

SYSTEM = """\
You change how a table is displayed. The user types a sentence; you answer with
the grid state that does what they asked.

You are given the grid's JSON schema, its current state, and the sentence.
Reply with ONE JSON object and nothing else - no prose, no code fence - with
exactly these keys:
  "gridState": the new state, valid against the schema you were given, holding
      only what must change;
  "propertiesToIgnore": the state properties you did NOT change, chosen from
      aggregation, filter, sort, pivot, columnVisibility, columnSizing, rowGroup;
  "explanation": one short sentence, in plain words, saying what you did.

Rules. Change the display only. Never invent a column or a value that is not in
the schema. If the request cannot be done with the schema, return an empty
gridState, list every property in propertiesToIgnore, and say why in the
explanation. Do not do arithmetic and do not state any amount of money.
"""


class GridAssistantError(RuntimeError):
    """The model did not return a usable grid state."""


def _user_turn(request: str, state: dict[str, Any], schema: dict[str, Any]) -> str:
    return json.dumps(
        {"request": request, "currentState": state, "gridStateSchema": schema},
        separators=(",", ":"),
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse(text: str) -> dict[str, Any]:
    """The model's reply as a validated result, or a named error."""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as error:
        raise GridAssistantError("the model did not return JSON") from error

    if not isinstance(data, dict):
        raise GridAssistantError("the model did not return a JSON object")
    state, ignore, why = (
        data.get("gridState"),
        data.get("propertiesToIgnore"),
        data.get("explanation"),
    )
    if not isinstance(state, dict):
        raise GridAssistantError("gridState is missing or not an object")
    if not isinstance(ignore, list) or any(p not in PROPERTIES for p in ignore):
        raise GridAssistantError(
            "propertiesToIgnore is missing or has an unknown entry"
        )
    if not isinstance(why, str):
        raise GridAssistantError("explanation is missing")
    return {"gridState": state, "propertiesToIgnore": ignore, "explanation": why}


def _ask_anthropic(settings: Settings, user: str, client: Any | None) -> str:
    from anthropic import Anthropic, APIError
    from anthropic.types import TextBlock

    api = client or Anthropic(
        api_key=settings.require_agent_api_key().get_secret_value()
    )
    try:
        # No sampling parameters and no `thinking` argument: both are 400s or
        # unnecessary on the current models (specs/tech-stack.md section 5.1).
        # Low effort: this is a lookup-sized task, not an investigation.
        response = api.messages.create(
            model=settings.agent_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
        )
    except APIError as error:
        raise GridAssistantError(f"the Anthropic call failed: {error}") from error

    # The stop reason is read before any content.
    if response.stop_reason == "refusal":
        raise GridAssistantError("the model declined this request")
    if response.stop_reason == "max_tokens":
        raise GridAssistantError("the model's answer was cut off")
    return "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    )


def _ask_openai(settings: Settings, user: str, client: Any | None) -> str:
    from openai import OpenAI, OpenAIError

    key = settings.require_agent_api_key().get_secret_value()
    api = client or OpenAI(api_key=key)
    try:
        response = api.chat.completions.create(
            model=settings.agent_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
        )
    except OpenAIError as error:
        raise GridAssistantError(f"the OpenAI call failed: {error}") from error

    choice = response.choices[0]
    if choice.finish_reason in ("content_filter", "length"):
        raise GridAssistantError(f"the model stopped early: {choice.finish_reason}")
    return choice.message.content or ""


def ask(
    settings: Settings,
    request: str,
    state: dict[str, Any],
    schema: dict[str, Any],
    *,
    client: Any | None = None,
) -> dict[str, Any]:
    """One sentence in, one validated grid state out."""
    user = _user_turn(request, state, schema)
    if settings.agent_provider == "anthropic":
        text = _ask_anthropic(settings, user, client)
    else:
        text = _ask_openai(settings, user, client)
    return parse(text)
