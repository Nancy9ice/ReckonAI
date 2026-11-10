"""The OpenAI provider: its own tool loop and its own configuration.

`requirements.md` D12. **specs/tech-stack.md section 5.1 does not apply here**,
and that is recorded in section 5.1 itself rather than left to inference: every
row of that table was derived from real Claude API behaviour, and several are
400s on that API rather than preferences. Adaptive thinking, `output_config`,
`budget_tokens` and the preserved-thinking check have no equivalents on this
API. This provider's configuration is recorded in
`specs/2026-11-10-investigation-agent/validation.md` section 3a.

What *does* transfer is every section 5.2 audit constraint, because those are
properties of an auditable financial product rather than of an SDK:

- the loop is **append-only** - each assistant turn and each tool result is
  appended, and nothing earlier is ever rewritten or dropped
- the **finish reason is read before any content**, so a content-filtered
  response is never processed as a report
- the **model id from the response** is returned for comparison against the one
  requested
- the structured report is requested through strict JSON schema, so a provider
  that cannot produce the shared shape fails rather than returning a looser one

**There is no fallback to or from the Anthropic provider.** A refusal ends the
investigation (section 5's decision, unchanged by D12).

**No reasoning summary is available.** The Chat Completions API returns none,
so `summarized_reasoning` is `None` on this path and section 5.2's "reasoning
must be recorded" is satisfied only in the weaker sense that there is nothing
to record. That is a real difference between the providers, stated rather than
hidden, and it is one of the things Block G's comparison has to account for.
"""

from __future__ import annotations

import copy
import json
from typing import Any, cast

from openai import OpenAI

from reckonai.agent.providers.base import (
    ITERATION_CEILING,
    ProviderError,
    ProviderResult,
)
from reckonai.agent.registry import ToolSpec
from reckonai.agent.report import InvestigationReport

#: As `reckonai.models.investigation.AGENT_PROVIDERS` spells it.
NAME = "openai"

#: The same runaway guard the Anthropic path uses, for the same reason: an
#: append-only loop that never concluded would grow until the request failed.
MAX_ITERATIONS = 40

#: Finish reasons that mean the model declined.
_REFUSAL_FINISH_REASONS: frozenset[str] = frozenset({"content_filter"})


def _as_function_tool(spec: ToolSpec) -> dict[str, Any]:
    """One registry entry as an OpenAI function tool.

    `strict: True` with the registry's `additionalProperties: false` schema is
    this API's equivalent of the Anthropic path's strict tool use - it is what
    keeps a hallucinated argument from reaching a financial recompute.
    """
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema(),
            "strict": True,
        },
    }


def _inlined(node: Any, definitions: dict[str, Any]) -> Any:
    """One JSON Schema node with every `$ref` resolved in place.

    **This exists because of a real 400 from this API**, not defensively:

        Invalid schema for response_format 'investigation_report':
        context=('properties', 'verdict'), $ref cannot have keywords
        {'description'}

    Pydantic emits an enum field with a docstring as a `$ref` carrying a
    sibling `description`, which is valid JSON Schema and which OpenAI's
    strict mode rejects. Inlining the definition and merging the siblings over
    it produces the same contract in a shape this API accepts.

    The alternative was changing `reckonai.agent.report` - flattening the
    enum to a `Literal`, or dropping the field descriptions. Both were
    rejected: the report model is the seam between the two providers, and
    bending it to one provider's schema dialect is how the two stop being
    comparable. The adaptation belongs in the adapter.

    Siblings win over the definition, so a field's own `description` survives
    rather than being replaced by the enum's. Not recursion-safe for a
    self-referential model; this one is a fixed three levels deep, and a
    recursive report would need a different approach anyway since strict mode
    forbids unbounded nesting.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            resolved = _inlined(copy.deepcopy(definitions[name]), definitions)
            resolved.update({k: v for k, v in node.items() if k != "$ref"})
            return resolved
        return {
            key: _inlined(value, definitions)
            for key, value in node.items()
            if key != "$defs"
        }
    if isinstance(node, list):
        return [_inlined(item, definitions) for item in node]
    return node


def strict_report_schema() -> dict[str, Any]:
    """The shared report as a schema this API's strict mode accepts.

    Exported rather than private so `tests/test_agent_providers.py` can assert
    the shape directly - the stub accepts any schema, so nothing but the live
    call or this test would catch a dialect violation.
    """
    schema = InvestigationReport.model_json_schema()
    inlined: dict[str, Any] = _inlined(schema, schema.get("$defs", {}))
    return inlined


def _response_format() -> dict[str, Any]:
    """The shared report as a strict JSON schema.

    Generated from the Pydantic model rather than hand-written, so the two
    providers are provably asked for the same shape - which is the whole
    premise of comparing them.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "investigation_report",
            "schema": strict_report_schema(),
            "strict": True,
        },
    }


class OpenAIProvider:
    """Runs an investigation through a hand-written, append-only tool loop.

    Hand-written because this SDK has no equivalent of the Anthropic beta Tool
    Runner. The loop is deliberately small and does exactly three things:
    append the assistant turn, run the requested tools, append their results.
    """

    name = NAME

    def __init__(self, api_key: str, *, client: OpenAI | None = None) -> None:
        self._client = client or OpenAI(api_key=api_key)

    def investigate(
        self,
        *,
        system: str,
        user: str,
        tools: tuple[ToolSpec, ...],
        model: str,
    ) -> ProviderResult:
        by_name = {spec.name: spec for spec in tools}
        schemas = [_as_function_tool(spec) for spec in tools]

        # The conversation. **Append-only**: entries are added and never
        # edited or removed, which is section 5.2's constraint and also the
        # reason a tool result is never trimmed to save tokens.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        model_returned: str | None = None
        finish_reason: str | None = None

        for turn in range(1, MAX_ITERATIONS + 1):
            try:
                # Cast at the boundary rather than scattering per-argument
                # ignores: the SDK's parameter types are large TypedDict
                # unions, and the registry already guarantees the shape -
                # every property a required string, `additionalProperties`
                # false. `tests/test_agent_providers.py` asserts the request
                # the stub receives, which is the check that actually holds
                # this honest.
                response = self._client.chat.completions.create(
                    model=model,
                    messages=cast(Any, messages),
                    tools=cast(Any, schemas),
                    tool_choice="auto",
                    response_format=cast(Any, _response_format()),
                )
            except Exception as error:  # noqa: BLE001
                raise ProviderError(f"the OpenAI call failed: {error}") from error

            model_returned = getattr(response, "model", None)
            choice = response.choices[0]
            finish_reason = choice.finish_reason

            # **The finish reason is read before any content.**
            if finish_reason in _REFUSAL_FINISH_REASONS:
                return ProviderResult(
                    report=None,
                    provider=NAME,
                    model_requested=model,
                    model_returned=model_returned,
                    stop_reason=finish_reason,
                    summarized_reasoning=None,
                    turns=turn,
                )

            # **A structured-output refusal arrives on `message.refusal`**,
            # not as a finish reason: `finish_reason` is "stop", `content` is
            # None, and the explanation is in that field. Read before any
            # content, which is what §5.2 requires - without this the row was
            # stored as a generic failure with `refused` False and the
            # refusal text discarded, indistinguishable from an unparseable
            # response. Found by code review.
            refusal = getattr(choice.message, "refusal", None)
            if refusal:
                return ProviderResult(
                    report=None,
                    provider=NAME,
                    model_requested=model,
                    model_returned=model_returned,
                    stop_reason="refusal",
                    summarized_reasoning=str(refusal),
                    turns=turn,
                )

            calls = choice.message.tool_calls or []
            if not calls:
                return ProviderResult(
                    report=_parsed_report(choice.message.content),
                    provider=NAME,
                    model_requested=model,
                    model_returned=model_returned,
                    stop_reason=finish_reason,
                    summarized_reasoning=None,
                    turns=turn,
                )

            messages.append(choice.message.model_dump(exclude_none=True))

            for call in calls:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": _run_tool(by_name, call),
                    }
                )

        # The loop hit its ceiling without concluding. Recorded as a failed
        # investigation with the last finish reason: there is no report, and
        # assembling one from a truncated conversation would present an
        # unfinished investigation as a conclusion.
        return ProviderResult(
            report=None,
            provider=NAME,
            model_requested=model,
            model_returned=model_returned,
            # The same vocabulary the Anthropic path reaches for the same
            # outcome - see `providers/base.py` ITERATION_CEILING. Block G
            # compares this column across providers, so the two must not
            # spell one outcome two ways.
            stop_reason=ITERATION_CEILING,
            summarized_reasoning=None,
            turns=MAX_ITERATIONS,
        )


def _run_tool(by_name: dict[str, ToolSpec], call: Any) -> str:
    """Execute one requested tool, or report the error back as a result.

    An unknown tool or a malformed argument object is returned to the model as
    a tool result rather than raised. Raising would abandon an investigation
    over a recoverable mistake, and the model can correct a named error on the
    next turn - which the append-only loop preserves rather than hides.
    """
    spec = by_name.get(call.function.name)
    if spec is None:
        return json.dumps({"error": f"no such tool: {call.function.name}"})

    try:
        arguments = json.loads(call.function.arguments or "{}")
    except json.JSONDecodeError as error:
        return json.dumps({"error": f"arguments were not valid JSON: {error}"})

    if not isinstance(arguments, dict):
        return json.dumps({"error": "arguments must be a JSON object"})

    try:
        return spec.run_as_json(**arguments)
    except TypeError as error:
        return json.dumps({"error": f"wrong arguments for {spec.name}: {error}"})


def _parsed_report(content: str | None) -> InvestigationReport | None:
    """The report from a strict-JSON response, or None.

    `None` rather than a partially-reconstructed model: a financial report
    assembled from unparseable output is worse than no report, and the caller
    records the investigation as failed.
    """
    if not content:
        return None

    try:
        return InvestigationReport.model_validate_json(content)
    except ValueError:
        return None
