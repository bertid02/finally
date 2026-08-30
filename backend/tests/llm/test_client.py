"""The provider call. No test here reaches the network."""

from __future__ import annotations

import pytest

from app.llm import client as client_module
from app.llm.client import EXTRA_BODY, MODEL, LLMUnavailableError, complete
from app.llm.schema import LLMResponse


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


@pytest.fixture
def calls(monkeypatch):
    """Capture the kwargs LiteLLM would have been called with."""
    recorded = []

    def _fake_completion(**kwargs):
        recorded.append(kwargs)
        return _Response('{"message": "hi"}')

    monkeypatch.setattr(client_module, "_load_completion", lambda: _fake_completion)
    return recorded


async def test_returns_the_message_content(calls):
    assert await complete([{"role": "user", "content": "hi"}]) == '{"message": "hi"}'


async def test_pins_the_model_the_provider_and_the_response_schema(calls):
    await complete([{"role": "user", "content": "hi"}])
    kwargs = calls[0]
    assert kwargs["model"] == MODEL
    assert kwargs["extra_body"] == EXTRA_BODY
    assert kwargs["extra_body"]["provider"]["order"] == ["cerebras"]
    assert kwargs["response_format"] is LLMResponse
    assert kwargs["reasoning_effort"] == "low"


async def test_a_provider_exception_becomes_llm_unavailable(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(client_module, "_load_completion", lambda: _boom)
    with pytest.raises(LLMUnavailableError, match="rate limited"):
        await complete([])


async def test_a_response_with_no_content_is_unavailable_not_a_parse_error(monkeypatch):
    monkeypatch.setattr(
        client_module, "_load_completion", lambda: lambda **kwargs: _Response(None)
    )
    with pytest.raises(LLMUnavailableError, match="no content"):
        await complete([])


def test_the_lazy_import_resolves_litellms_completion():
    # The one place the real dependency is touched -- it must be importable, or
    # every live call fails at the first message rather than at startup.
    assert callable(client_module._load_completion())
