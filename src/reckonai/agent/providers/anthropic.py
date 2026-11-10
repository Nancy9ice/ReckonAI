"""The Anthropic provider: the beta Tool Runner, configured per section 5.1.

specs/tech-stack.md section 5.1 is reproduced here as code, and its rows are
not preferences - several are 400s on this model, verified against real API
behaviour and recorded in that table with the reasoning. The ones that bite:

- **adaptive thinking with `display` set explicitly.** The default is
  `"omitted"`, which returns thinking blocks with empty text - and section 5.2
  requires the reasoning to be recorded. A default here would quietly produce
  an empty audit field.
- **`budget_tokens` is a 400** on Haiku 5.5. Adaptive thinking replaced it.
- **no sampling parameters at all.** A non-default `temperature`, `top_p` or
  `top_k` returns 400 here.
- **`effort` set explicitly**, although `medium` is also this model's default,
  so the value is auditable and survives a change of default.
- **`max_tokens=64000` with streaming.** An unset value is a latent truncation
  failure, and a value this large needs streaming to avoid an HTTP timeout.
- **eager input streaming off.** Our tool inputs are record references; it buys
  nothing and shifts validation to us, where a tolerant parser can return a
  silently truncated input. Unacceptable on a tool driving a financial
  recompute.
- **no refusal fallback.** Haiku 5.5 has no server-side equivalent, and
  section 5 decided a refusal is reported as failed rather than papered over.

**The loop is append-only.** `runner.until_done()` appends; nothing here
compacts, edits context or trims an earlier tool result. The runner exposes
`compact_before_next_turn`, and this module deliberately never calls it -
trimming a tool result would be deleting the evidence trail to save tokens.
"""

from __future__ import annotations

from typing import Any, Literal

from anthropic import Anthropic, beta_tool
from anthropic.lib.tools import BetaFunctionTool
from anthropic.types.beta import (
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaThinkingConfigAdaptiveParam,
    BetaToolChoiceAutoParam,
)

from reckonai.agent.providers.base import (
    ITERATION_CEILING,
    ProviderError,
    ProviderResult,
)
from reckonai.agent.registry import ToolSpec
from reckonai.agent.report import InvestigationReport

#: As `reckonai.models.investigation.AGENT_PROVIDERS` spells it.
NAME = "anthropic"

#: section 5.1. Streaming is required at this `max_tokens`.
MAX_TOKENS = 64_000

#: Set explicitly even though it matches this model's default, so the value is
#: auditable. section 5.2's granularity watch-item is about this setting: if
#: cited ids thin out while conclusions stay confident, Block G raises it to
#: `high` and re-measures.
EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

#: A ceiling on tool turns. Not an audit requirement - a runaway guard. The
#: loop is append-only, so an investigation that never concluded would grow
#: its own context until the request failed; failing at a known bound is more
#: legible than failing at the context limit.
MAX_ITERATIONS = 40


def _as_beta_tool(spec: ToolSpec) -> BetaFunctionTool[Any]:
    """One registry entry as a Tool Runner tool.

    Built programmatically from the registry rather than written as a
    decorated function per tool, so both providers receive provably the same
    name, description and schema (`registry.py`).

    `strict=True` guarantees schema-valid arguments, which matters more here
    than usual: `tool_choice` is `auto` (section 5.1), so the schema is what
    keeps a hallucinated argument from reaching a financial recompute.
    """

    def run(**arguments: Any) -> str:
        return spec.run_as_json(**arguments)

    run.__name__ = spec.name
    run.__doc__ = spec.description

    return beta_tool(
        run,
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema(),
        strict=True,
        # Off, deliberately. See the module docstring.
        eager_input_streaming=False,
    )


class AnthropicProvider:
    """Runs an investigation through `client.beta.messages.tool_runner`."""

    name = NAME

    def __init__(self, api_key: str, *, client: Anthropic | None = None) -> None:
        # The client is injectable so tests drive a stub through the same code
        # path the real call takes. Nothing in the suite constructs a real
        # client, and no test makes an API call.
        self._client = client or Anthropic(api_key=api_key)

    def investigate(
        self,
        *,
        system: str,
        user: str,
        tools: tuple[ToolSpec, ...],
        model: str,
    ) -> ProviderResult:
        """One investigation, driven to completion.

        The stop reason is read before any content, and a refusal returns a
        result with no report rather than raising - a refusal is an outcome,
        not an error.
        """
        # Declared with the SDK's own parameter types rather than as bare
        # dicts. These four values are the audit-critical configuration of
        # specs/tech-stack.md section 5.1, and annotating them means mypy
        # checks each against the API's actual shape - a bare dict infers as
        # `dict[str, str]` and type-checks against nothing.
        system_blocks: list[BetaTextBlockParam] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        # `display` explicit: the default is "omitted", which returns thinking
        # blocks with empty text, and section 5.2 requires the reasoning to be
        # recorded.
        thinking: BetaThinkingConfigAdaptiveParam = {
            "type": "adaptive",
            "display": "summarized",
        }
        output_config: BetaOutputConfigParam = {"effort": EFFORT}
        # `auto` plus `strict` per tool. Forced tool choice is not used.
        tool_choice: BetaToolChoiceAutoParam = {"type": "auto"}

        try:
            runner = self._client.beta.messages.tool_runner(
                model=model,
                max_tokens=MAX_TOKENS,
                # Cached: identical across every investigation. The exception
                # under investigation is in the user turn, after the prefix,
                # because caching is a prefix match.
                system=system_blocks,
                messages=[{"role": "user", "content": user}],
                tools=[_as_beta_tool(spec) for spec in tools],
                thinking=thinking,
                # Two separate parameters. `output_config` carries the effort;
                # the Pydantic report goes through `output_format`.
                #
                # **`output_format` is marked deprecated by the SDK's own error
                # text, and it is still the only route to a parsed report.**
                # Verified against the installed source rather than assumed
                # (code review finding 14): `parsed_output` is produced by
                # `parse_text(content.text, output_format)`, and
                # `output_config["format"]` accepts only a raw JSON-schema
                # dict, with no path from it to a parsed model.
                #
                # The alternative is to send a hand-built schema and
                # `model_validate_json` the text, as the OpenAI adapter does.
                # That was not done blind: Anthropic's structured outputs
                # restrict schema features (this report's `confidence` carries
                # `minimum`/`maximum`, which the SDK's private transform
                # rewrites), and the default provider has had no live call.
                # When the Anthropic account is topped up, try that migration
                # against the real API - see validation.md section 8 debt 6.
                output_config=output_config,
                output_format=InvestigationReport,
                tool_choice=tool_choice,
                max_iterations=MAX_ITERATIONS,
                stream=True,
            )
            # **Iterate the runner rather than calling `until_done()` blind.**
            # `until_done()` returns only the last message, which in a
            # multi-turn tool loop is the turn that formats the report - so
            # `summarized_reasoning` recorded the least informative part of the
            # run, and every earlier turn's thinking (where the agent decided
            # what evidence to retrieve) was dropped. Code review findings 5
            # and 10.
            #
            # Safe, and checked against the SDK source rather than assumed: the
            # streaming runner binds `stream.get_final_message` itself and calls
            # it after each yield, so draining each turn here cannot hang it,
            # and `until_done()` is documented as returning the last message
            # unchanged once the runner has been consumed.
            turn_messages = [stream.get_final_message() for stream in runner]
            message = runner.until_done()
        except Exception as error:  # noqa: BLE001
            # A transport or configuration failure: no response was observed.
            # Deliberately not narrowed to the SDK's exception tree - whatever
            # went wrong, the investigation is recorded as failed with no stop
            # reason rather than claiming a response that never arrived.
            raise ProviderError(f"the Anthropic call failed: {error}") from error

        # **Read the stop reason before the content.** A refusal arrives as a
        # successful response, so inspecting content first would process a
        # refusal as though it were a report.
        stop_reason = getattr(message, "stop_reason", None)
        model_returned = getattr(message, "model", None)
        reasoning = _reasoning_across(turn_messages) or _summarized_thinking(message)

        if stop_reason in {"refusal", "content_filter"}:
            return ProviderResult(
                report=None,
                provider=NAME,
                model_requested=model,
                model_returned=model_returned,
                stop_reason=stop_reason,
                summarized_reasoning=reasoning,
                turns=len(turn_messages),
            )

        report = _parsed_report(message)

        # **The runner stops silently at its ceiling.** It returns the last
        # message, whose own `stop_reason` is "tool_use" and whose
        # `parsed_output` is None - so without this the identical outcome was
        # persisted as "tool_use" here and "max_iterations" on the OpenAI
        # path, and Block G compares that column across providers.
        if report is None and stop_reason == "tool_use":
            stop_reason = ITERATION_CEILING

        return ProviderResult(
            report=report,
            provider=NAME,
            model_requested=model,
            model_returned=model_returned,
            stop_reason=stop_reason,
            summarized_reasoning=reasoning,
            # Counted from the turns actually iterated. section 5.2 asks for a
            # cost proxy and this is the cheapest one; it was 0 on the
            # configured default provider, where it is most needed.
            turns=len(turn_messages),
        )


def _parsed_report(message: Any) -> InvestigationReport | None:
    """The structured report, or None if the response carried none.

    `output_format` makes `until_done()` return a `ParsedBetaMessage`,
    so the validated model is on `parsed_output`. A response that reached
    `max_tokens` can still arrive with nothing parsed, and that is a failed
    investigation rather than something to reconstruct from the text - a
    partially-parsed financial report is worse than none.
    """
    parsed = getattr(message, "parsed_output", None)
    return parsed if isinstance(parsed, InvestigationReport) else None


def _reasoning_across(turn_messages: list[Any]) -> str | None:
    """Every turn's summarized thinking, labelled by turn, or None.

    One turn is returned unlabelled, so the common single-turn case reads as it
    always did. With several, each block is prefixed with its turn number: the
    order in which evidence was requested is what makes the reasoning
    auditable, and a flat concatenation would lose where one turn ended.
    """
    per_turn = [_summarized_thinking(message) for message in turn_messages]
    present = [(n, text) for n, text in enumerate(per_turn, start=1) if text]
    if not present:
        return None
    if len(turn_messages) == 1:
        return present[0][1]
    return "\n\n".join(f"[turn {n}] {text}" for n, text in present)


def _summarized_thinking(message: Any) -> str | None:
    """Every summarized thinking block, joined, or None if there were none.

    Stored as supporting context and never as the authoritative cause: it is a
    summary, not a raw chain of thought, which no model exposes. The cited
    source records are the authority (specs/mission.md P5).
    """
    blocks = [
        block.thinking
        for block in getattr(message, "content", []) or []
        if getattr(block, "type", None) == "thinking"
        and getattr(block, "thinking", None)
    ]
    return "\n\n".join(blocks) if blocks else None
