"""Search terms, the search match conditions and the search capacity guard (the parts of publication/pool.ts the public API uses).

The conditions are SQL fragments with named parameters; `PoolSql` collects the values the terms need (term_0, term_1, …)."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

import regex


class SearchBusyError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("search capacity exhausted")
        self.retry_after = retry_after


# Search capacity guard: bounded concurrency with a short queue. Overflow answers 503 + Retry-After instead of letting machine traffic drag
# list browsing down.
MAX_CONCURRENT_SEARCHES = 4
MAX_QUEUED_SEARCHES = 8
QUEUE_TIMEOUT_S = 3.0
_cond = threading.Condition()
_running = 0
_waiting = 0


@contextmanager
def search_capacity():
    global _running, _waiting
    with _cond:
        if _running >= MAX_CONCURRENT_SEARCHES:
            if _waiting >= MAX_QUEUED_SEARCHES:
                raise SearchBusyError(5)
            _waiting += 1
            deadline = time.monotonic() + QUEUE_TIMEOUT_S
            try:
                while _running >= MAX_CONCURRENT_SEARCHES:
                    left = deadline - time.monotonic()
                    if left <= 0 or not _cond.wait(left):
                        if _running >= MAX_CONCURRENT_SEARCHES:
                            raise SearchBusyError(5)
            finally:
                _waiting -= 1
        _running += 1
    try:
        yield
    finally:
        with _cond:
            _running -= 1
            _cond.notify()


def with_search_capacity(conn, fn: Callable):
    """Runs `fn(conn)` in a transaction with plans made for the actual values (a cached generic plan cannot tell a two-character search
    term, which has no usable trigram, from a longer one), within the search capacity."""
    with search_capacity(), conn.transaction():
        conn.execute("SET LOCAL plan_cache_mode = force_custom_plan")
        return fn(conn)


_HAN_BEFORE_LATIN = regex.compile(r"(\p{Script=Han})(?=[a-z])")
_LATIN_BEFORE_HAN = regex.compile(r"([a-z][a-z0-9]*)(?=\p{Script=Han})")


def search_terms(q: str) -> list[str]:
    """Search terms: whitespace separated, lower-cased, LIKE metacharacters escaped."""
    s = q.lower()
    # "多模態RAG" means 多模態 and rag: split where Han meets a Latin word. Digits alone stay attached, so "10月" is still one term.
    s = _HAN_BEFORE_LATIN.sub(r"\1 ", s)
    s = _LATIN_BEFORE_HAN.sub(r"\1 ", s)
    terms = [t.strip() for t in re.split(r"\s+", s) if t.strip()][:6]
    return [re.sub(r"[\\%_]", lambda m: "\\" + m.group(0), t) for t in terms]


def _body_match(i: int, t: str) -> str:
    """A plain Latin or digit term must start a word in the body: "rag" is not inside "storage"."""
    like = f"ps.body LIKE %(like_{i})s"
    return f"{like} AND ps.body ~ %(word_{i})s" if re.fullmatch(r"[a-z0-9]+", t) else like


def public_match(terms: list[str]) -> tuple[str, dict]:
    """The public APIs' q (v1 and MCP): every term matches the subject, title or summary, or the start of a body whose full text may be
    shown. Results stay in time order. Returns the condition (empty without terms) and its parameters."""
    params: dict = {}
    parts = []
    for i, t in enumerate(terms):
        params[f"like_{i}"] = f"%{t}%"
        params[f"word_{i}"] = f"(^|[^a-z0-9]){t}"
        # Keep the body lookup correlated to the time-ordered candidates: without OFFSET 0, PostgreSQL may hash every matching body.
        parts.append(
            f"AND (p.search_text LIKE %(like_{i})s OR EXISTS (SELECT 1 FROM pool_search ps WHERE ps.article_id = p.article_id AND {_body_match(i, t)} OFFSET 0))"
        )
    return " ".join(parts), params
