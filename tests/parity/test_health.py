"""sourceHealth: one database of runs, articles and revisions, read by the TypeScript engine and by the Python port at the same instant, must give the
same groups and the same report text."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

ERRORS = ["fetch failed", "Error: HTTP 503", "no items matched (html)", "Budget for jina exhausted (hour)", "TypeError: boom", "socialdata HTTP 429", "items path did not resolve"]


def setup_sql(ids: list[str]) -> str:
    a, b, c, d, e, f = ids
    runs = []
    # a: failing now, mostly transient errors
    runs.append(f"INSERT INTO fetch_runs (source_id, started_at, finished_at, status, error) SELECT '{a}', now() - g * interval '3 hours', now() - g * interval '3 hours', 'failed', "
                f"(ARRAY['fetch failed','Error: HTTP 503','socialdata HTTP 429'])[1 + g % 3] FROM generate_series(1, 12) g")
    # b: unstable, reads that find nothing, plus budget errors that must not count
    runs.append(f"INSERT INTO fetch_runs (source_id, started_at, finished_at, status, error) SELECT '{b}', now() - g * interval '5 hours', now() - g * interval '5 hours', "
                f"CASE WHEN g % 3 = 0 THEN 'ok' ELSE 'failed' END, CASE WHEN g % 3 = 0 THEN NULL WHEN g % 7 = 1 THEN 'Budget for jina exhausted (hour)' ELSE 'no items matched (html)' END FROM generate_series(1, 20) g")
    # c: detail pages failing
    runs.append(f"INSERT INTO fetch_runs (source_id, started_at, finished_at, status, detail) SELECT '{c}', now() - g * interval '4 hours', now() - g * interval '4 hours', 'ok', "
                f"jsonb_build_object('detailAttempts', 10, 'detailFailures', CASE WHEN g < 5 THEN 3 ELSE 0 END) FROM generate_series(1, 10) g")
    runs.append(f"INSERT INTO fetch_runs (source_id, started_at, status, found_count) SELECT '{d}', now() - g * interval '1 day', 'ok', 3 FROM generate_series(1, 6) g")
    runs.append(f"INSERT INTO fetch_runs (source_id, started_at, status) SELECT '{e}', now() - g * interval '1 day', 'skipped' FROM generate_series(1, 3) g")
    stmts = [
        f"UPDATE sources SET health='failing', fail_count=12, last_error='fetch failed: timeout', participation_mode='editorial', created_at=now()-interval '200 days', enabled=true WHERE id='{a}'",
        f"UPDATE sources SET health='ok', participation_mode='editorial', created_at=now()-interval '200 days', enabled=true WHERE id='{b}'",
        f"UPDATE sources SET health='degraded', participation_mode='hot_signal', created_at=now()-interval '200 days', enabled=true WHERE id='{c}'",
        # d: quiet, with a steady rhythm of one article every 3 days that stopped 20 days ago
        f"UPDATE sources SET health='ok', participation_mode='editorial', created_at=now()-interval '200 days', enabled=true WHERE id='{d}'",
        f"UPDATE sources SET health='ok', participation_mode='hot_signal', created_at=now()-interval '200 days', enabled=true WHERE id='{e}'",
        f"UPDATE sources SET health='ok', participation_mode='editorial', created_at=now()-interval '100 days', enabled=true WHERE id='{f}'",
        f"DELETE FROM article_discoveries WHERE source_id IN ('{d}', '{e}')",
        # the fixtures' own articles are moved: d's long ago, e's a few days apart, the rest recent and some undated
        f"WITH r AS (SELECT id, row_number() OVER (ORDER BY id) n FROM articles WHERE source_id='{d}') UPDATE articles x SET discovered_at = now() - (20 + 3*r.n) * interval '1 day', "
        f"published_at = now() - (20 + 3*r.n) * interval '1 day', backfill_reason = NULL FROM r WHERE x.id = r.id",
        f"UPDATE articles SET discovered_at = now() - interval '12 days', published_at = now() - interval '12 days' WHERE source_id='{e}'",
        f"WITH r AS (SELECT id, row_number() OVER (ORDER BY id) n FROM articles WHERE source_id='{a}') UPDATE articles x SET discovered_at = now() - n * interval '2 hours', "
        f"published_at = CASE WHEN n % 2 = 0 THEN NULL ELSE now() - n * interval '2 hours' END FROM r WHERE x.id = r.id",
        f"INSERT INTO article_revisions (article_id, revision, title, created_at) SELECT (SELECT id FROM articles WHERE source_id='{a}' ORDER BY id LIMIT 1), g, 't', now() - g * interval '1 hour' FROM generate_series(2, 8) g ON CONFLICT DO NOTHING",
        f"INSERT INTO article_discoveries (article_id, source_id, via, discovered_at) SELECT (SELECT id FROM articles WHERE source_id='{a}' ORDER BY id LIMIT 1), '{f}', 'search', now() - interval '40 days' ON CONFLICT DO NOTHING",
        *runs,
    ]  # fmt: skip
    return ";\n".join(stmts)


@pytest.fixture(scope="module")
def copy(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["health_db"])
    admin.close()
    return urls["health_db"]


def _plain(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if hasattr(v, "as_tuple"):
        return float(v)
    return v


def test_health_groups_and_text(copy):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.sources import health as H

    with psycopg.connect(copy, row_factory=dict_row) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM sources ORDER BY id").fetchall()][:6]
    with psycopg.connect(copy, autocommit=True) as conn:
        conn.execute(setup_sql(ids))
    now = datetime.now(UTC).replace(microsecond=0)
    env = {**os.environ, **ENV, "DATABASE_URL": copy}
    out = subprocess.run(["node", "scripts/health-run.ts", now.isoformat()], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=120)
    node = json.loads(out.stdout.decode().strip().splitlines()[-1])

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(copy, min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        groups = H.source_health(pool, now)
    texts = []
    for g in groups:
        texts += [H.rhythm_text(s) for s in g["unknown"]] + [H.rhythm_text(s) for s in g["overdue"]]
        texts += [H.source_health_list([s for s in g["sources"] if p["observed_at"](s)], p["describe"]) for p in H.SOURCE_PROBLEMS]
    py = _plain(groups)
    assert [g["mode"] for g in py] == [g["mode"] for g in node["groups"]]
    for pg, ng in zip(py, node["groups"], strict=True):
        for key in ng:
            assert pg[key] == ng[key], f"{ng['mode']}.{key}: {pg[key]!r} != {ng[key]!r}"
    assert texts == node["texts"]
    # the scenario reached every kind of finding
    editorial, hot = node["groups"]
    assert editorial["failing"] and editorial["unstable"] and editorial["undated"] and editorial["revisions"] and hot["details"]
    assert any("倍" in t for t in node["texts"]) and any("從未" in t or "還看不出" in t for t in node["texts"])
