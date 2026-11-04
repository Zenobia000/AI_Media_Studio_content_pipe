"""/api/admin/runs, nav-counts, models and processing/requeue: the same state in two copies of one database, read and changed through the TypeScript server and
the Python app, must answer the same and leave the same rows. (The queue sections read each engine's own queue tables; the same work is queued on both.)"""

from __future__ import annotations

import re
from datetime import datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.test_admin import both, same, sides  # noqa: F401  (the fixture)
from tests.parity.test_admin_sources import admin  # noqa: F401  (signs in, starts the site, creates the Python queue)

SETUP = """
INSERT INTO settings (key, value, updated_by) VALUES
  ('heartbeat.api', jsonb_build_object('pid', 11, 'host', 'h', 'release', 'dev', 'startedAt', '2026-11-01T00:00:00.000Z', 'at', '2026-11-04T00:00:00.000Z'), 'api'),
  ('heartbeat.old', jsonb_build_object('pid', 12, 'host', 'h', 'release', 'dev', 'startedAt', '2026-11-01T00:00:00.000Z', 'at', '2026-11-01T00:00:00.000Z'), 'old')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CASE WHEN EXCLUDED.updated_by = 'old' THEN now() - interval '1 day' ELSE now() END;
UPDATE settings SET updated_at = now() - interval '1 day' WHERE key = 'heartbeat.old';
INSERT INTO job_runs (job, started_at, finished_at, status, error) SELECT 'sources.schedule', now() - g * interval '1 hour', now() - g * interval '1 hour', CASE WHEN g % 4 = 0 THEN 'failed' ELSE 'ok' END,
  CASE WHEN g % 4 = 0 THEN 'boom ' || g END FROM generate_series(1, 30) g;
INSERT INTO receipts (logical_key, service, model, purpose, subject, status, request, error, attempts, created_at, updated_at) VALUES
  ('k1', 'llm', 'm', 'score_article', 'article:a1', 'unknown', '{}', 'timeout', 1, now() - interval '2 hours', now() - interval '1 hour'),
  ('k2', 'llm', 'm', 'score_article', 'article:a2', 'unknown', '{"fallbackReceiptId": 9}', NULL, 1, now() - interval '2 hours', now() - interval '1 hour'),
  ('k3', 'llm', 'm', 'summarize_article', 'article:a3', 'failed', '{}', 'bad json', 2, now() - interval '1 day', now() - interval '1 day'),
  ('k4', 'jina', NULL, 'source_listing', 'source:x', 'pending', '{}', NULL, 1, now() - interval '3 hours', now() - interval '2 hours'),
  ('k5', 'llm', 'glm-5.3-flash', 'score_article', 'article:a5', 'received', '{"promptVersion": "v7"}', NULL, 1, now() - interval '1 hour', now() - interval '1 hour');
INSERT INTO receipt_attempts (receipt_id, attempt, service, model, origin, status, usage, cost, currency, cost_basis, latency_ms, started_at)
  SELECT id, 1, 'zhipu', 'glm-5.3-flash', 'live', 'received', '{"prompt_tokens": 1200, "completion_tokens": 300, "prompt_tokens_details": {"cached_tokens": 200}}', NULL, NULL, NULL, 800 + id * 10, now() - interval '1 hour'
  FROM receipts WHERE logical_key = 'k5';
INSERT INTO receipt_attempts (receipt_id, attempt, service, model, origin, status, usage, cost, currency, cost_basis, latency_ms, started_at)
  SELECT id, g, 'zhipu', 'glm-5.3-flash', 'live', CASE WHEN g = 3 THEN 'failed' ELSE 'received' END, '{"prompt_tokens": 100, "completion_tokens": 50}', 0.002, 'CNY', 'actual', 100 * g, now() - interval '2 hours'
  FROM receipts, generate_series(2, 4) g WHERE logical_key = 'k5';
INSERT INTO service_prices (service, model, currency, input_per_mtok, cached_per_mtok, output_per_mtok) VALUES ('zhipu', 'glm-5.3-flash', 'CNY', 1.2, 0.3, 4.5), ('zhipu', '', 'CNY', 1, NULL, 3)
  ON CONFLICT DO NOTHING;
INSERT INTO notify_targets (key, purpose, kind) VALUES ('feishu', 'content', 'feishu_webhook') ON CONFLICT DO NOTHING;
INSERT INTO deliveries (target_key, subject_kind, subject_id, dedupe_key, status, attempts, response, created_at, updated_at) VALUES
  ('feishu', 'article', 'a1', 'd1', 'unknown', 1, 'timeout', now() - interval '1 hour', now() - interval '1 hour'),
  ('feishu', 'article', 'a2', 'd2', 'failed', 3, 'HTTP 500', now() - interval '2 hours', now() - interval '2 hours'),
  ('feishu', 'article', 'a3', 'd3', 'sending', 1, NULL, now() - interval '1 hour', now() - interval '1 hour'),
  ('feishu', 'article', 'a4', 'd4', 'sent', 1, 'ok', now() - interval '1 hour', now() - interval '1 hour');
INSERT INTO ingest_events (client, kind, status, summary, error) VALUES ('cli', 'items', 'ok', '{"n": 3}', NULL), ('cli', 'items', 'failed', NULL, 'bad body');
UPDATE sources SET health = 'failing', fail_count = 6, last_error = 'x', last_ok_at = now() - interval '2 days', next_fetch_at = now() - interval '2 hours'
  WHERE id = (SELECT id FROM sources ORDER BY id LIMIT 1);
UPDATE articles SET processing_state = 'failed', processing_error = 'HTTP 502 from 10.0.0.1 id 123456', discovered_at = now() - interval '1 day'
  WHERE id IN (SELECT id FROM articles ORDER BY id LIMIT 3);
UPDATE articles SET processing_state = 'failed', processing_error = 'model said no', discovered_at = now() - interval '2 days'
  WHERE id IN (SELECT id FROM articles ORDER BY id OFFSET 3 LIMIT 2);
UPDATE articles SET processing_state = 'new', processing_attempts = 2, processing_retry_at = now() + interval '10 minutes' WHERE id IN (SELECT id FROM articles ORDER BY id OFFSET 5 LIMIT 2);
UPDATE articles SET grouping_status = 'failed', grouping_error = 'no model', grouping_receipt_id = (SELECT id FROM receipts WHERE logical_key = 'k1')
  WHERE id = (SELECT article_id FROM publications WHERE selection_candidate AND eligible AND visibility = 'public' ORDER BY article_id LIMIT 1);
UPDATE articles SET grouping_status = 'pending' WHERE id IN (SELECT article_id FROM publications WHERE selection_candidate AND eligible AND visibility = 'public' ORDER BY article_id OFFSET 1 LIMIT 3);
"""


@pytest.fixture(scope="module")
def seeded(admin):  # noqa: F811
    for url in admin["urls"].values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(SETUP)
    return admin


ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def near(a, b) -> bool:
    """Equal, but for instants a couple of seconds apart (each database stamped its own `now()`)."""
    if isinstance(a, str) and isinstance(b, str) and ISO.match(a) and ISO.match(b):
        return abs(datetime.fromisoformat(a.replace("Z", "+00:00")) - datetime.fromisoformat(b.replace("Z", "+00:00"))).total_seconds() < 3
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(near(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(near(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def first_difference(a, b, path="") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k} missing on one side"
            d = first_difference(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = first_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if near(a, b) else f"{path}: {a!r} vs {b!r}"


def rows(url: str, query: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(query).fetchall()


def test_nav_counts(seeded):
    same(seeded, "GET", "/api/admin/nav-counts", 200)


def test_models(seeded):
    same(seeded, "GET", "/api/admin/models", 200)
    same(seeded, "GET", "/api/admin/models?days=30", 200)
    same(seeded, "GET", "/api/admin/models?days=500", 200)
    same(seeded, "GET", "/api/admin/models?days=abc", 200)
    same(seeded, "GET", "/api/admin/models?days=1.5", 200)
    choices = seeded["node"].request("GET", "/api/admin/models").json()["choices"]
    assert len(choices) > 3
    cap = "score"
    for body in (
        {"model": "deepseek-flash", "reason": "try"}, {"model": None, "reason": "back"}, {"model": "nope", "reason": "x"}, {"model": "deepseek-flash", "reason": "  "},
        {"model": "deepseek-flash"}, {"reason": "default again"}, {"model": "qwen3.8-flash", "reason": "again"},
    ):
        same(seeded, "POST", f"/api/admin/models/{cap}", json_body=body)
    same(seeded, "POST", "/api/admin/models/understand", json_body={"model": "deepseek-flash", "reason": "needs vision"})
    same(seeded, "POST", "/api/admin/models/nope", json_body={"model": None, "reason": "x"})
    same(seeded, "GET", "/api/admin/models", 200)
    q = "SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action = 'models.switch' ORDER BY id"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)
    q = "SELECT key, value FROM settings WHERE key LIKE 'models.%' ORDER BY key"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)


def test_runs_overview(seeded):
    # the same work waits in both queues
    for side in ("node", "py"):
        assert seeded[side].request("POST", "/api/admin/sources/" + seeded["node"].request("GET", "/api/admin/sources").json()["rows"][0]["id"] + "/fetch").status_code == 200
    a, b = both(seeded, "GET", "/api/admin/runs")
    assert a.status_code == b.status_code == 200, (a.text[:300], b.text[:300])
    ja, jb = a.json(), b.json()
    for j in (ja, jb):
        j.pop("checkedAt")
        j["jobs"] = []  # each engine lists the timers of its own scheduler
        j["processes"] = [p for p in j["processes"] if not p["role"].startswith("api:")]  # each server beats under its own port
        for p in j["processes"]:
            p.pop("at", None)
        j["queues"] = [{k: v for k, v in qd.items() if k != "oldest"} for qd in j["queues"]]
    assert near(jb, ja), first_difference(jb, ja)
    assert ja["errors"] and ja["receipts"]["issues"] and ja["deliveries"] and ja["lagging"] and ja["grouping"]["items"] and ja["retrying"]["count"] == 2
    assert {p["alive"] for p in ja["processes"]} == {True, False}


def test_requeue(seeded):
    same(seeded, "POST", "/api/admin/processing/requeue", 500, json_body={"group": None, "reason": " "})
    groups = [e["error"] for e in seeded["node"].request("GET", "/api/admin/runs").json()["errors"]]
    assert len(groups) == 2
    same(seeded, "POST", "/api/admin/processing/requeue", 200, json_body={"group": groups[0], "reason": "one group"})
    same(seeded, "POST", "/api/admin/processing/requeue", 200, json_body={"group": None, "reason": "all"})
    q = "SELECT identity_key, processing_state, processing_attempts, processing_error, processing_queued_at IS NOT NULL AS queued FROM articles ORDER BY identity_key"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)
    q = "SELECT actor, action, subject, reason, before, after FROM audit_log WHERE action = 'processing.requeue' ORDER BY id"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)


def _report():
    def case(i, gold, decision, stratum="news"):
        return {"caseId": f"c{i}", "title": f"Case {i}", "stratum": stratum, "gold": gold, "decision": decision, "score": i / 10, "relevance": "pass", "category": "ai", "reason": "r", "receiptId": None, "error": None}

    golds = ["select", "reject", "either", "select", "reject", "select"]
    a = [case(i, g, d) for i, (g, d) in enumerate(zip(golds, ["select", "select", "reject", "reject", "reject", None], strict=True))]
    b = [case(i, g, d, "tools" if i % 2 else "news") for i, (g, d) in enumerate(zip(golds, ["select", "reject", "select", "select", "select", "select"], strict=True))]
    return {"meta": {"n": 6, "seed": 3, "split": "dev", "promptVersion": "v9"}, "models": {"m-a": {"summary": {"n": 6, "f1": 0.5}, "sweep": [{"t": 1}], "cases": a}, "m-b": {"summary": {"n": 6, "f1": 0.7}, "cases": b}}}


def test_selectbench(seeded):
    bad = [
        {"report": {"models": {}}, "label": "x"}, {"report": {"models": {"m": {"summary": {"n": 1}, "cases": "no"}}}}, {"report": {"models": {"m": {"summary": {"n": 1}, "cases": [{"caseId": " "}]}}}},
        {"report": {"models": {"m": {"summary": {"n": 1}, "cases": [{"caseId": "a", "gold": "select"}, {"caseId": "a", "gold": "select"}]}}}},
        {"report": {"models": {"m": {"summary": {"n": 1}, "cases": [{"caseId": "a", "gold": "maybe"}]}}}},
        {"report": {"models": {"m": {"summary": {"n": 1}, "cases": [{"caseId": "a", "gold": "select"}]}, "n": {"summary": {"n": 1}, "cases": [{"caseId": "a", "gold": "reject"}]}}}},
    ]
    for body in bad:
        same(seeded, "POST", "/api/admin/selectbench/import", 400, json_body=body)
    ids = {}
    for side in ("node", "py"):
        out = seeded[side].request("POST", "/api/admin/selectbench/import", json_body={"report": _report(), "label": "compare"})
        assert out.status_code == 200, out.text
        ids[side] = out.json()["id"]
    assert ids["node"][:12] == ids["py"][:12]

    def masked(side, text):
        return text.replace(ids[side], "<run>") if isinstance(text, str) else text

    la, lb = (seeded[s].request("GET", "/api/admin/selectbench").json() for s in ("node", "py"))
    for j, side in ((la, "node"), (lb, "py")):
        for r in j["runs"]:
            r["id"] = masked(side, r["id"])
            r["created_at"] = "<t>"
    assert la == lb and len(la["runs"]) == 1 and la["runs"][0]["cases"] == 12
    for query in ("", "?model=m-b", "?outcome=fp", "?outcome=fn&model=m-b", "?outcome=tp", "?outcome=tn", "?outcome=either", "?outcome=error", "?stratum=tools", "?disagree=1", "?outcome=zz"):
        a, b = (seeded[s].request("GET", f"/api/admin/selectbench/{ids[s]}{query}") for s in ("node", "py"))
        assert a.status_code == b.status_code == 200, query
        ja, jb = a.json(), b.json()
        for j, side in ((ja, "node"), (jb, "py")):
            j["run"]["id"] = "<run>"
            j["run"]["created_at"] = "<t>"
            j["run"].pop("imported_by", None)
        assert ja == jb, query
    assert seeded["node"].request("GET", "/api/admin/selectbench/nope").status_code == seeded["py"].request("GET", "/api/admin/selectbench/nope").status_code == 404
    q = "SELECT label, split, sample_size, seed, prompt_version, models, summary, imported_by FROM selectbench_runs ORDER BY created_at"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)
    q = "SELECT model, case_id, title, stratum, gold, decision, score, relevance, category, reason FROM selectbench_results ORDER BY model, case_id"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)
    q = "SELECT actor, action, reason, before, after FROM audit_log WHERE action = 'selectbench.import' ORDER BY id"
    assert rows(seeded["urls"]["py"], q) == rows(seeded["urls"]["node"], q)
