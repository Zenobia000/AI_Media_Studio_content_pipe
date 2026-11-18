"""The chat's model as any service that speaks the OpenAI chat-completions protocol (RAG_CHAT_PROVIDER=openai): OpenAI itself, Google's Gemini through its
OpenAI-compatible endpoint, DeepSeek, Qwen, a local server, and so on. One module for all of them: the service, the key and the model are settings.

    RAG_OPENAI_BASE_URL   e.g. https://api.openai.com/v1
    RAG_OPENAI_API_KEY    the key (never logged, never in an error message)
    RAG_OPENAI_MODEL      the model's name at that service

Same two calls as the others (one answer; an answer in pieces). A JSON answer (the route) is asked for with `response_format: json_object`, which the
services accept most widely; the prompt itself says JSON, and a reply that is not JSON is treated as a question about the news (ask.route). No temperature
is sent: some current models refuse it."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from newsroom.rag.index import EmbedError

Messages = list[dict[str, str]]
TIMEOUT = 600


def settings() -> tuple[str, str, str]:
    """(base url, key, model); a missing one is an error that names the setting."""
    base = (os.environ.get("RAG_OPENAI_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("RAG_OPENAI_API_KEY") or "").strip()
    model = (os.environ.get("RAG_OPENAI_MODEL") or "").strip()
    for name, value in (("RAG_OPENAI_BASE_URL", base), ("RAG_OPENAI_API_KEY", key), ("RAG_OPENAI_MODEL", model)):
        if not value:
            raise EmbedError(f"chat failed (openai): {name} is not set")
    return base, key, model


def _body(messages: Messages, model: str, stream: bool, fmt: dict | None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if fmt:
        body["response_format"] = {"type": "json_object"}
    return body


def _failed(model: str, e: Exception) -> EmbedError:
    return EmbedError(f"chat failed (openai-compatible, model {model}): {type(e).__name__}: {e}")  # the key is only in the header, never in `e`


def complete(messages: Messages, fmt: dict | None = None, post: Callable[..., Any] = httpx.post) -> str:
    """One answer; `fmt` set means the answer must be JSON. `post` is for tests."""
    base, key, model = settings()
    try:
        res = post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=_body(messages, model, False, fmt), timeout=TIMEOUT)
        res.raise_for_status()
        return str(res.json()["choices"][0]["message"]["content"] or "").strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        raise _failed(model, e) from e


def stream(messages: Messages, open_stream: Callable[..., Any] = httpx.stream) -> Iterator[str]:
    """The answer in pieces as the model writes it (server-sent events: `data: {json}` lines, ended by `data: [DONE]`)."""
    base, key, model = settings()
    try:
        with open_stream(
            "POST", f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=_body(messages, model, True, None), timeout=TIMEOUT
        ) as res:
            res.raise_for_status()
            for line in res.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                choices = json.loads(data).get("choices") or []
                piece = choices[0].get("delta", {}).get("content") if choices else None
                if piece:
                    yield piece
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
        raise _failed(model, e) from e
