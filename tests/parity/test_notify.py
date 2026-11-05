"""Notifications: the same pushes, deliveries, alerts and agent events sent by the TypeScript engine and by the Python port - to local servers standing in for the Feishu bots
and the hub, each on its own copy of one database - must send the same requests and leave the same deliveries, leases, settings and audit."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin


class Hooks:
    """Stands in for the content groups' bots, the alert group's bot and the hub: records every request, answers by the path."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
                path = self.path
                if path.startswith("/tell"):
                    raw = re.sub(r'"key":"[^"]*"', '"key":"K"', raw)
                if path.startswith("/hub") and self.headers.get("authorization") != "Bearer hub-key":
                    path += " BAD-AUTH"
                outer.requests.append((path, raw))
                if path.startswith("/unknown"):
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(b"<html>bad gateway</html>")
                    return
                if path.startswith("/tell-refused"):
                    self.send_response(503)
                    self.end_headers()
                    return
                code = 19001 if path.startswith("/fail") else 0
                data = json.dumps({"code": code, "msg": "x"}).encode()
                self.send_response(200 if not path.startswith("/hub-down") else 500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["nt_node", "nt_py"])
    admin.close()
    from pathlib import Path

    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True) as c:
            if name == "nt_py":
                c.execute(migration.read_text(encoding="utf-8"))
            c.execute("DELETE FROM deliveries")
            c.execute("DELETE FROM delivery_leases")
            c.execute("DELETE FROM settings WHERE key LIKE 'webhook.event.%'")
    return urls


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {
            "targets": c.execute("SELECT key, purpose, kind, enabled, config_ref FROM notify_targets ORDER BY key").fetchall(),
            "deliveries": c.execute("SELECT target_key, subject_kind, subject_id, dedupe_key, status, attempts, payload, response FROM deliveries ORDER BY id").fetchall(),
            "leases": c.execute("SELECT lease_key, holder FROM delivery_leases ORDER BY 1").fetchall(),
            "events": [
                {**r["value"], "queuedAt": "T"} for r in c.execute("SELECT value FROM settings WHERE key LIKE 'webhook.event.%' ORDER BY value->'event'->>'id'").fetchall()
            ],
            "audit": c.execute("SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action LIKE 'delivery.%' ORDER BY id").fetchall(),
        }


def test_pure_formatters(settings_env, tmp_path):
    from newsroom.engine import notify_feishu as F

    since = "2026-11-04T01:00:00.000Z"
    f = {"key": "k", "level": "now", "title": "網站停了", "impact": "讀者看不到", "heals": "不會", "action": "重啟"}
    calls = [["duration", ms] for ms in (0, 30_000, 61_000, 3_600_000, 5_400_000, 200_000_000, 300_000_000)]
    calls += [["formatAlert", f, since, int(datetime(2026, 11, 4, 3, tzinfo=UTC).timestamp() * 1000), rep] for rep in (False, True)]
    calls += [["formatAlert", {**f, "level": "later", "impact": None}, since, int(datetime(2026, 11, 4, 1, 0, 30, tzinfo=UTC).timestamp() * 1000), False]]
    calls += [["formatReturn", f, 3], ["formatRecovery", "網站停了", since, int(datetime(2026, 11, 4, 5, tzinfo=UTC).timestamp() * 1000)]]
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x", "TZ": "UTC"}
    node = json.loads(next(line for line in reversed(subprocess.run(["node", "scripts/notify-run.ts", str(path)], cwd=AIHOT, env=env, capture_output=True, timeout=300).stdout.decode().splitlines()) if line.startswith("[")))
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))

    def finding(d):
        return F.Finding(key=d["key"], level=d["level"], title=d["title"], impact=d.get("impact"), heals=d.get("heals"), action=d.get("action"))

    got = [F.duration(c[1]) for c in calls[:7]]
    got += [F.format_alert(finding(c[1]), since_dt, c[3], c[4]) for c in calls[7:10]]
    got += [F.format_return(finding(calls[10][1]), 3), F.format_recovery("網站停了", since_dt, calls[11][3])]
    assert got == node[: len(got)] if False else got == node


def test_pushes_deliveries_alerts_and_events(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import notify_agent as A
    from newsroom.engine import notify_deliver as D
    from newsroom.engine import notify_feishu as F
    from newsroom.engine import notify_selected as S

    node_hooks, py_hooks = Hooks(), Hooks()
    now = "2026-11-04T02:40:00Z"
    later = "2026-11-06T02:40:00Z"

    def env_for(h: Hooks) -> dict:
        return {
            "FEISHU_CONTENT_PUSH_ENABLED": "true", "FEISHU_PUSH_WEBHOOK_URL": f"{h.url}/ok", "FEISHU_PUSH_MIRROR_WEBHOOK_URL": f"{h.url}/unknown",
            "FEISHU_INTERNAL_ENABLED": "true", "FEISHU_ALERT_WEBHOOK_URL": f"{h.url}/alert", "WEBHOOK_HUB_URL": f"{h.url}/hub", "WEBHOOK_HUB_KEY": "hub-key",
            "AIHOT_ENVIRONMENT": "staging", "SITE_URL": "https://example.test",
        }

    for url in world.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("INSERT INTO notify_targets (key, purpose, kind, enabled, enabled_at, config_ref) VALUES ('feishu-content-main', 'content', 'feishu_webhook', true, '2026-01-01', 'FEISHU_PUSH_WEBHOOK_URL') ON CONFLICT (key) DO UPDATE SET enabled = true, enabled_at = '2026-01-01'")
            c.execute("INSERT INTO notify_targets (key, purpose, kind, enabled, enabled_at, config_ref) VALUES ('feishu-content-mirror', 'content', 'feishu_webhook', true, '2026-01-01', 'FEISHU_PUSH_MIRROR_WEBHOOK_URL') ON CONFLICT (key) DO UPDATE SET enabled = true, enabled_at = '2026-01-01'")
    with psycopg.connect(world["nt_node"], row_factory=dict_row) as c:
        sel = [r["article_id"] for r in c.execute("SELECT p.article_id FROM publications p JOIN sources s ON s.id = p.source_id WHERE p.selected ORDER BY p.timeline_at DESC").fetchall()]
        fact1 = c.execute("SELECT article_id FROM fact_articles WHERE fact_id = 1").fetchall()
    t1_nofact, t2, t1_fact1, t1_fact4 = sel[0], sel[1], sel[2], sel[3]
    for url in world.values():  # a delivery re-reads the item against the real clock, so these reports are made recent
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("UPDATE publications SET timeline_at = now() - interval '1 hour' WHERE article_id = ANY(%s)", (sel[:4],))
    long_text = "\n".join(f"第 {i} 行的告警內容，說明影響與處理方式。" * 4 for i in range(400))
    finding = {"key": "worker", "level": "now", "title": "後臺停了", "impact": "網站停更", "detail": "細節", "since": "2026-11-04T01:00:00.000Z"}
    calls = [
        ["pushSelected", t1_nofact, now], ["pushSelected", t1_nofact, now],  # the second finds it sent
        ["pushSelected", t2, now],  # not a first-party source
        ["pushSelected", t1_fact1, now], ["pushSelected", t1_fact4, now],
        ["pushSelected", t1_nofact, later],  # not live any more
        ["pushSelected", "no-such-article", now],
        ["sendAlert", "標題", ["影響：一", "你需要：二"]], ["sendAlert", "很長的告警", [long_text]],
        ["wakeAgent", finding, 1_762_000_000_000], ["wakeAgent", finding, 1_762_000_000_000], ["wakeAgent", {**finding, "key": "x" * 150, "agentOnly": True, "agentType": "alert.review"}, 1_762_000_000_000],
        ["wakeDigest", "2026-11-04", 3], ["flushAgentEvents"],
        ["handoffs", "worker", "2026-11-01T00:00:00Z"], ["agentTransportFindings", 1_762_000_000_000],
        ["markStaleDeliveries"],
    ]  # fmt: skip
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, **env_for(node_hooks), "DATABASE_URL": world["nt_node"], "TZ": "UTC", "AIHOT_DATA_DIR": str(tmp_path / "node")}
    out = subprocess.run(["node", "scripts/notify-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
    want = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    for k, v in env_for(py_hooks).items():
        monkeypatch.setenv(k, v)

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    got = []
    with ConnectionPool(world["nt_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        D.ensure_content_targets(pool)
        for name, *a in calls:
            try:
                if name == "pushSelected":
                    r = S.push_selected(pool, a[0], datetime.fromisoformat(a[1].replace("Z", "+00:00")))
                    got.append(r)
                elif name == "sendAlert":
                    got.append(F.send_alert(a[0], a[1]))
                elif name == "wakeAgent":
                    d = a[0]
                    f = F.Finding(key=d["key"], level=d["level"], title=d["title"], impact=d.get("impact"), detail=d.get("detail"), since=datetime.fromisoformat(d["since"].replace("Z", "+00:00")), agent_only=bool(d.get("agentOnly")), agent_type=d.get("agentType"))
                    got.append(A.wake_agent(pool, f, a[1]))
                elif name == "wakeDigest":
                    got.append(A.wake_digest(pool, a[0], a[1]))
                elif name == "flushAgentEvents":
                    A.flush_agent_events(pool)
                    got.append(None)
                elif name == "handoffs":
                    got.append(A.handoffs(pool, a[0], datetime.fromisoformat(a[1].replace("Z", "+00:00"))))
                elif name == "agentTransportFindings":
                    got.append([{"key": x.key, "level": x.level, "owner": x.owner, "title": x.title, "impact": x.impact, "heals": x.heals, "action": x.action, "detail": x.detail} for x in A.agent_transport_findings(pool, a[0])])
                else:
                    got.append(D.mark_stale_deliveries(pool))
            except Exception as error:
                got.append({"error": str(error)})
    for call, w, g in zip(calls, want, got, strict=True):
        if isinstance(g, dict) and "after" in g:
            g = {**g, "after": g["after"].isoformat().replace("+00:00", ".000Z")}
        assert g == w, (call[:2], w, g)
    a, b = dump(world["nt_node"]), dump(world["nt_py"])
    for part in a:
        assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:2500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:2500]}"
    assert sorted(py_hooks.requests) == sorted(node_hooks.requests) and len(py_hooks.requests) > 8

    # deliveries in doubt, resolved by hand: confirmed, given up, sent again
    with psycopg.connect(world["nt_node"], row_factory=dict_row) as c:
        unknown = [r["id"] for r in c.execute("SELECT id FROM deliveries WHERE status = 'unknown' ORDER BY id").fetchall()]
    assert unknown
    resolve = [["resolveDelivery", unknown[0], {"outcome": "sent", "note": "checked"}, "editor"], ["resolveDelivery", unknown[0], {"outcome": "drop", "note": "again"}, "editor"],
               ["resolveDelivery", 999999, {"outcome": "drop", "note": "n"}, "editor"], ["resolveDelivery", unknown[-1], {"outcome": "resend", "note": "missing"}, "editor"]]  # fmt: skip
    path.write_text(json.dumps({"calls": resolve}), encoding="utf-8")
    out = subprocess.run(["node", "scripts/notify-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
    want = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))
    got = []
    with ConnectionPool(world["nt_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for _n, i, inp, actor in resolve:
            try:
                got.append(D.resolve_delivery(pool, i, inp, actor))
            except Exception as error:
                got.append({"error": str(error)})
    for call, w, g in zip(resolve, want, got, strict=True):
        if isinstance(w, dict) and "error" in w:
            assert isinstance(g, dict) and g["error"] == w["error"], (call, w, g)
        else:
            assert g == w, (call, w, g)
    a, b = dump(world["nt_node"]), dump(world["nt_py"])
    for part in a:
        assert b[part] == a[part], part
    assert sorted(py_hooks.requests) == sorted(node_hooks.requests)
    assert timedelta
