"""LLM_EXTRA_JSON and LLM_REASONING_TOKENS: the default model reads them as the original did (providers/llm.ts), so what .env.example promises is true."""

from __future__ import annotations

import pytest

from newsroom.engine import models as M
from newsroom.engine.llm import request_for


def opts(**kw):
    return {"purpose": "p", "prompt_version": "v", "system": "s", "user": "u", **kw}


def test_unset_changes_nothing(monkeypatch):
    monkeypatch.delenv("LLM_EXTRA_JSON", raising=False)
    monkeypatch.delenv("LLM_REASONING_TOKENS", raising=False)
    spec = M.models()["default"]
    assert "extra" not in spec and "reasoningTokens" not in spec and "envError" not in spec
    assert request_for(opts(max_tokens=1200), spec)[2] == 1200


def test_extra_fields_and_room_for_reasoning(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_JSON", '{"enable_thinking": false}')
    monkeypatch.setenv("LLM_REASONING_TOKENS", "4000")
    spec = M.models()["default"]
    assert spec["extra"] == {"enable_thinking": False} and spec["reasoningTokens"] == 4000
    request, _, max_tokens, _ = request_for(opts(max_tokens=1200), spec)
    assert max_tokens == 5200  # the step's own limit and the room added to it
    assert request.identity["extra"] == {"enable_thinking": False}  # a different setting is a different request, as in the original


def test_the_floor_of_512_stays_below_the_room(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_TOKENS", "0")
    assert request_for(opts(max_tokens=100), M.models()["default"])[2] == 512


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LLM_EXTRA_JSON", "{not json", 'LLM_EXTRA_JSON must be a JSON object, e.g. {"enable_thinking": false}'),
        ("LLM_REASONING_TOKENS", "-5", "LLM_REASONING_TOKENS must be a non-negative integer, e.g. 4000"),
        ("LLM_REASONING_TOKENS", "4k", "LLM_REASONING_TOKENS must be a non-negative integer, e.g. 4000"),
    ],
)
def test_a_bad_value_is_reported_when_a_call_is_made(monkeypatch, name, value, message):
    monkeypatch.setenv(name, value)
    spec = M.models()["default"]  # the registry still builds: the other features do not stop for it
    with pytest.raises(RuntimeError, match=message.replace("{", r"\{").replace("}", r"\}")):
        request_for(opts(max_tokens=1200), spec)
