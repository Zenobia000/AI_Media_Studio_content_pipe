"""Daily retention and recovery of stale work: the same housekeeping done by the TypeScript engine and by the Python port, each on its own copy of one database and its own
data directory, must delete the same rows and files, release the same receipts and leave the same audit, articles and queued work."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

NOW = datetime.now(UTC).replace(microsecond=0)
AGO = lambda **kw: NOW - timedelta(**kw)  # noqa: E731


@pytest.fixture(scope="module")
def world(database_url, settings_env, tmp_path_factory):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["op_node", "op_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    dirs = {"op_node": tmp_path_factory.mktemp("node-data"), "op_py": tmp_path_factory.mktemp("py-data")}
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            if name == "op_py":
                c.execute(migration.read_text(encoding="utf-8"))
            c.execute("DELETE FROM receipts")
            c.execute("DELETE FROM job_runs")
            c.execute("INSERT INTO delivery_leases (lease_key, holder, expires_at) VALUES ('old', 'h', %s), ('new', 'h', %s)", (AGO(hours=1), NOW + timedelta(hours=1)))
            c.execute("INSERT INTO feedback_bans (source_hash, reason, created_by, expires_at) VALUES ('ban-old', 'spam', 'x', %s), ('ban-new', 'spam', 'x', %s)", (AGO(days=1), NOW + timedelta(days=1)))
            c.execute("INSERT INTO feedback_submission_state (source_hash, expires_at) VALUES ('s-old', %s), ('s-new', %s)", (AGO(hours=1), NOW + timedelta(hours=1)))
            for job, status, age in (("a", "ok", 31), ("b", "ok", 5), ("c", "failed", 40), ("d", "failed", 95), ("e", "running", 60)):
                c.execute("INSERT INTO job_runs (job, started_at, status) VALUES (%s, %s, %s)", (job, AGO(days=age), status))
            # receipts: a stale placeholder, unknown ones (old, young, already released once, continued by a backup, one that stopped an article)
            a = c.execute("SELECT id FROM articles ORDER BY identity_key LIMIT 3").fetchall()
            c.execute("UPDATE articles SET processing_state = 'failed' WHERE id = %s", (a[0]["id"],))
            rows = [
                ("pending-stale", "pending", "prefilter_article", f"article:{a[1]['id']}@1", AGO(hours=2), None, None),
                ("unknown-old", "unknown", "score_article", f"article:{a[0]['id']}@1", AGO(hours=2), None, None),
                ("unknown-young", "unknown", "score_article", f"article:{a[1]['id']}@1", AGO(minutes=5), None, None),
                ("unknown-again", "unknown", "translate_body", "article:x@1#0", AGO(hours=2), None, "自動放行：結果未知超過 30 分鐘，未核對是否計費"),
                ("unknown-backup", "unknown", "group_article", "article:y", AGO(hours=2), {"fallbackReceiptId": 1}, None),
                ("unknown-manual", "unknown", "story_digest", "story:3@2", AGO(minutes=1), None, None),
                ("completed", "completed", "story_digest", "story:4@2", AGO(hours=3), None, None),
            ]
            for key, status, purpose, subject, updated, request, attempt_error in rows:
                rid = c.execute(
                    """INSERT INTO receipts (logical_key, service, purpose, subject, status, request, updated_at, attempts) VALUES (%s, 'llm', %s, %s, %s, %s, %s, 1) RETURNING id""",
                    (key, purpose, subject, status, Jsonb(request) if request else None, updated),
                ).fetchone()["id"]
                c.execute("INSERT INTO receipt_attempts (receipt_id, attempt, service, status, error) VALUES (%s, 1, 'llm', %s, %s)", (rid, "received" if status == "completed" else status, attempt_error))
            c.execute("INSERT INTO notify_targets (key, purpose, kind) VALUES ('t', 'alert', 'log') ON CONFLICT DO NOTHING")
            c.execute(
                "INSERT INTO deliveries (target_key, subject_kind, subject_id, dedupe_key, status, updated_at) VALUES ('t', 'k', '1', 'd1', 'sending', %s), ('t', 'k', '2', 'd2', 'pending', %s), ('t', 'k', '3', 'd3', 'sending', %s)",
                (AGO(hours=1), AGO(hours=1), AGO(minutes=1)),
            )
            # an article waiting for grouping with no job
            c.execute("UPDATE articles SET grouping_status = 'pending', processing_state = 'analyzed', created_at = %s WHERE id = %s", (AGO(hours=1), a[2]["id"]))
        root = dirs[name]
        for sub, days in (("imgcache", 31), ("imgcache", 2), ("ogcache", 40), ("ogcache", 1), ("feedback-screenshots", 9)):
            d = root / sub / "ab"
            d.mkdir(parents=True, exist_ok=True)
            f = d / f"file-{sub}-{days}"
            f.write_bytes(b"x")
            t = (NOW - timedelta(days=days)).timestamp()
            os.utime(f, (t, t))
    return urls, dirs


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        jobs = []
        for table in ("pgboss.job", "jobs"):
            try:
                jobs = c.execute(f"SELECT name, data, priority, singleton_key, state FROM {table} WHERE name = 'events.group' OR state = 'retry'").fetchall()
                break
            except psycopg.errors.UndefinedTable:
                c.rollback()
        return {
            "leases": c.execute("SELECT lease_key FROM delivery_leases ORDER BY 1").fetchall(),
            "bans": c.execute("SELECT source_hash FROM feedback_bans ORDER BY 1").fetchall(),
            "state": c.execute("SELECT source_hash FROM feedback_submission_state ORDER BY 1").fetchall(),
            "runs": c.execute("SELECT job FROM job_runs ORDER BY 1").fetchall(),
            "receipts": c.execute("SELECT logical_key, status, error FROM receipts ORDER BY logical_key").fetchall(),
            "attempts": c.execute("SELECT r.logical_key, a.status, a.error FROM receipt_attempts a JOIN receipts r ON r.id = a.receipt_id ORDER BY 1").fetchall(),
            "deliveries": c.execute("SELECT dedupe_key, status, response FROM deliveries ORDER BY 1").fetchall(),
            "audit": c.execute("SELECT actor, action, subject, reason, before, after FROM audit_log ORDER BY id").fetchall(),
            "articles": c.execute("SELECT id, processing_state, processing_attempts FROM articles WHERE processing_state IN ('failed', 'new') OR grouping_status = 'pending' ORDER BY id").fetchall(),
            "jobs": sorted((j["name"], j["priority"], j["singleton_key"], json.dumps(j["data"], sort_keys=True)) for j in jobs if j["name"] in ("events.group", "content.analyze")),
        }


def files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_retention_and_recovery(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import operations as O

    urls, dirs = world
    with psycopg.connect(urls["op_node"]) as c:
        ids = {r[1]: r[0] for r in c.execute("SELECT id, logical_key FROM receipts").fetchall()}
    with psycopg.connect(urls["op_py"]) as c:
        py_ids = {r[1]: r[0] for r in c.execute("SELECT id, logical_key FROM receipts").fetchall()}
    calls = [
        ["dailyRetention", NOW.isoformat()],
        ["recoverStaleWork"],
        ["recoverStaleWork"],  # nothing left to do
        ["releaseReceipt", ids["unknown-manual"], {"billed": False, "note": "checked the bill"}, "editor"],
        ["releaseReceipt", ids["unknown-again"], {"billed": None, "note": "authorised"}, "editor"],
        ["releaseReceipt", ids["unknown-again"], {"billed": None, "note": "twice"}, "editor"],
        ["releaseReceipt", ids["completed"], {"billed": True, "note": "n"}, "editor"],
        ["releaseReceipt", ids["unknown-young"], {"billed": True, "note": "  "}, "editor"],
        ["releaseReceipt", ids["unknown-young"], {"billed": "yes", "note": "n"}, "editor"],
        ["releaseReceipt", 999999, {"billed": True, "note": "n"}, "editor"],
    ]
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": urls["op_node"], "TZ": "UTC", "AIHOT_DATA_DIR": str(dirs["op_node"])}
    out = subprocess.run(["node", "scripts/ops-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
    want = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    monkeypatch.setenv("AIHOT_DATA_DIR", str(dirs["op_py"]))

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    got = []
    with ConnectionPool(urls["op_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for name, *args in calls:
            try:
                if name == "dailyRetention":
                    got.append(O.daily_retention(pool, datetime.fromisoformat(args[0])))
                elif name == "recoverStaleWork":
                    got.append(O.recover_stale_work(pool))
                else:
                    got.append(O.release_receipt(pool, py_ids[next(k for k, v in ids.items() if v == args[0])] if args[0] in ids.values() else args[0], args[1], args[2]))
            except Exception as error:
                got.append({"error": str(error), "name": type(error).__name__})
    for call, w, g in zip(calls, want, got, strict=True):
        if isinstance(w, dict) and "error" in w:
            assert isinstance(g, dict) and g["error"] == w["error"] and (g["name"] == w["name"] or {g["name"], w["name"]} == {"Error", "ValueError"}), (call, w, g)
        elif call[0] == "releaseReceipt" and isinstance(w, dict) and "id" in w:
            assert {k: v for k, v in g.items() if k != "id"} == {k: v for k, v in w.items() if k != "id"}, (call, w, g)
        else:
            if call[0] == "recoverStaleWork":
                w, g = {**w, "jobs": w["jobs"]}, {**g}
            assert g == w, (call, w, g)
    a, b = dump(urls["op_node"]), dump(urls["op_py"])
    for part in a:
        assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:2500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:2500]}"
    assert files(dirs["op_py"]) == files(dirs["op_node"]) and files(dirs["op_py"])
    assert got[0]["deletedLeases"] == 1 and got[0]["prunedCache"] == 2 and got[1]["released"]["released"] >= 1 and got[1]["grouping"]["enqueued"] >= 1


def test_a_job_that_failed_on_a_released_receipt_runs_again(world):
    """The Python queue's own table: a failed job whose receipt was released afterwards goes back to the queue, one whose receipt was not does not."""
    from psycopg_pool import ConnectionPool

    from newsroom.engine.operations import retry_released_receipt_jobs

    urls, _ = world
    with psycopg.connect(urls["op_py"], autocommit=True, row_factory=dict_row) as c:
        for receipt, singleton in ((424242, "released"), (434343, "not-released")):
            c.execute(
                """INSERT INTO jobs (name, policy, data, state, singleton_key, output, started_on, completed_on) VALUES ('content.analyze', 'short', '{}', 'failed', %s, %s, %s, %s)""",
                (singleton, Jsonb({"message": "x", "receiptId": receipt}), AGO(minutes=10), AGO(minutes=9)),
            )
        c.execute("INSERT INTO audit_log (actor, action, subject, reason) VALUES ('ops', 'receipt.release', 'receipt:424242', 'test')")
    with ConnectionPool(urls["op_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, open=True) as pool:
        assert retry_released_receipt_jobs(pool) == 1
    with psycopg.connect(urls["op_py"], row_factory=dict_row) as c:
        states = {r["singleton_key"]: r["state"] for r in c.execute("SELECT singleton_key, state FROM jobs WHERE singleton_key IN ('released', 'not-released')").fetchall()}
    assert states == {"released": "retry", "not-released": "failed"}
