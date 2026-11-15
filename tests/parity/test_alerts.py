"""Operations alerts, the 09:00 list, the Monday source report, the worker watchdog and backups: the same rounds run by the TypeScript engine and by the Python port, each on
its own copy of one database, against local stand-ins for the alert group and the hub, must find the same problems, send the same messages and keep the same state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_notify import Hooks
from tests.parity.test_publish import _admin

NOW = int(time.time() * 1000) // 60_000 * 60_000
MIN = 60_000
# pg_dump for the backup: the one on PATH (Linux, the checks on GitHub), else where the Windows installer puts PostgreSQL 17.
PG_BIN = Path(shutil.which("pg_dump") or "C:/Program Files/PostgreSQL/17/bin/pg_dump").parent


def at(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["al_node", "al_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            if name == "al_py":
                c.execute(migration.read_text(encoding="utf-8"))
    return urls


def break_things(urls) -> None:
    """A site that stopped collecting, with a backlog, a refused paid service, unknown outcomes, a refused push and a selected report waiting for grouping."""
    for url in urls.values():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            for table in ("receipt_attempts", "receipts", "deliveries", "fetch_runs"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM settings WHERE key LIKE 'alerts.%' OR key LIKE 'webhook.event.%'")
            c.execute("DELETE FROM job_runs")
            c.execute("UPDATE articles SET discovered_at = %s, processing_state = 'analyzed'", (at(NOW - 8 * 60 * MIN),))
            ids = [r["id"] for r in c.execute("SELECT id FROM articles ORDER BY identity_key LIMIT 12").fetchall()]
            c.execute("UPDATE articles SET processing_state = 'new', processing_error = 'timeout' WHERE id = ANY(%s)", (ids,))
            rid = c.execute("INSERT INTO receipts (logical_key, service, model, purpose, status, updated_at) VALUES ('refused', 'llm', 'fake', 'score_article', 'failed', now()) RETURNING id").fetchone()["id"]
            c.execute("INSERT INTO receipt_attempts (receipt_id, attempt, service, model, status, error, started_at) VALUES (%s, 1, 'llm', 'fake', 'failed', 'llm HTTP 402: {\"code\":\"x\"}', %s)", (rid, at(NOW - 10 * MIN)))
            c.execute("INSERT INTO receipts (logical_key, service, purpose, status, updated_at) VALUES ('unknown-1', 'llm', 'story_digest', 'unknown', now())")
            c.execute("INSERT INTO notify_targets (key, purpose, kind, enabled, enabled_at, note) VALUES ('feishu-content-main', 'content', 'feishu_webhook', true, '2026-01-01', '內容主群') ON CONFLICT (key) DO NOTHING")
            c.execute("INSERT INTO deliveries (target_key, subject_kind, subject_id, dedupe_key, status, response, updated_at) VALUES ('feishu-content-main', 'selected', 'x', 'd1', 'failed', 'code 19001', %s)", (at(NOW - 20 * MIN),))
            sel = c.execute("SELECT article_id FROM publications WHERE selected ORDER BY timeline_at DESC LIMIT 1").fetchone()["article_id"]
            c.execute("UPDATE publications SET selection_candidate = true, eligible = true, visibility = 'public', selected_ready_at = %s WHERE article_id = %s", (at(NOW - 3 * 60 * MIN), sel))
            c.execute("UPDATE articles SET grouping_status = 'pending' WHERE id = %s", (sel,))
            # The wait is counted from the latest of several times, the revision's among them: on a database seeded minutes ago that one is now.
            c.execute("UPDATE article_revisions SET created_at = %s WHERE article_id = %s", (at(NOW - 3 * 60 * MIN), sel))


def mend(urls) -> None:
    for url in urls.values():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            c.execute("UPDATE articles SET discovered_at = %s, processing_state = 'analyzed', grouping_status = 'complete'", (at(NOW + 65 * MIN),))
            c.execute("DELETE FROM receipt_attempts")
            c.execute("DELETE FROM receipts")
            c.execute("UPDATE deliveries SET status = 'sent'")
            c.execute("INSERT INTO reports (kind, key, window_start, window_end, content, generated_at) SELECT 'daily', d::date::text, now(), now(), '{}', now() FROM generate_series(now()::date - 8, now()::date + 3, '1 day') d ON CONFLICT DO NOTHING")


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        settings = c.execute("SELECT key, value FROM settings WHERE key LIKE 'alerts.%' OR key LIKE 'webhook.event.%' OR key IN ('watchdog.worker', 'backup.last') ORDER BY key").fetchall()
        for s in settings:
            if s["key"].startswith("webhook.event."):
                s["value"] = {**s["value"], "queuedAt": "T"}
            if s["key"] == "backup.last":
                s["value"] = {"uploaded": s["value"]["uploaded"], "objects": [o["key"].split(":")[0] for o in s["value"]["objects"]]}
        return {"settings": settings, "audit": c.execute("SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action LIKE 'alert.%%' OR action LIKE 'followup.%%' ORDER BY id").fetchall()}


class Side:
    def __init__(self, world, tmp_path, monkeypatch):
        from psycopg_pool import ConnectionPool

        from newsroom.core.db import _FloatNumeric

        self.world, self.tmp_path, self.monkeypatch = world, tmp_path, monkeypatch
        self.node_hooks, self.py_hooks = Hooks(), Hooks()
        self.pool = ConnectionPool(world["al_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=lambda c: c.adapters.register_loader("numeric", _FloatNumeric), open=True)

    def env(self, hooks: Hooks, hub: bool, extra: dict | None = None) -> dict:
        env = {
            "COLLECT_ENABLED": "true", "MODEL_CALLS_ENABLED": "true", "FEISHU_INTERNAL_ENABLED": "true", "FEISHU_ALERT_WEBHOOK_URL": f"{hooks.url}/alert", "AIHOT_ENVIRONMENT": "staging",
            "SITE_URL": "https://example.test", "WEBHOOK_HUB_URL": f"{hooks.url}/hub" if hub else "", "WEBHOOK_HUB_KEY": "hub-key" if hub else "", **(extra or {}),
        }  # fmt: skip
        return env

    def run(self, calls: list[list], hub: bool = False, extra: dict | None = None):
        from newsroom.engine import operations_alerts as A
        from newsroom.engine import operations_backup as B
        from newsroom.engine import operations_reports as R

        path = self.tmp_path / "in.json"
        path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
        env = {**os.environ, **ENV, **self.env(self.node_hooks, hub, extra), "DATABASE_URL": self.world["al_node"], "TZ": "UTC", "AIHOT_DATA_DIR": str(self.tmp_path / "node-data"), "PATH": f"{PG_BIN}{os.pathsep}{os.environ['PATH']}"}
        out = subprocess.run(["node", "scripts/ops-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
        want = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))
        for k, v in {**self.env(self.py_hooks, hub, extra), "AIHOT_DATA_DIR": str(self.tmp_path / "py-data"), "PATH": f"{PG_BIN}{os.pathsep}{os.environ['PATH']}", "DATABASE_URL": self.world["al_py"]}.items():
            self.monkeypatch.setenv(k, v)
        got = []
        for name, *a in calls:
            try:
                if name == "checkAlerts":
                    got.append(A.check_alerts(self.pool, a[0]))
                elif name == "sendDigest":
                    got.append(A.send_digest(self.pool, a[0]))
                elif name == "collectFindings":
                    got.append([{"key": f.key, "level": f.level, "title": f.title, "impact": f.impact, "heals": f.heals, "action": f.action, "detail": f.detail, "owner": f.owner} for f in A.collect_findings(self.pool, a[0])])
                elif name == "sourceHealthWeekly":
                    got.append(R.source_health_weekly(self.pool, a[0]))
                elif name == "holdAlert":
                    got.append(A.hold_alert(self.pool, a[0], a[1], datetime.fromisoformat(a[2].replace("Z", "+00:00")), a[3], a[4]))
                elif name == "dismissFollowUp":
                    got.append(A.dismiss_follow_up(self.pool, a[0], a[1], a[2], a[3], a[4]))
                elif name == "restoreFollowUp":
                    got.append(A.restore_follow_up(self.pool, a[0], a[1], a[2]))
                elif name == "runBackup":
                    got.append(B.run_backup(self.pool, datetime.fromisoformat(a[0].replace("Z", "+00:00"))))
                elif name == "checkWorkerHeartbeat":
                    from newsroom.engine.operations_watch import check_worker_heartbeat

                    check_worker_heartbeat(self.pool)
                    got.append(None)
            except Exception as error:
                got.append({"error": str(error)})
        return want, got

    def compare(self, label: str, calls, want, got) -> None:
        for call, w, g in zip(calls, want, got, strict=True):
            if isinstance(w, dict) and set(w) == {"error", "name"}:
                w = {"error": w["error"]}  # the class name of an error is the language's
            assert g == w, (label, call[:3], w, g)
        a, b = dump(self.world["al_node"]), dump(self.world["al_py"])
        for part in a:
            assert b[part] == a[part], f"{label}: {part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:3000]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:3000]}"
        assert sorted(self.py_hooks.requests) == sorted(self.node_hooks.requests), label


def test_owner_alerts_repeats_and_recovery(world, monkeypatch, tmp_path):
    side = Side(world, tmp_path, monkeypatch)
    break_things(world)
    calls = [["collectFindings", NOW], ["checkAlerts", NOW], ["checkAlerts", NOW + 10 * MIN], ["checkAlerts", NOW + 61 * MIN]]
    want, got = side.run(calls)
    side.compare("breaking", calls, want, got)
    keys = {f["key"] for f in got[0]}
    assert {"content.collect", "content.process", "content.grouping", "deliveries.failed", "receipts.unknown"} <= keys and any(k.startswith("provider.refused.") for k in keys), keys
    assert len(side.py_hooks.requests) >= 8
    mend(world)
    calls = [["checkAlerts", NOW + 70 * MIN], ["sendDigest", NOW + 80 * MIN], ["sourceHealthWeekly", NOW + 80 * MIN]]
    want, got = side.run(calls)
    side.compare("mended", calls, want, got)
    assert any("recovered" in s for s in got[0]["sent"]), got[0]


def test_alerts_handed_to_the_agent(world, monkeypatch, tmp_path):
    side = Side(world, tmp_path, monkeypatch)
    break_things(world)
    t0 = NOW + 200 * MIN
    key = "content.collect"
    calls = [["checkAlerts", t0], ["checkAlerts", t0 + 10 * MIN], ["holdAlert", key, "a quiet weekend", at(t0 + 12 * MIN).isoformat(), "agent", t0 + 11 * MIN],
             ["holdAlert", key, "", at(t0 + 12 * MIN).isoformat(), "agent", t0 + 11 * MIN], ["checkAlerts", t0 + 61 * MIN], ["checkAlerts", t0 + 130 * MIN], ["sendDigest", t0 + 140 * MIN]]  # fmt: skip
    want, got = side.run(calls, hub=True)
    side.compare("handoff", calls, want, got)
    assert any(path.startswith("/hub") for path, _ in side.py_hooks.requests)
    assert got[3] == {"error": "reason is required"}


def test_watchdog_and_backup(world, monkeypatch, tmp_path):
    side = Side(world, tmp_path, monkeypatch)
    break_things(world)
    for url in world.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DELETE FROM settings WHERE key IN ('watchdog.worker', 'heartbeat.worker')")
            c.execute("INSERT INTO settings (key, value, updated_by, updated_at) VALUES ('heartbeat.worker', '{}', 'worker', date_trunc('day', now()) - interval '4 days')")
    calls = [["checkWorkerHeartbeat"], ["checkWorkerHeartbeat"]]
    want, got = side.run(calls)
    side.compare("watchdog", calls, want, got)
    assert any("後臺處理服務停了" in raw for _, raw in side.py_hooks.requests)
    if not shutil.which("pg_dump", path=str(PG_BIN)):
        pytest.skip("pg_dump is not installed")
    calls = [["runBackup", "2026-11-05T04:10:00Z"]]
    want, got = side.run(calls)
    assert got[0] == {"error": "backup made locally but not uploaded: object store credentials are not configured"} and want[0]["error"] == got[0]["error"]
    a, b = dump(world["al_node"]), dump(world["al_py"])
    assert b["settings"] == a["settings"]
    assert sorted(p.name.split("-")[-1][:0] + p.suffix for p in (tmp_path / "py-data" / "backups").iterdir()) == sorted(p.suffix for p in (tmp_path / "node-data" / "backups").iterdir())


def test_a_signed_upload(settings_env, tmp_path):
    """The signature is checked against a worked example of the scheme (Signature Version 4 for S3, the documented GET example), since both engines sign with the clock."""
    from newsroom.engine.operations_backup import sign_v4

    headers = sign_v4(
        "GET", "https://examplebucket.s3.amazonaws.com/test.txt", "us-east-1", "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", {"range": "bytes=0-9"}, datetime(2013, 5, 24, tzinfo=UTC),
    )  # fmt: skip
    assert headers["authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
