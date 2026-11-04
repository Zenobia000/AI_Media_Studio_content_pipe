"""scheduleDueSources, adaptIntervals and sweepUnprocessed: the same database state handled by the TypeScript engine and by the Python port, each in its
own copy, must leave the same intervals, due times and queued work."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

SETUP = """
UPDATE sources SET next_fetch_at = NULL, interval_minutes = 30, enabled = true, health = 'ok';
UPDATE sources SET next_fetch_at = now() + interval '2 hours' WHERE id = (SELECT id FROM sources ORDER BY id LIMIT 1);
UPDATE sources SET enabled = false WHERE id = (SELECT id FROM sources ORDER BY id OFFSET 1 LIMIT 1);
UPDATE sources SET participation_mode = 'hot_signal' WHERE id = (SELECT id FROM sources ORDER BY id OFFSET 2 LIMIT 1);
UPDATE sources SET config = jsonb_set(config, '{url}', '"https://r.jina.ai/https://example.org/list"') WHERE id = (SELECT id FROM sources ORDER BY id OFFSET 3 LIMIT 1);
UPDATE articles SET discovered_at = now() - interval '1 day', backfill = false WHERE source_id = (SELECT id FROM sources ORDER BY id OFFSET 4 LIMIT 1);
UPDATE articles SET discovered_at = now() - interval '30 days', backfill = false WHERE source_id = (SELECT id FROM sources ORDER BY id OFFSET 5 LIMIT 1);
UPDATE articles SET created_at = now() - interval '1 hour', processing_state = 'new', processing_queued_at = NULL, processing_retry_at = NULL
  WHERE id IN (SELECT id FROM articles ORDER BY id LIMIT 4);
UPDATE articles SET created_at = now() - interval '1 hour', processing_state = 'new', processing_queued_at = now() - interval '2 hours'
  WHERE id IN (SELECT id FROM articles ORDER BY id OFFSET 4 LIMIT 2);
UPDATE articles SET created_at = now() - interval '1 hour', processing_state = 'new', processing_queued_at = now() - interval '5 minutes'
  WHERE id IN (SELECT id FROM articles ORDER BY id OFFSET 6 LIMIT 2);
"""


@pytest.fixture(scope="module")
def copies(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["sch_node", "sch_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(SETUP)
            if name == "sch_py":
                c.execute(migration.read_text(encoding="utf-8"))
    return urls


def dump(url: str) -> dict:
    with psycopg.connect(url, row_factory=dict_row) as c:
        sources = c.execute("SELECT id, interval_minutes, next_fetch_at IS NOT NULL AS scheduled FROM sources ORDER BY id").fetchall()
        articles = c.execute("SELECT identity_key, processing_queued_at IS NOT NULL AS queued, processing_attempt_tag FROM articles ORDER BY identity_key").fetchall()
        ids = {r["id"]: r["identity_key"] for r in c.execute("SELECT id, identity_key FROM articles").fetchall()}
        try:
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM pgboss.job").fetchall()
        except psycopg.errors.UndefinedTable:
            c.rollback()
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM jobs").fetchall()
    queue = sorted(
        (j["name"], j["priority"], (j["singleton_key"] or "").replace(j["data"].get("articleId", "\0"), ids.get(j["data"].get("articleId"), "?")),
         json.dumps({k: v for k, v in j["data"].items() if k != "articleId"}, sort_keys=True))
        for j in jobs
    )  # fmt: skip
    return {"sources": sources, "articles": articles, "queue": queue}


def test_schedule_adapt_and_sweep(copies):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.jobs.sources import adapt_intervals, schedule_due_sources, sweep_unprocessed

    env = {**os.environ, **ENV, "DATABASE_URL": copies["sch_node"], "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/schedule-run.ts"], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    node_result = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith('{"scheduled"')))

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(copies["sch_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        py_result = {"scheduled": schedule_due_sources(pool), "adapted": adapt_intervals(pool), "swept": sweep_unprocessed(pool)}
    assert py_result == node_result
    node, py = dump(copies["sch_node"]), dump(copies["sch_py"])
    assert py == node
    assert node_result["scheduled"]["enqueued"] >= 3 and node_result["swept"]["enqueued"] >= 4 and node_result["adapted"]["updated"] >= 1
