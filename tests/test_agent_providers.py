"""Both providers, driven by stubs. No test here makes an API call.

Block E, Group 4. What is proven:

  - the Anthropic request carries every specs/tech-stack.md section 5.1 value,
    and **none** of the parameters that return 400 on this model
  - the stop reason is read before any content, on both providers
  - a refusal produces no report rather than raising
  - the model id comes back for comparison, and a mismatch reads as downgraded
  - the loop is append-only
  - **both providers return the same report shape**, which is the premise of
    Block G comparing them

The stub is driven through the real provider classes, so these assert the code
path the live call takes rather than a parallel one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from reckonai.agent.providers.anthropic import (
    EFFORT,
    MAX_TOKENS,
    AnthropicProvider,
)
from reckonai.agent.providers.base import ProviderError, ProviderResult
from reckonai.agent.providers.openai import OpenAIProvider
from reckonai.agent.registry import ToolSpec
from reckonai.agent.report import InvestigationReport, ModelVerdict


class _Raiser:
    """Every attribute access returns something that raises on call.

    One stub for both providers: whichever method the provider reaches for,
    the call fails the way a reset connection does.
    """

    def __getattr__(self, _name: str) -> Any:
        return _Raiser()

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("connection reset")


class _ExplodingAnthropic(_Raiser):
    pass


class _ExplodingOpenAI(_Raiser):
    pass


REPORT = {
    "verdict": "reported",
    "summary": "The refund was never reflected in the seller ledger.",
    "causes": [
        {
            "description": "The ledger is missing the refund debit.",
            "confidence": 0.85,
            "cited_refs": ["ORD-0013", "5TY05013RG002845M"],
        }
    ],
}


@pytest.fixture
def specs() -> tuple[ToolSpec, ...]:
    """A two-tool surface, enough to exercise dispatch and schema shape."""
    return (
        ToolSpec(
            name="recompute",
            description="Run the engine.",
            parameters={},
            call=lambda: [{"code": "missing_ledger_adjustment"}],
        ),
        ToolSpec(
            name="fetch_order",
            description="One order.",
            parameters={"order_ref": "The order reference."},
            call=lambda order_ref: {"order_ref": order_ref},
        ),
    )


# ---------------------------------------------------------------------------
# Anthropic: the request configuration
# ---------------------------------------------------------------------------


class _Block:
    def __init__(self, type_: str, **fields: Any) -> None:
        self.type = type_
        for name, value in fields.items():
            setattr(self, name, value)


class _Message:
    def __init__(
        self,
        *,
        stop_reason: str | None = "end_turn",
        model: str | None = "claude-haiku-5-5",
        parsed: InvestigationReport | None = None,
        content: list[Any] | None = None,
    ) -> None:
        self.stop_reason = stop_reason
        self.model = model
        self.parsed_output = parsed
        self.content = content or []


class _Stream:
    """One turn's stream, as the streaming tool runner yields it."""

    def __init__(self, message: _Message) -> None:
        self._message = message

    def get_final_message(self) -> _Message:
        return self._message


class _Runner:
    """Iterable of per-turn streams, ending with `message`.

    **Shaped like the real streaming runner**, which yields one
    `BetaMessageStream` per turn and returns the last message from
    `until_done()`. The earlier stub implemented `until_done()` alone, so it
    could not distinguish a provider that reads every turn from one that reads
    only the last - which is exactly the defect code review found (finding 5).
    """

    def __init__(
        self, message: _Message, earlier: list[_Message] | None = None
    ) -> None:
        self._message = message
        self._turns = [*(earlier or []), message]
        self.compacted = False

    def __iter__(self) -> Any:
        return iter(_Stream(turn) for turn in self._turns)

    def until_done(self) -> _Message:
        return self._message

    def compact_before_next_turn(self) -> None:
        # Recorded rather than implemented. If the provider ever called this,
        # the append-only test below would catch it.
        self.compacted = True


class _StubAnthropic:
    """Captures the request and returns a prepared message."""

    def __init__(
        self, message: _Message, earlier: list[_Message] | None = None
    ) -> None:
        self.request: dict[str, Any] = {}
        self._message = message
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs: Any) -> _Runner:
                outer.request = kwargs
                return outer.runner

        class _Beta:
            messages = _Messages()

        self.runner = _Runner(message, earlier)
        self.beta = _Beta()


def _anthropic(
    message: _Message, earlier: list[_Message] | None = None
) -> tuple[AnthropicProvider, _StubAnthropic]:
    stub = _StubAnthropic(message, earlier)
    return AnthropicProvider("unused", client=stub), stub  # type: ignore[arg-type]


def test_the_request_carries_every_section_5_1_value(
    specs: tuple[ToolSpec, ...],
) -> None:
    """section 5.1 as a table, asserted as a request.

    Each row of that table was derived from real API behaviour, so this is a
    regression test against someone 'tidying' a value that looks redundant -
    `effort` matching the model default is the one most likely to be removed.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )
    request = stub.request

    assert request["max_tokens"] == MAX_TOKENS == 64_000
    assert request["stream"] is True
    assert request["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert request["output_config"] == {"effort": EFFORT}
    assert EFFORT == "medium"
    assert request["output_format"] is InvestigationReport
    assert request["tool_choice"] == {"type": "auto"}
    assert request["model"] == "claude-haiku-5-5"


def test_the_request_sends_no_parameter_that_returns_400(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Sampling parameters and `budget_tokens` are 400s on this model.

    Also asserts no `fallbacks` or refusal-fallback beta: Haiku 5.5 has no
    server-side equivalent, and section 5 decided a refusal is reported as
    failed rather than papered over.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )
    request = stub.request

    for forbidden in ("temperature", "top_p", "top_k", "fallbacks"):
        assert forbidden not in request, forbidden

    assert "budget_tokens" not in request["thinking"]


def test_the_system_prompt_is_cached_and_the_exception_is_not(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Caching is a prefix match, so the volatile part goes after it.

    The exception under investigation belongs in the user turn. Interpolating
    it into the cached system prompt would forfeit the cache on every single
    investigation, which is the whole saving section 5.1 is claiming.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="ORD-0013 is wrong", tools=specs, model="claude-haiku-5-5"
    )
    request = stub.request

    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert request["system"][0]["text"] == "SYSTEM"
    assert "ORD-0013" not in request["system"][0]["text"]
    assert request["messages"] == [{"role": "user", "content": "ORD-0013 is wrong"}]


def test_eager_input_streaming_is_off_on_every_tool(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Our tool inputs are record references; eager streaming buys nothing.

    It would shift validation to us, where a tolerant parser can return a
    silently truncated input - unacceptable on a tool that drives a financial
    recompute.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    for tool in stub.request["tools"]:
        assert tool.to_dict().get("eager_input_streaming") in (False, None)


def test_every_tool_schema_is_strict_and_string_only(
    specs: tuple[ToolSpec, ...],
) -> None:
    """specs/mission.md P1, visible in the wire format.

    No property is anything but a string, so the model cannot pass a figure
    into a tool at all.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    for tool in stub.request["tools"]:
        schema = tool.input_schema
        assert schema["additionalProperties"] is False
        for prop in schema["properties"].values():
            assert prop["type"] == "string", schema


def test_the_loop_is_never_compacted(specs: tuple[ToolSpec, ...]) -> None:
    """section 5.2: append-only, no compaction, no trimming.

    The runner exposes `compact_before_next_turn`. Trimming a tool result
    would be deleting the evidence trail to save tokens, so this asserts the
    provider never reaches for it.
    """
    provider, stub = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT))
    )

    provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert stub.runner.compacted is False
    assert "context_management" not in stub.request


# ---------------------------------------------------------------------------
# Anthropic: outcomes
# ---------------------------------------------------------------------------


def test_a_refusal_yields_no_report_and_does_not_raise(
    specs: tuple[ToolSpec, ...],
) -> None:
    """A refusal is an outcome, not an error.

    Haiku 5.5 has no server-side fallback, so a `stop_reason: "refusal"`
    simply stops. It becomes a failed investigation: no report, no cause list,
    nothing presented as a conclusion.
    """
    provider, _ = _anthropic(
        _Message(
            stop_reason="refusal",
            parsed=InvestigationReport.model_validate(REPORT),
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.refused is True
    assert result.report is None
    assert result.stop_reason == "refusal"


def test_a_refusal_discards_a_report_the_response_still_carried(
    specs: tuple[ToolSpec, ...],
) -> None:
    """The stop reason is read **before** the content, and wins.

    The stub above deliberately returns a refusal *and* a parsed report - a
    shape that should never occur. If the provider read content first it would
    file that report as a conclusion, which is exactly the failure the
    read-order rule prevents. Asserted on the discarding, because an
    implementation that happened to check `parsed_output` first would pass a
    weaker test.
    """
    provider, _ = _anthropic(
        _Message(
            stop_reason="refusal",
            parsed=InvestigationReport.model_validate(REPORT),
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.report is None


def test_the_model_id_comes_back_for_comparison(
    specs: tuple[ToolSpec, ...],
) -> None:
    provider, _ = _anthropic(
        _Message(
            model="claude-haiku-5-5",
            parsed=InvestigationReport.model_validate(REPORT),
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.model_requested == "claude-haiku-5-5"
    assert result.model_returned == "claude-haiku-5-5"
    assert result.downgraded is False


def test_a_different_model_in_the_response_reads_as_downgraded(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Nothing in the MVP can produce this, which is why it is stubbed.

    The columns are what *prove* that nothing downgrades silently, and an
    unexercised flag proves nothing. section 5.2: an unflagged downgrade would
    make the audit trail lie.
    """
    provider, _ = _anthropic(
        _Message(
            model="claude-sonnet-5-5",
            parsed=InvestigationReport.model_validate(REPORT),
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.downgraded is True


def test_the_summarized_reasoning_is_collected(
    specs: tuple[ToolSpec, ...],
) -> None:
    """section 5.2: reasoning must be recorded."""
    provider, _ = _anthropic(
        _Message(
            parsed=InvestigationReport.model_validate(REPORT),
            content=[
                _Block("thinking", thinking="Checked the ledger."),
                _Block("text", text="ignored"),
                _Block("thinking", thinking="No refund debit present."),
            ],
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.summarized_reasoning is not None
    assert "Checked the ledger." in result.summarized_reasoning
    assert "No refund debit present." in result.summarized_reasoning
    assert "ignored" not in result.summarized_reasoning


def test_a_response_with_no_parsed_report_is_not_reconstructed(
    specs: tuple[ToolSpec, ...],
) -> None:
    """A truncated financial report is worse than none.

    `max_tokens` can end a turn with nothing parsed. That is a failed
    investigation, not something to rebuild from the text.
    """
    provider, _ = _anthropic(_Message(stop_reason="max_tokens", parsed=None))

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.report is None
    assert result.stop_reason == "max_tokens"
    assert result.refused is False


def test_a_transport_failure_is_a_provider_error(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Distinct from a refusal: we observed no response at all.

    Claiming a stop reason here would assert something that did not happen.
    """

    provider = AnthropicProvider("unused", client=_ExplodingAnthropic())  # type: ignore[arg-type]

    with pytest.raises(ProviderError):
        provider.investigate(
            system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


class _Call:
    def __init__(self, name: str, arguments: str, call_id: str = "call_1") -> None:
        self.id = call_id
        self.function = _Block("function", name=name, arguments=arguments)


class _ChatMessage:
    def __init__(
        self, *, content: str | None = None, tool_calls: list[_Call] | None = None
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, **_: Any) -> dict[str, Any]:
        return {"role": "assistant", "content": self.content}


class _Choice:
    def __init__(self, message: _ChatMessage, finish_reason: str) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Completion:
    def __init__(self, choice: _Choice, model: str = "gpt-5") -> None:
        self.choices = [choice]
        self.model = model


class _StubOpenAI:
    """Returns prepared completions in order, recording each request."""

    def __init__(self, completions: list[_Completion]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._completions = list(completions)
        outer = self

        class _Completions:
            def create(self, **kwargs: Any) -> _Completion:
                # **Snapshot the messages.** The provider appends to the same
                # list across turns, so recording the reference would make
                # every recorded request show the final state - and the
                # append-only assertion below would compare a list with
                # itself and pass regardless. Found by that test failing.
                recorded = dict(kwargs)
                recorded["messages"] = [dict(m) for m in kwargs["messages"]]
                outer.requests.append(recorded)
                return outer._completions.pop(0)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _openai(completions: list[_Completion]) -> tuple[OpenAIProvider, _StubOpenAI]:
    stub = _StubOpenAI(completions)
    return OpenAIProvider("unused", client=stub), stub  # type: ignore[arg-type]


def test_openai_returns_the_same_report_shape(
    specs: tuple[ToolSpec, ...],
) -> None:
    """The premise of comparing the two providers at all (D12).

    If the shapes differed, Block G's cross-provider scoring would be
    measuring the adapters.
    """
    provider, _ = _openai(
        [_Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop"))]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    assert isinstance(result, ProviderResult)
    assert result.report is not None
    assert result.report.verdict is ModelVerdict.REPORTED
    assert result.report.causes[0].cited_refs == ["ORD-0013", "5TY05013RG002845M"]
    assert result.provider == "openai"
    assert result.model_returned == "gpt-5"


def test_openai_runs_tools_and_appends_every_turn(
    specs: tuple[ToolSpec, ...],
) -> None:
    """section 5.2: the loop is append-only.

    Asserted on the message list the stub receives across turns - the second
    request must contain the first request's messages as a prefix, with the
    assistant turn and the tool result appended and nothing rewritten.
    """
    provider, stub = _openai(
        [
            _Completion(
                _Choice(
                    _ChatMessage(
                        tool_calls=[_Call("fetch_order", '{"order_ref": "O"}')]
                    ),
                    "tool_calls",
                )
            ),
            _Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop")),
        ]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    first, second = stub.requests[0]["messages"], stub.requests[1]["messages"]

    assert second[: len(first)] == first
    assert len(second) == len(first) + 2
    assert second[-1]["role"] == "tool"
    assert json.loads(second[-1]["content"]) == {"order_ref": "O"}
    assert result.turns == 2


def test_openai_reads_the_finish_reason_before_the_content(
    specs: tuple[ToolSpec, ...],
) -> None:
    """A content-filtered response must not be read as a report.

    The stub returns a filtered finish reason alongside a valid report body,
    which should never happen - and if the provider read content first it
    would file it.
    """
    provider, _ = _openai(
        [
            _Completion(
                _Choice(_ChatMessage(content=json.dumps(REPORT)), "content_filter")
            )
        ]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    assert result.refused is True
    assert result.report is None
    assert result.stop_reason == "content_filter"


def test_openai_requests_a_strict_schema(specs: tuple[ToolSpec, ...]) -> None:
    """Generated from the shared Pydantic model, not hand-written."""
    provider, stub = _openai(
        [_Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop"))]
    )

    provider.investigate(system="SYSTEM", user="USER", tools=specs, model="gpt-5")
    request = stub.requests[0]

    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["tool_choice"] == "auto"
    for tool in request["tools"]:
        assert tool["function"]["strict"] is True
        schema = tool["function"]["parameters"]
        assert schema["additionalProperties"] is False
        for prop in schema["properties"].values():
            assert prop["type"] == "string"


def test_openai_reports_an_unknown_tool_back_to_the_model(
    specs: tuple[ToolSpec, ...],
) -> None:
    """A recoverable mistake must not abandon the investigation.

    The model can correct a named error on the next turn, and the append-only
    loop keeps that correction visible rather than hiding it.
    """
    provider, stub = _openai(
        [
            _Completion(
                _Choice(
                    _ChatMessage(tool_calls=[_Call("no_such_tool", "{}")]),
                    "tool_calls",
                )
            ),
            _Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop")),
        ]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    tool_result = json.loads(stub.requests[1]["messages"][-1]["content"])
    assert "no such tool" in tool_result["error"]
    assert result.report is not None


def test_openai_reports_malformed_arguments_back_to_the_model(
    specs: tuple[ToolSpec, ...],
) -> None:
    provider, stub = _openai(
        [
            _Completion(
                _Choice(
                    _ChatMessage(tool_calls=[_Call("fetch_order", "{not json")]),
                    "tool_calls",
                )
            ),
            _Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop")),
        ]
    )

    provider.investigate(system="SYSTEM", user="USER", tools=specs, model="gpt-5")

    tool_result = json.loads(stub.requests[1]["messages"][-1]["content"])
    assert "not valid JSON" in tool_result["error"]


def test_openai_unparseable_output_is_not_reconstructed(
    specs: tuple[ToolSpec, ...],
) -> None:
    provider, _ = _openai(
        [_Completion(_Choice(_ChatMessage(content="not a report"), "stop"))]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    assert result.report is None


def test_openai_carries_no_reasoning_summary(
    specs: tuple[ToolSpec, ...],
) -> None:
    """A real difference between the providers, asserted rather than hidden.

    This API returns no reasoning summary, so section 5.2's "reasoning must be
    recorded" holds only in the weaker sense that there is nothing to record.
    Block G's comparison has to account for it.
    """
    provider, _ = _openai(
        [_Completion(_Choice(_ChatMessage(content=json.dumps(REPORT)), "stop"))]
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-5"
    )

    assert result.summarized_reasoning is None


def test_a_transport_failure_on_openai_is_a_provider_error(
    specs: tuple[ToolSpec, ...],
) -> None:
    provider = OpenAIProvider("unused", client=_ExplodingOpenAI())  # type: ignore[arg-type]

    with pytest.raises(ProviderError):
        provider.investigate(system="SYSTEM", user="USER", tools=specs, model="gpt-5")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_the_provider_is_selected_from_settings() -> None:
    from pydantic import SecretStr

    from reckonai.agent.providers import provider_for
    from reckonai.config import Settings

    url = "postgresql+psycopg://user:secret@db.example:5432/reckonai"

    anthropic_settings = Settings(
        database_url=url,  # type: ignore[arg-type]
        anthropic_api_key=SecretStr("sk-ant-test"),
    )
    assert provider_for(anthropic_settings).name == "anthropic"

    openai_settings = Settings(
        database_url=url,  # type: ignore[arg-type]
        agent_provider="openai",
        agent_model="gpt-5",
        openai_api_key=SecretStr("sk-openai-test"),
    )
    assert provider_for(openai_settings).name == "openai"


# ---------------------------------------------------------------------------
# The downgrade flag, and what must not trip it
# ---------------------------------------------------------------------------


def _result_for(requested: str, returned: str | None) -> ProviderResult:
    return ProviderResult(
        report=None,
        provider="openai",
        model_requested=requested,
        model_returned=returned,
        stop_reason="stop",
        summarized_reasoning=None,
    )


def test_an_alias_resolving_to_its_snapshot_is_not_a_downgrade() -> None:
    """Found on the first live run, where it was a false positive.

    OpenAI serves a request for `gpt-4.1` with `gpt-4.1-2025-04-14`, and a
    plain inequality stored that report flagged as downgraded. It is the same
    model named more precisely. specs/tech-stack.md section 5.2's flag exists
    to tell a finance team their reasoning came from a weaker model, and one
    that fires on every response is noise rather than evidence - the same
    failure as never firing at all.
    """
    assert _result_for("gpt-4.1", "gpt-4.1-2025-04-14").downgraded is False
    assert _result_for("gpt-5", "gpt-5-2025-08-07").downgraded is False
    assert (
        _result_for("claude-haiku-5-5", "claude-haiku-5-5").downgraded is False
    )


def test_a_genuinely_different_model_is_still_a_downgrade() -> None:
    """The flag has to stay worth having.

    Prefix matching must not become "anything vaguely similar passes": a
    different model in the same family is exactly what the flag is for.
    """
    assert _result_for("claude-haiku-5-5", "claude-sonnet-5-5").downgraded is True
    assert _result_for("gpt-4.1", "gpt-4o").downgraded is True
    assert _result_for("gpt-5", "gpt-4.1").downgraded is True


def test_no_response_is_not_a_downgrade() -> None:
    """A call that never happened observed no model to compare."""
    assert _result_for("gpt-4.1", None).downgraded is False


def test_the_strict_schema_carries_no_refs() -> None:
    """OpenAI's strict mode rejects a `$ref` with sibling keywords.

    Pydantic emits an enum field with a docstring as exactly that, and the
    live call returned:

        $ref cannot have keywords {'description'}

    The stub accepts any schema, so nothing but the real API or this test
    would catch a dialect violation. Asserted on the absence of refs rather
    than on the error, because that is the property the API requires.
    """
    from reckonai.agent.providers.openai import strict_report_schema

    rendered = json.dumps(strict_report_schema())

    assert "$ref" not in rendered
    assert "$defs" not in rendered

    # The field's own description survives, rather than being replaced by the
    # enum definition it was merged with.
    verdict = strict_report_schema()["properties"]["verdict"]
    assert "insufficient_evidence" in verdict["enum"]
    assert "never a failure" in verdict["description"]


# ---------------------------------------------------------------------------
# Code review findings that changed provider behaviour
# ---------------------------------------------------------------------------


def test_reasoning_is_collected_from_every_turn_not_just_the_last(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Finding 5. The last turn is the one that merely formats the report.

    `until_done()` returns only the final message, so on the default provider
    `summarized_reasoning` recorded the least informative part of the run and
    dropped every earlier turn - where the agent decided what evidence to
    retrieve. The stub now yields per-turn streams like the real runner, which
    is what makes this distinguishable at all.
    """
    first = _Message(
        stop_reason="tool_use",
        content=[_Block("thinking", thinking="Start with recompute.")],
    )
    second = _Message(
        stop_reason="tool_use",
        content=[_Block("thinking", thinking="Now the ledger.")],
    )
    final = _Message(
        parsed=InvestigationReport.model_validate(REPORT),
        content=[_Block("thinking", thinking="Format the report.")],
    )
    provider, _ = _anthropic(final, earlier=[first, second])

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.summarized_reasoning is not None
    assert "[turn 1] Start with recompute." in result.summarized_reasoning
    assert "[turn 2] Now the ledger." in result.summarized_reasoning
    assert "[turn 3] Format the report." in result.summarized_reasoning


def test_a_single_turn_reasoning_is_not_labelled(
    specs: tuple[ToolSpec, ...],
) -> None:
    """The common case reads as it always did."""
    provider, _ = _anthropic(
        _Message(
            parsed=InvestigationReport.model_validate(REPORT),
            content=[_Block("thinking", thinking="One step.")],
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.summarized_reasoning == "One step."


def test_turns_are_counted_on_the_anthropic_path(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Finding 10. The cost proxy was 0 on the configured default provider.

    An earlier attempt at this read a `messages` attribute that the runner
    does not expose and so silently returned 0 - a fix that looked like one.
    Counting the turns actually iterated is the version that cannot lie.
    """
    tool_turn = _Message(stop_reason="tool_use")
    provider, _ = _anthropic(
        _Message(parsed=InvestigationReport.model_validate(REPORT)),
        earlier=[tool_turn, tool_turn],
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.turns == 3


def test_the_iteration_ceiling_is_spelled_the_same_on_both_providers(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Finding 9. One outcome, one spelling, for Block G's comparison.

    The Anthropic runner stops silently at its ceiling and returns the last
    message, whose own stop reason is "tool_use" with nothing parsed.
    """
    from reckonai.agent.providers.base import ITERATION_CEILING

    provider, _ = _anthropic(_Message(stop_reason="tool_use", parsed=None))

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.report is None
    assert result.stop_reason == ITERATION_CEILING


def test_a_tool_use_stop_with_a_report_is_not_rewritten(
    specs: tuple[ToolSpec, ...],
) -> None:
    """The normalisation applies only when nothing was parsed."""
    provider, _ = _anthropic(
        _Message(
            stop_reason="tool_use",
            parsed=InvestigationReport.model_validate(REPORT),
        )
    )

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="claude-haiku-5-5"
    )

    assert result.report is not None
    assert result.stop_reason == "tool_use"


def test_openai_ceiling_uses_the_shared_spelling(
    specs: tuple[ToolSpec, ...],
) -> None:
    from reckonai.agent.providers.base import ITERATION_CEILING
    from reckonai.agent.providers.openai import MAX_ITERATIONS

    looping = [
        _Completion(
            _Choice(
                _ChatMessage(tool_calls=[_Call("recompute", "{}", f"call_{n}")]),
                "tool_calls",
            )
        )
        for n in range(MAX_ITERATIONS)
    ]
    provider, _ = _openai(looping)

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-4.1"
    )

    assert result.report is None
    assert result.stop_reason == ITERATION_CEILING
    assert result.turns == MAX_ITERATIONS


def test_an_openai_structured_output_refusal_is_read_as_a_refusal(
    specs: tuple[ToolSpec, ...],
) -> None:
    """Finding 3. A refusal arrives on `message.refusal`, not as a finish reason.

    With a strict JSON schema the response has `finish_reason == "stop"` and
    `content` None, and the explanation sits in `refusal`. Without reading it
    the row was stored as a generic failure with `refused` False and the text
    discarded - indistinguishable from an unparseable response, when section
    5.2 requires a refusal to be read as one before any content.
    """
    message = _ChatMessage(content=None)
    message.refusal = "I can't help with that."  # type: ignore[attr-defined]
    provider, _ = _openai([_Completion(_Choice(message, "stop"))])

    result = provider.investigate(
        system="SYSTEM", user="USER", tools=specs, model="gpt-4.1"
    )

    assert result.refused is True
    assert result.report is None
    assert result.stop_reason == "refusal"
    assert result.summarized_reasoning == "I can't help with that."


def test_a_weaker_variant_extending_the_requested_id_is_a_downgrade() -> None:
    """Finding 4. A bare prefix test missed the case the flag exists for.

    `gpt-4.1` served by `gpt-4.1-mini` satisfies `startswith("gpt-4.1")`, so the
    earlier fix to the alias false positive created a silent false negative -
    the kind section 5.2 calls worse than a failed investigation, in the one
    column that exists to prove it cannot happen.
    """
    assert _result_for("gpt-4.1", "gpt-4.1-mini").downgraded is True
    assert _result_for("gpt-5", "gpt-5-nano").downgraded is True
    assert _result_for("gpt-5", "gpt-5-mini").downgraded is True


def test_only_a_dated_snapshot_extends_an_id_without_downgrading() -> None:
    """Both providers' pinned-version spellings, and nothing looser."""
    assert _result_for("gpt-4.1", "gpt-4.1-2025-04-14").downgraded is False
    assert (
        _result_for("claude-haiku-4-5", "claude-haiku-4-5-20251001").downgraded
        is False
    )
    # A suffix that is date-shaped but not a date alone is still a swap.
    assert _result_for("gpt-4.1", "gpt-4.1-2025-04-14-mini").downgraded is True
