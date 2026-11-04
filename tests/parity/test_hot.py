"""computeHotRanking and snapshotHeat: the same inputs in two copies of one database, one run by the TypeScript engine and one by the Python
port, at the same instants, must leave the same rankings and hourly heat."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV
from tests.parity.test_publish import _admin

os.environ.update(ENV)

SCENARIOS = {
    "as seeded": "SELECT 1",
    "sources behind": "UPDATE sources SET last_ok_at = now() - interval '30 hours', interval_minutes = 60 WHERE id IN (SELECT id FROM sources ORDER BY id LIMIT 2)",
    "a late source": "UPDATE sources SET created_at = now() - interval '2 hours' WHERE id IN (SELECT id FROM sources ORDER BY id DESC LIMIT 2)",
    "roles changed": "UPDATE sources SET participation_mode = 'hot_signal' WHERE id = (SELECT id FROM sources ORDER BY id LIMIT 1);"
    "UPDATE sources SET participation_mode = 'isolated' WHERE id = (SELECT id FROM sources ORDER BY id DESC LIMIT 1)",
    "withdrawn": "UPDATE publications SET visibility = 'withdrawn' WHERE article_id IN (SELECT article_id FROM publications ORDER BY article_id LIMIT 6)",
}


def _rows(url: str, query: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as conn:
        return conn.execute(query).fetchall()


def _plain(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        r = dict(r)
        r.pop("id", None)
        out.append({k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()})
    return out


@pytest.fixture(scope="module")
def copies(database_url):
    admin = psycopg.connect(_admin(database_url), autocommit=True)
    base = database_url.rsplit("/", 1)[1]
    urls = {}
    admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (base,))
    for tag in ("hot_node", "hot_py"):
        admin.execute(f"DROP DATABASE IF EXISTS {tag} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {tag} TEMPLATE {base}")
        urls[tag] = database_url.rsplit("/", 1)[0] + "/" + tag
    yield urls
    admin.close()


def test_rankings_and_heat(copies):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.events.hot import compute_hot_ranking, snapshot_heat

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    base = datetime.now(UTC).replace(microsecond=0)
    with ConnectionPool(
        copies["hot_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True
    ) as pool:
        for name, sql in SCENARIOS.items():
            for url in copies.values():
                with psycopg.connect(url, autocommit=True) as c:
                    c.execute(sql)
            for hours in (0, -5, 7):
                at = base + timedelta(hours=hours)
                env = {**os.environ, **ENV, "DATABASE_URL": copies["hot_node"]}
                subprocess.run(["node", "scripts/hot-run.ts", at.isoformat()], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=120)
                compute_hot_ranking(pool, at)
                snapshot_heat(pool, at)
            node = _plain(
                _rows(copies["hot_node"], "SELECT computed_at, rule_version, entries, evidence, published FROM hot_rankings ORDER BY computed_at, id")
            )
            py = _plain(
                _rows(copies["hot_py"], "SELECT computed_at, rule_version, entries, evidence, published FROM hot_rankings ORDER BY computed_at, id")
            )
            assert py == node, f"{name}: hot_rankings differ"
            q = "SELECT story_id, hour, heat, participants, cohort, complete FROM story_heat_hourly ORDER BY story_id, hour"
            assert _plain(_rows(copies["hot_py"], q)) == _plain(_rows(copies["hot_node"], q)), f"{name}: story_heat_hourly differ"
    assert any(r["entries"] for r in node if r["computed_at"] >= base - timedelta(hours=6)), "the scenarios produced no ranking entries"
    assert any(r["hour"] >= base - timedelta(hours=6) for r in _rows(copies["hot_node"], "SELECT hour FROM story_heat_hourly")), "no hourly heat was written"
