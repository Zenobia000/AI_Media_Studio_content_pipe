"""The chat's model as Claude (RAG_CHAT_PROVIDER=claude), through the official `anthropic` SDK: the same two calls `ask.py` makes of Ollama (one answer; an
answer in pieces), with the same messages. The SDK is imported when the first call is made, so a machine that keeps the default (Ollama) does not need it
(`pip install -e ".[claude]"`).

What differs from the Ollama calls:
- Claude takes the system prompt apart from the messages, and the first message must be the user's: the system messages are joined into `system`, and
  earlier assistant turns that come before the first user turn (the chat keeps only the last few turns) are dropped.
- No `temperature` (the current models do not take it), no `think: false` (Opus 5.5 always thinks; `effort: low` keeps it short).
- A JSON answer (`fmt`, the route) is asked for with structured outputs, which need `additionalProperties: false` on every object.
- A refusal comes back as a normal response with stop_reason "refusal"; the server-side fallback (beta `server-side-fallback-2026-07-01`) re-runs it on
  another model, and if the whole chain refuses it is raised as an error like any other model failure.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from newsroom.rag.index import EmbedError

Messages = list[dict[str, str]]
DEFAULT_MODEL = "claude-opus-5-5"
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def claude_model() -> str:
    return os.environ.get("RAG_CLAUDE_MODEL") or DEFAULT_MODEL


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema with `additionalProperties: false` on every object, as structured outputs require."""
    out = dict(schema)
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["properties"] = {k: _strict(v) for k, v in out.get("properties", {}).items()}
    return out


def split(messages: Messages) -> tuple[str, Messages]:
    """(the system prompt, the turns): Claude wants the system apart, and the turns to start with the user's."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") in ("user", "assistant")]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return system, turns


def _request(messages: Messages, fmt: dict | None) -> dict[str, Any]:
    system, turns = split(messages)
    if not turns:
        raise EmbedError("chat failed (claude): there is no user message to answer")
    output_config: dict[str, Any] = {"effort": "low"}
    if fmt:
        output_config["format"] = {"type": "json_schema", "schema": _strict(fmt)}
    request: dict[str, Any] = {
        "model": claude_model(),
        "max_tokens": MAX_TOKENS,
        "messages": turns,
        "output_config": output_config,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }
    if system:
        request["system"] = system
    return request


def _client() -> Any:
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as e:
        raise EmbedError('chat failed (claude): the `anthropic` package is not installed (python -m pip install -e ".[claude]")') from e
    return anthropic


def _failed(e: Exception) -> EmbedError:
    return EmbedError(f"chat failed (claude, model {claude_model()}): {type(e).__name__}: {e}")


def complete(messages: Messages, fmt: dict | None = None, client: Any = None) -> str:
    """One answer; `fmt` is a JSON schema the answer must follow. `client` is for tests."""
    anthropic = _client()
    try:
        response = (client or anthropic.Anthropic()).beta.messages.create(**_request(messages, fmt))
    except (anthropic.AnthropicError, TypeError, ValueError) as e:  # TypeError: the SDK's complaint about a missing API key
        raise _failed(e) from e
    if response.stop_reason == "refusal":
        raise EmbedError(f"chat failed (claude, model {claude_model()}): the model declined the request")
    return "".join(b.text for b in response.content if b.type == "text").strip()


def stream(messages: Messages, client: Any = None) -> Iterator[str]:
    """The answer in pieces as the model writes it."""
    anthropic = _client()
    try:
        with (client or anthropic.Anthropic()).beta.messages.stream(**_request(messages, None)) as s:
            yield from s.text_stream
            final = s.get_final_message()
    except (anthropic.AnthropicError, TypeError, ValueError) as e:
        raise _failed(e) from e
    if final.stop_reason == "refusal":
        raise EmbedError(f"chat failed (claude, model {claude_model()}): the model declined the request")
