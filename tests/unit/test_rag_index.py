"""The worker's vector timer: it is only scheduled when switched on, and a stopped embedding server leaves the articles for the next run instead of failing."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from newsroom.engine.jobs.sources import register_processing_jobs
from newsroom.rag import index
from newsroom.rag.index import EmbedError


class _Pool:
    @contextmanager
    def connection(self):
        yield None


def _scheduled(monkeypatch, switch: str | None) -> list[str]:
    if switch is None:
        monkeypatch.delenv("RAG_INDEX_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RAG_INDEX_ENABLED", switch)
    names: list[str] = []
    worker = SimpleNamespace(pool=_Pool(), work=lambda *a, **k: None, schedule=lambda name, *a, **k: names.append(name))
    register_processing_jobs(worker)
    return names


@pytest.mark.parametrize("switch", [None, "false", "1"])
def test_the_timer_is_off_unless_the_switch_is_true(monkeypatch, switch):
    assert "rag.index" not in _scheduled(monkeypatch, switch)


def test_the_timer_is_scheduled_when_the_switch_is_true(monkeypatch):
    assert "rag.index" in _scheduled(monkeypatch, "true")


def test_a_run_reports_what_it_embedded(monkeypatch):
    monkeypatch.setattr(index, "index_articles", lambda conn, limit=None: {"candidates": 10, "embedded": 4, "already_indexed": 6})
    assert index.index_pending(_Pool()) == {"embedded": 4, "candidates": 10}


def test_a_run_is_capped(monkeypatch):
    seen = []
    monkeypatch.setattr(index, "index_articles", lambda conn, limit=None: seen.append(limit) or {"candidates": 0, "embedded": 0, "already_indexed": 0})
    index.index_pending(_Pool(), limit=50)
    assert seen == [50]


def test_a_stopped_embedding_server_is_a_skipped_run_not_a_failed_one(monkeypatch):
    def down(conn, limit=None):
        raise EmbedError("connection refused")

    monkeypatch.setattr(index, "index_articles", down)
    assert index.index_pending(_Pool()) == {"skipped": "connection refused"}
