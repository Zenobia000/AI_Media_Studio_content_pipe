"""The chat's model as an OpenAI-compatible service, without the network: the request it sends, how it reads a reply and a stream, and what a missing
setting or a failing service becomes. The HTTP call is replaced by a fake."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from newsroom.rag import ask as rag
from newsroom.rag import openai_chat as oa
from newsroom.rag.index import EmbedError


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("RAG_OPENAI_BASE_URL", "https://api.example.com/v1/")
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("RAG_OPENAI_MODEL", "some-model")


def _reply(text):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": text}}]})


def test_the_request_goes_to_chat_completions_with_the_key_in_the_header_only():
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return _reply("  答案 ")

    assert oa.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], None, post) == "答案"
    assert seen["url"] == "https://api.example.com/v1/chat/completions"  # the trailing slash of the setting is dropped
    assert seen["headers"] == {"Authorization": "Bearer sk-secret"}
    assert "sk-secret" not in json.dumps(seen["json"]) and "temperature" not in seen["json"]
    assert seen["json"]["model"] == "some-model" and seen["json"]["stream"] is False and "response_format" not in seen["json"]


def test_a_json_answer_is_asked_for_as_json_object():
    seen = {}
    oa.complete([{"role": "user", "content": "q"}], rag.ROUTE_FORMAT, lambda url, **kw: seen.update(kw) or _reply("{}"))
    assert seen["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("name", ["RAG_OPENAI_BASE_URL", "RAG_OPENAI_API_KEY", "RAG_OPENAI_MODEL"])
def test_a_missing_setting_is_an_error_that_names_it(monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(EmbedError, match=name):
        oa.complete([{"role": "user", "content": "q"}], None, lambda *a, **k: _reply("x"))


def test_a_failing_service_is_an_embed_error_that_does_not_show_the_key():
    def post(*a, **k):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(EmbedError) as e:
        oa.complete([{"role": "user", "content": "q"}], None, post)
    assert "chat failed" in str(e.value) and "sk-secret" not in str(e.value)


def test_a_reply_with_no_choices_is_an_embed_error():
    with pytest.raises(EmbedError):
        oa.complete([{"role": "user", "content": "q"}], None, lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": []}))


class _Stream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return SimpleNamespace(raise_for_status=lambda: None, iter_lines=lambda: iter(self._lines))

    def __exit__(self, *a):
        return False


def test_stream_yields_the_pieces_and_stops_at_done():
    lines = [
        "",
        'data: {"choices": [{"delta": {"role": "assistant"}}]}',  # no content yet
        'data: {"choices": [{"delta": {"content": "甲"}}]}',
        ": keep-alive",
        'data: {"choices": [{"delta": {"content": "乙"}}]}',
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "不該出現"}}]}',
    ]
    assert list(oa.stream([{"role": "user", "content": "q"}], lambda *a, **k: _Stream(lines))) == ["甲", "乙"]


def test_the_chat_goes_to_the_service_when_asked_to(monkeypatch):
    monkeypatch.setenv("RAG_CHAT_PROVIDER", "openai")
    monkeypatch.setattr(oa, "complete", lambda messages, fmt=None, post=None: "來自服務")
    monkeypatch.setattr(oa, "stream", lambda messages, open_stream=None: iter(["來", "自"]))
    assert rag.chat([{"role": "user", "content": "q"}]) == "來自服務"
    assert list(rag.chat_stream([{"role": "user", "content": "q"}])) == ["來", "自"]
