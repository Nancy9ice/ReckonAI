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


class _Runner:
    def __init__(self, message: _Message) -> None:
        self._message = message
        self.compacted = False

    def until_done(self) -> _Message:
        return self._message

    def compact_before_next_turn(self) -> None:
        # Recorded rather than implemented. If the provider ever called this,
        # the append-only test below would catch it.
        self.compacted = True


class _StubAnthropic:
    """Captures the request and returns a prepared message."""

    def __init__(self, message: _Message) -> None:
        self.request: dict[str, Any] = {}
        self._message = message
        outer = self

        class _Messages:
            def tool_runner(self, **kwargs: Any) -> _Runner:
                outer.request = kwargs
                return outer.runner

        class _Beta:
            messages = _Messages()

        self.runner = _Runner(message)
        self.beta = _Beta()


def _anthropic(message: _Message) -> tuple[AnthropicProvider, _StubAnthropic]:
    stub = _StubAnthropic(message)
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
