"""The chat's model as Claude, without the network: the request it builds, what it does with the answer, and how the provider is chosen. The SDK is replaced
by a fake client, so these run on a machine that does not have `anthropic` installed."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from newsroom.rag import ask as rag
from newsroom.rag import claude_chat as claude
from newsroom.rag.index import EmbedError


class _Errors(types.ModuleType):
    class AnthropicError(Exception):
        pass


@pytest.fixture
def fake_sdk(monkeypatch):
    """A stand-in `anthropic` module, so the lazy import finds something with the exception class the code catches."""
    module = _Errors("anthropic")
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def _response(text="好", stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


class _Client:
    def __init__(self, response=None, error=None, pieces=()):
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create, stream=self._stream))
        self._response, self._error, self._pieces = response, error, pieces

    def _create(self, **kw):
        self.calls.append(kw)
        if self._error:
            raise self._error
        return self._response

    def _stream(self, **kw):
        self.calls.append(kw)
        return _Stream(self._pieces, self._response or _response())


class _Stream:
    def __init__(self, pieces, final):
        self.text_stream, self._final = iter(pieces), final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._final


def test_the_system_prompt_is_taken_apart_and_the_turns_start_with_the_user():
    system, turns = claude.split(
        [
            {"role": "system", "content": "規則一"},
            {"role": "assistant", "content": "上次的回答"},  # the chat keeps the last few turns, so it can start with the assistant's
            {"role": "user", "content": "問題"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "再問"},
        ]
    )
    assert system == "規則一"
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]


def test_the_schema_is_made_strict_for_structured_outputs():
    strict = claude._strict(rag.ROUTE_FORMAT)
    assert strict["additionalProperties"] is False
    assert "additionalProperties" not in rag.ROUTE_FORMAT  # the Ollama one is left as it was


def test_a_request_sends_no_temperature_no_thinking_switch_and_asks_for_the_fallback(monkeypatch):
    monkeypatch.delenv("RAG_CLAUDE_MODEL", raising=False)
    body = claude._request([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], rag.ROUTE_FORMAT)
    assert body["model"] == "claude-opus-5-5" and body["system"] == "s"
    assert "temperature" not in body and "thinking" not in body
    assert body["fallbacks"] == "default" and body["betas"] == [claude.FALLBACK_BETA]
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"


def test_the_model_can_be_chosen(monkeypatch):
    monkeypatch.setenv("RAG_CLAUDE_MODEL", "claude-sonnet-5-5")
    assert claude._request([{"role": "user", "content": "q"}], None)["model"] == "claude-sonnet-5-5"


def test_complete_returns_the_text_blocks_only(fake_sdk):
    client = _Client(_response("  答案  "))
    assert claude.complete([{"role": "user", "content": "q"}], None, client) == "答案"


def test_a_refusal_is_an_error_like_any_other_model_failure(fake_sdk):
    with pytest.raises(EmbedError, match="declined"):
        claude.complete([{"role": "user", "content": "q"}], None, _Client(_response("", "refusal")))


def test_the_sdk_failing_is_an_embed_error(fake_sdk):
    with pytest.raises(EmbedError, match="chat failed"):
        claude.complete([{"role": "user", "content": "q"}], None, _Client(error=fake_sdk.AnthropicError("boom")))


def test_no_user_message_is_an_error_and_not_a_request(fake_sdk):
    with pytest.raises(EmbedError, match="no user message"):
        claude.complete([{"role": "system", "content": "s"}], None, _Client(_response()))


def test_stream_yields_the_pieces(fake_sdk):
    client = _Client(pieces=["甲", "乙"])
    assert list(claude.stream([{"role": "user", "content": "q"}], client)) == ["甲", "乙"]


def test_without_the_sdk_the_error_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # an import of it fails
    with pytest.raises(EmbedError, match=r"\.\[claude\]"):
        claude.complete([{"role": "user", "content": "q"}])


def test_ollama_is_the_default_provider_and_a_misspelling_is_an_error(monkeypatch):
    monkeypatch.delenv("RAG_CHAT_PROVIDER", raising=False)
    assert rag.chat_provider() == "ollama"
    monkeypatch.setenv("RAG_CHAT_PROVIDER", " Claude ")
    assert rag.chat_provider() == "claude"
    monkeypatch.setenv("RAG_CHAT_PROVIDER", "claud")
    with pytest.raises(EmbedError, match="RAG_CHAT_PROVIDER"):
        rag.chat_provider()


def test_the_chat_goes_to_claude_when_asked_to(monkeypatch):
    monkeypatch.setenv("RAG_CHAT_PROVIDER", "claude")
    monkeypatch.setattr(claude, "complete", lambda messages, fmt=None, client=None: "來自 Claude")
    monkeypatch.setattr(claude, "stream", lambda messages, client=None: iter(["來", "自"]))
    assert rag.chat([{"role": "user", "content": "q"}]) == "來自 Claude"
    assert list(rag.chat_stream([{"role": "user", "content": "q"}])) == ["來", "自"]
