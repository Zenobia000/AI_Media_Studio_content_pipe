"""Story digests and story merges: the same stories handled by the TypeScript engine and by the Python port, each on its own copy of one database, against fake model
servers, must end with the same digests, versions, titles, receipts, memberships, aliases, queued work and audit rows."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin


class Fake:
    """A digest of the request: its length and wording depend on the request alone, so both engines get the same reply."""

    def __init__(self):
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                user = body["messages"][-1]["content"]
                outer.requests.append(user)
                n = int(hashlib.sha1(user.encode()).hexdigest(), 16)
                if "SHORTDIGEST" in user:
                    content = json.dumps({"title": "標題", "digest": "短"}, ensure_ascii=False)
                elif "NOTITLE" in user:
                    content = json.dumps({"digest": f"這是沒有新標題的綜述，編號 {n % 1000}，說明事件的經過和影響。"}, ensure_ascii=False)
                else:
                    content = json.dumps({"title": f"綜述標題 {n % 1000}", "digest": f"這是事件的綜述，編號 {n % 1000}，說明事件的經過和影響，並提到最新進展。"}, ensure_ascii=False)
                data = json.dumps({"id": "c", "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["dg_node", "dg_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["dg_py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DELETE FROM receipts")
    return urls


def run_sql(urls, sql: str, params=None):
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(sql, params)


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        jobs = []
        for table in ("pgboss.job", "jobs"):
            try:
                jobs = c.execute(f"SELECT name, data, singleton_key FROM {table} WHERE name = 'events.digest'").fetchall()
                break
            except psycopg.errors.UndefinedTable:
                c.rollback()
        return {
            "stories": c.execute("SELECT id, origin, title, digest, latest, version, merged_into, digest_updated_at IS NOT NULL AS stamped, latest_at, first_report_at FROM stories ORDER BY id").fetchall(),
            "digests": c.execute("SELECT story_id, version, digest, latest, article_ids, inputs_hash, receipt_id IS NOT NULL AS paid FROM story_digests ORDER BY story_id, version").fetchall(),
            "facts": c.execute("SELECT id, story_id FROM facts ORDER BY id").fetchall(),
            "signals": c.execute("SELECT story_id, article_id, participant_key, source_id, kind FROM story_signals ORDER BY story_id, article_id").fetchall(),
            "aliases": c.execute("SELECT story_id, public_id FROM story_aliases ORDER BY public_id").fetchall(),
            "publications": c.execute("SELECT article_id, story_id, selected, visibility FROM publications ORDER BY article_id").fetchall(),
            "receipts": c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall(),
            "audit": c.execute("SELECT actor, action, subject, reason, before, after FROM audit_log ORDER BY id").fetchall(),
            "jobs": sorted(jobs, key=lambda j: (j["singleton_key"] or "", json.dumps(j["data"]))),
        }


def run_node(urls, fake, op: dict, tmp_path):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(op), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": urls["dg_node"], "TZ": "UTC", "MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake",
           "AIHOT_DATA_DIR": str(tmp_path / "node-data")}  # fmt: skip
    out = subprocess.run(["node", "scripts/events-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


# The report a step changes has to be one a digest reads. The seed withdraws two reports and shows only the summary of others, picked by article id, which is
# random: when the first report of an event was one of them, changing it changed nothing and the step's own check failed.
SHOWN = "fa.article_id IN (SELECT article_id FROM publications WHERE visibility = 'public')"


def test_digests_and_merges(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.events.digest import compose_story_digest
    from newsroom.engine.events.merge import merge_story_into
    from newsroom.engine.models import invalidate_model_cache
    from newsroom.engine.receipts import failure_kind

    node_fake, py_fake = Fake(), Fake()
    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake", "AIHOT_DATA_DIR": str(tmp_path / "py-data")}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    def check(label, node_out, py_out):
        assert py_out == node_out, (label, node_out, py_out)
        a, b = dump(world["dg_node"]), dump(world["dg_py"])
        for part in a:
            assert b[part] == a[part], f"{label}: {part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:1500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:1500]}"
        assert sorted(py_fake.requests) == sorted(node_fake.requests), label

    def digest(pool, ids, rewrite=False):
        node = run_node(world, node_fake, {"op": "digest", "ids": ids, "rewrite": rewrite}, tmp_path)
        py = []
        for i in ids:
            try:
                py.append(compose_story_digest(pool, i, rewrite))
            except Exception as error:
                py.append({"error": failure_kind(error), "name": type(error).__name__})
        return node, py

    with ConnectionPool(world["dg_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        # the first digests, a story that is not there, and one more run that finds nothing to do
        node, py = digest(pool, [1, 2, 3, 4, 5, 999])
        check("first", node, py)
        assert [r["updated"] for r in py] == [True] * 5 + [False], py
        node, py = digest(pool, [1, 2, 3])
        check("again", node, py)
        assert [r["updated"] for r in py] == [False] * 3
        # an editor corrected a report's wording: the same reports with different content are written again from the reports as they are
        run_sql(world, f"UPDATE publications SET title = title || ' (corrected)' WHERE article_id = (SELECT min(fa.article_id) FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE {SHOWN} AND f.story_id = 1)")
        node, py = digest(pool, [1])
        check("corrected", node, py)
        assert py[0]["updated"] and py[0]["version"] == 3
        # a report left the story
        run_sql(world, f"DELETE FROM fact_articles WHERE article_id = (SELECT min(fa.article_id) FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE {SHOWN} AND f.story_id = 2)")
        check("withdrawn", *digest(pool, [2]))
        # and came back: the digest of exactly that evidence is reused without a model call
        # an explicit rewrite
        check("rewrite", *digest(pool, [3], True))
        # a story without any report loses its digest
        run_sql(world, "DELETE FROM fact_articles WHERE fact_id IN (SELECT id FROM facts WHERE story_id = 4)")
        node, py = digest(pool, [4])
        check("emptied", node, py)
        assert py == [{"updated": True}]
        # a digest the model gets wrong, and one without a new title
        run_sql(world, f"UPDATE publications SET title = title || ' SHORTDIGEST' WHERE article_id = (SELECT min(fa.article_id) FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE {SHOWN} AND f.story_id = 5)")
        node, py = digest(pool, [5])
        check("short", node, py)
        assert py[0].get("error") == "unusable", py
        run_sql(world, "UPDATE publications SET title = replace(title, 'SHORTDIGEST', 'NOTITLE') WHERE title LIKE '%SHORTDIGEST%'")
        check("no title", *digest(pool, [5]))
        # a manual story keeps its title
        run_sql(world, "UPDATE stories SET origin = 'manual' WHERE id = 3")
        run_sql(world, f"UPDATE publications SET title = title || ' again' WHERE article_id = (SELECT min(fa.article_id) FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id WHERE {SHOWN} AND f.story_id = 3)")
        check("manual", *digest(pool, [3]))

        # merges: a report with evidence in both stories keeps one row; unknown, merged and self merges; grouping never changes an editor's story
        run_sql(world, "INSERT INTO story_signals (story_id, article_id, participant_key, source_id, kind, observed_at) SELECT 5, article_id, participant_key, source_id, kind, observed_at FROM story_signals WHERE story_id = 1 LIMIT 2 ON CONFLICT DO NOTHING")
        node = run_node(world, node_fake, {"op": "merge", "pairs": [[1, 5], [999, 5], [1, 5], [5, 5]]}, tmp_path)
        py = []
        for a, b in [(1, 5), (999, 5), (1, 5), (5, 5)]:
            try:
                py.append(merge_story_into(pool, a, b, "test", "editor"))
            except Exception as error:
                py.append({"error": str(error)})
        check("merge", node, py)
        node = run_node(world, node_fake, {"op": "merge", "pairs": [[3, 4]], "actor": "grouping"}, tmp_path)
        py = [merge_story_into(pool, 3, 4, "test", "grouping")]
        check("manual merge by grouping", node, py)
        assert py == [None]
        node = run_node(world, node_fake, {"op": "merge", "pairs": [[2, 4]], "actor": "grouping"}, tmp_path)
        py = [merge_story_into(pool, 2, 4, "test", "grouping")]
        check("grouping merge", node, py)
        assert py[0] and py[0]["moved"] >= 0
    final = dump(world["dg_py"])
    assert any(s["merged_into"] for s in final["stories"]) and final["aliases"] and final["audit"] and final["jobs"]
