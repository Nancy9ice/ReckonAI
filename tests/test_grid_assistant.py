"""The grid assistant: display-only, validated, provider-selected, no live call."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reckonai.agent import grid_assistant
from reckonai.agent.grid_assistant import GridAssistantError, ask, parse
from reckonai.app import app
from reckonai.config import Settings

GOOD = {
    "gridState": {"filter": {"filterModel": {}}},
    "propertiesToIgnore": ["sort", "rowGroup"],
    "explanation": "Showed confirmed rows only.",
}


def _settings(monkeypatch: pytest.MonkeyPatch, provider: str, model: str) -> Settings:
    monkeypatch.setenv("AGENT_PROVIDER", provider)
    monkeypatch.setenv("AGENT_MODEL", model)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return Settings()  # type: ignore[call-arg]


def test_a_valid_reply_parses() -> None:
    assert parse(json.dumps(GOOD)) == GOOD


def test_a_fenced_reply_parses() -> None:
    assert parse("```json\n" + json.dumps(GOOD) + "\n```") == GOOD


@pytest.mark.parametrize(
    "reply",
    [
        "sure, here you go",
        "[]",
        json.dumps({**GOOD, "gridState": "no"}),
        json.dumps({**GOOD, "propertiesToIgnore": ["madeUp"]}),
        json.dumps({**GOOD, "explanation": 3}),
        json.dumps({"gridState": {}}),
    ],
)
def test_an_unusable_reply_is_a_named_error(reply: str) -> None:
    with pytest.raises(GridAssistantError):
        parse(reply)


class _AnthropicStub:
    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.calls: list[dict[str, Any]] = []
        self._text, self._stop = text, stop
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        from anthropic.types import TextBlock

        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason=self._stop,
            content=[TextBlock(type="text", text=self._text, citations=None)],
        )


def test_anthropic_path_sends_no_sampling_parameters_and_reads_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _AnthropicStub(json.dumps(GOOD))
    result = ask(
        _settings(monkeypatch, "anthropic", "claude-haiku-5-5"),
        "confirmed only",
        {},
        {},
        client=stub,
    )
    assert result == GOOD
    sent = stub.calls[0]
    assert not {"temperature", "top_p", "top_k", "thinking", "tools"} & sent.keys()
    assert sent["output_config"] == {"effort": "low"}
    assert sent["model"] == "claude-haiku-5-5"


def test_a_refusal_is_an_error_not_a_state(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _AnthropicStub(json.dumps(GOOD), stop="refusal")
    with pytest.raises(GridAssistantError, match="declined"):
        ask(
            _settings(monkeypatch, "anthropic", "claude-haiku-5-5"),
            "x",
            {},
            {},
            client=stub,
        )


def test_openai_path_asks_for_a_json_object(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def create(**kwargs: Any) -> Any:
        sent.update(kwargs)
        message = SimpleNamespace(content=json.dumps(GOOD))
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=message)]
        )

    stub = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = ask(_settings(monkeypatch, "openai", "gpt-4.1"), "x", {}, {}, client=stub)
    assert result == GOOD
    assert sent["response_format"] == {"type": "json_object"}


def test_the_model_is_not_sent_any_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Display only: the user turn is the sentence, the state and the schema."""
    stub = _AnthropicStub(json.dumps(GOOD))
    ask(
        _settings(monkeypatch, "anthropic", "claude-haiku-5-5"),
        "hide the order column",
        {"columns": []},
        {"type": "object"},
        client=stub,
    )
    turn = json.loads(stub.calls[0]["messages"][0]["content"])
    assert set(turn) == {"request", "currentState", "gridStateSchema"}


@pytest.fixture
def client(database_url: str) -> Any:
    with TestClient(app) as test_client:
        yield test_client


BODY = {"request": "confirmed only", "state": {}, "schema": {"type": "object"}}


def test_the_route_returns_the_validated_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(grid_assistant, "ask", lambda *_a, **_k: GOOD)
    response = client.post("/api/grid-assistant", json=BODY)
    assert response.status_code == 200
    assert response.json() == GOOD


def test_a_missing_credential_is_a_503_naming_the_variable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from reckonai.config import get_settings

    monkeypatch.setenv("AGENT_PROVIDER", "anthropic")
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        response = client.post("/api/grid-assistant", json=BODY)
    finally:
        get_settings.cache_clear()
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_a_blank_or_oversized_request_is_refused(client: TestClient) -> None:
    assert (
        client.post("/api/grid-assistant", json={**BODY, "request": "  "}).status_code
        == 422
    )
    big = {**BODY, "request": "x" * 501}
    assert client.post("/api/grid-assistant", json=big).status_code == 422
