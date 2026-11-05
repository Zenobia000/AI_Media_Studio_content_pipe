"""Manual grouping corrections: the same edits made by the TypeScript engine and by the Python port, each on its own copy of one database, must leave the same memberships,
heat evidence, stories, aliases, public rows, audit entries and queued work."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_digest_merge import Fake
from tests.parity.test_publish import _admin


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["co_node", "co_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["co_py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DELETE FROM receipts")
    return urls


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        jobs = []
        for table in ("pgboss.job", "jobs"):
            try:
                jobs = c.execute(f"SELECT name, data, priority, singleton_key FROM {table} WHERE name IN ('events.group', 'events.digest')").fetchall()
                break
            except psycopg.errors.UndefinedTable:
                c.rollback()
        return {
            "members": c.execute("SELECT fa.fact_id, fa.article_id, fa.role, fa.evidence, fa.manual FROM fact_articles fa ORDER BY fa.fact_id, fa.article_id").fetchall(),
            "overrides": c.execute("SELECT article_id, reason, actor FROM grouping_overrides ORDER BY article_id").fetchall(),
            "signals": c.execute("SELECT story_id, article_id, participant_key, kind FROM story_signals ORDER BY story_id, article_id").fetchall(),
            "stories": c.execute("SELECT id, title, merged_into, version, digest, latest, first_report_at, latest_at FROM stories ORDER BY id").fetchall(),
            "aliases": c.execute("SELECT story_id FROM story_aliases ORDER BY story_id").fetchall(),
            "articles": c.execute("SELECT id, grouping_status, grouped_at IS NOT NULL AS grouped, selection_adds_value, selection_value_reason FROM articles ORDER BY id").fetchall(),
            "publications": c.execute("SELECT article_id, fact_id, story_id, selected, visibility, seat FROM publications ORDER BY article_id").fetchall(),
            "digests": c.execute("SELECT story_id, version, digest, article_ids FROM story_digests ORDER BY story_id, version").fetchall(),
            "audit": c.execute("SELECT actor, action, subject, reason, before, after FROM audit_log ORDER BY id").fetchall(),
            "receipts": c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall(),
            "jobs": sorted((j["name"], j["priority"], j["singleton_key"], json.dumps(j["data"], sort_keys=True)) for j in jobs),
        }


def test_corrections(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.events import corrections as C
    from newsroom.engine.models import invalidate_model_cache

    node_fake, py_fake = Fake(), Fake()
    base = {"MODEL_CALLS_ENABLED": "true", "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}
    for k, v in {**base, "LLM_BASE_URL": py_fake.url}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()
    with psycopg.connect(world["co_node"], row_factory=dict_row) as c:
        members = c.execute("SELECT f.id AS fact, f.public_id, f.story_id, fa.article_id FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id ORDER BY f.story_id, f.id").fetchall()
    by_story: dict[int, list[dict]] = {}
    for m in members:
        by_story.setdefault(m["story_id"], []).append(m)
    s1, s2, s3, s4, s5 = (by_story[i] for i in (1, 2, 3, 4, 5))
    calls = [
        ["detachFromFact", s1[0]["article_id"], "wrongly grouped", "editor"],
        ["detachFromFact", "no-such-article", "x", "editor"],
        ["moveToFact", s2[0]["article_id"], s3[0]["public_id"], "belongs there", "editor"],
        ["moveToFact", s2[1]["article_id"], "f-missing", "nowhere", "editor"],
        ["moveToFact", "no-such-article", s3[0]["public_id"], "x", "editor"],
        ["moveToFact", s1[0]["article_id"], s1[1]["public_id"], "put back", "editor"],  # a detached report placed by hand
        # a story emptied by moves is merged into where its reports went
        ["moveToFact", s5[0]["article_id"], s4[0]["public_id"], "m1", "editor"],
        ["moveToFact", s5[1]["article_id"], s4[0]["public_id"], "m2", "editor"],
        ["moveToFact", s5[2]["article_id"], s4[0]["public_id"], "m3", "editor"],
        ["mergeStories", 3, 3, "self", "editor"],
        ["mergeStories", 2, 3, "same event", "editor"],
        ["mergeStories", 2, 3, "again", "editor"],
        ["mergeStories", 99, 3, "unknown", "editor"],
        ["rewriteStoryDigest", 1, "prompt changed", "editor"],
        ["rewriteStoryDigest", 999, "unknown", "editor"],
        ["requestRegroup", s3[1]["article_id"], "req-1"],
        ["requestRegroup", s3[1]["article_id"], "req-1"],
        ["requestRegroup", s4[1]["article_id"], "req-2"],
    ]
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"op": "corrections", "calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, **base, "DATABASE_URL": world["co_node"], "TZ": "UTC", "LLM_BASE_URL": node_fake.url, "AIHOT_DATA_DIR": str(tmp_path / "node-data")}
    out = subprocess.run(["node", "scripts/events-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    py = []
    with ConnectionPool(world["co_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        fns = {"detachFromFact": C.detach_from_fact, "moveToFact": C.move_to_fact, "mergeStories": C.merge_stories, "rewriteStoryDigest": C.rewrite_story_digest, "requestRegroup": C.request_regroup}
        for name, *args in calls:
            try:
                r = fns[name](pool, *args)
                py.append(r if r is not None else None)
            except Exception as error:
                py.append({"error": str(error), "name": type(error).__name__})
    for call, n, p in zip(calls, node, py, strict=True):
        if isinstance(n, dict) and "error" in n and isinstance(p, dict) and "error" in p:
            # a refusal of ours reads the same; an error of the database driver differs in its class name only
            if n["name"] == "Conflict" or p["name"] == "Conflict":
                assert (n["name"], n["error"]) == (p["name"], p["error"]), (call, n, p)
            else:
                assert n["error"].split(":")[0][:40] == p["error"].split(":")[0][:40] or n["error"][:40] == p["error"][:40], (call, n, p)
        elif call[0] == "requestRegroup":
            assert bool(n) == bool(p), (call, n, p)  # job ids are not comparable
        else:
            assert p == n, (call, n, p)
    a, b = dump(world["co_node"]), dump(world["co_py"])
    for part in a:
        assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:2500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:2500]}"
    assert any(isinstance(r, dict) and r.get("detached") for r in py) and any(isinstance(r, dict) and "story" in r for r in py)
