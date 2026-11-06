"""Reads shared within this process. Port of lib/cache.ts, with threads instead of promises.

`shared`: callers that overlap share one read and nothing is kept. `cached`: a value kept for a while. Readers get it straight away within
`fresh_ms`; after that the old value is still returned while a single background read replaces it, so no reader waits for a refresh. Past
`max_stale_ms` (or before the first read) callers wait for the read. Concurrent readers always share one read.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime
from typing import Generic, TypeVar

K = TypeVar("K")
T = TypeVar("T")
A = TypeVar("A")


def shared(name: Callable[[K], str], load: Callable[[K], T]) -> Callable[[K], T]:
    """Callers with the same key that arrive while its read is running wait for that read; nothing is kept once it settles, so a finished or
    failed read is never reused."""
    pending: dict[str, Future] = {}
    lock = threading.Lock()

    def read(key: K) -> T:
        ident = name(key)
        with lock:
            existing = pending.get(ident)
            owner = existing is None
            if existing is None:
                future: Future = Future()
                pending[ident] = future
            else:
                future = existing
        if owner:
            try:
                future.set_result(load(key))
            except BaseException as e:  # noqa: BLE001
                future.set_exception(e)
            finally:
                with lock:
                    pending.pop(ident, None)
        return future.result()

    return read


class Cached(Generic[T, A]):
    def __init__(self, load: Callable[[A], T], fresh_ms: float, max_stale_ms: float) -> None:
        self._load, self._fresh, self._stale = load, fresh_ms / 1000, max_stale_ms / 1000
        self._value: tuple[float, T] | None = None
        self._pending: Future | None = None
        self._lock = threading.Lock()

    def _refresh(self, arg: A) -> Future:
        with self._lock:
            if self._pending is not None:
                return self._pending
            future: Future = Future()
            self._pending = future

        def work() -> None:
            try:
                data = self._load(arg)
                self._value = (time.monotonic(), data)
                future.set_result(data)
            except BaseException as e:  # noqa: BLE001
                future.set_exception(e)
            finally:
                with self._lock:
                    self._pending = None

        threading.Thread(target=work, daemon=True).start()
        return future

    def get(self, arg: A | None = None) -> T:
        value = self._value
        age = time.monotonic() - value[0] if value else float("inf")
        if value and age < self._fresh:
            return value[1]
        future = self._refresh(arg)  # type: ignore[arg-type]
        if value and age < self._stale:
            return value[1]  # a failed background read keeps the old value; the next reader tries again
        return future.result()


def cached(load: Callable[[A], T], fresh_ms: float, max_stale_ms: float) -> Cached[T, A]:
    return Cached(load, fresh_ms, max_stale_ms)


def cached_by_key(name: Callable[[K], str], load: Callable[[K], T], fresh_ms: float, max_stale_ms: float, max_keys: int) -> Callable[[K], T]:
    """One `cached` value per key, for at most `max_keys` keys: the first key read is dropped first."""
    entries: dict[str, Cached] = {}
    lock = threading.Lock()

    def read(key: K) -> T:
        ident = name(key)
        with lock:
            entry = entries.get(ident)
            if entry is None:
                if len(entries) >= max_keys:
                    entries.pop(next(iter(entries)))
                entry = entries[ident] = Cached(load, fresh_ms, max_stale_ms)
        return entry.get(key)

    return read


def shared_search(name: Callable[[K], str], load: Callable[[K, datetime], T], is_search: Callable[[K], bool]) -> Callable[..., T]:
    """A public search that overlapping callers share. A call that names its own clock (a replay, a historical read) and any call
    `is_search` rejects read on their own."""
    search = shared(name, lambda key: load(key, datetime.now(UTC)))

    def read(key: K, now=None) -> T:
        if now or not is_search(key):
            return load(key, now or datetime.now(UTC))
        return search(key)

    return read
