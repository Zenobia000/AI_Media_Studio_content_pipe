"""Answers a question from the indexed articles: search, keep what scores above RAG_MIN_SCORE, let a local Qwen answer from those articles only.
With nothing above the score the answer is "not found" and the model is not called, so it cannot make something up from unrelated articles."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx

from newsroom.rag.index import EmbedError, EmbedFn, embed, embed_url, search

ChatFn = Callable[[str, str], str]
NOT_FOUND = "資料庫裡找不到相關的文章。"

SYSTEM = (
    "你是新聞資料庫的助手。只根據下面「文章」裡的內容回答，用繁體中文。"
    "每個重點後面用 [編號] 標出出處。文章裡沒有的資訊就說文章沒提到，不要自己補充。"
)


def min_score() -> float:
    try:
        return float(os.environ.get("RAG_MIN_SCORE", "0.6"))
    except ValueError:
        return 0.6


def chat_model() -> str:
    return os.environ.get("RAG_CHAT_MODEL") or "qwen2.5:14b"


def chat(system: str, user: str) -> str:
    """One answer from an Ollama server (/api/chat)."""
    try:
        res = httpx.post(
            f"{embed_url()}/api/chat",
            json={"model": chat_model(), "stream": False, "options": {"temperature": 0.2},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=600,
        )  # fmt: skip
        res.raise_for_status()
        return str(res.json()["message"]["content"]).strip()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise EmbedError(f"chat failed ({embed_url()}, model {chat_model()}): {e}") from e


def ask(conn, question: str, k: int = 5, embed_fn: EmbedFn = embed, chat_fn: ChatFn = chat) -> dict[str, Any]:
    hits = [h for h in search(conn, question, k, embed_fn) if h["score"] >= min_score()]
    if not hits:
        return {"question": question, "found": False, "answer": NOT_FOUND, "sources": []}
    articles = "\n\n".join(f"[{i}] {h['title']}\n{h['summary']}" for i, h in enumerate(hits, 1))
    answer = chat_fn(SYSTEM, f"文章：\n{articles}\n\n問題：{question}")
    return {"question": question, "found": True, "answer": answer, "sources": [{"n": i, **h} for i, h in enumerate(hits, 1)]}
