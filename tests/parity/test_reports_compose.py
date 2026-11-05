"""Daily, weekly and monthly reports: the same issues composed by the TypeScript engine and by the Python port, each on its own copy of one database, against fake
writer models, must carry the same entries in the same order and leave the same reports, revisions and receipts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin


class Fake:
    """A period writer: a grounded overview and introductions, or (by the request's digest) text naming what the events do not, or text that is too long."""

    def __init__(self):
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                user = body["messages"][-1]["content"]
                outer.requests.append(user)
                n = int(hashlib.sha1(user.encode()).hexdigest(), 16) % 3
                labels = [line[1:-1] for line in user.split("\n") if line.startswith("【") and line.endswith("】")]
                if n == 0:
                    overview = "本期有多項重要進展，整體節奏穩定。" + "後續值得持續關注。" * 40
                    sections = {label: "這一欄收錄了本期的主要進展。" for label in labels}
                elif n == 1:
                    overview, sections = "本期 Zorbo-9 發布，達到 98.7% 的成績。", {label: "Zorbo-9 領先。" for label in labels}
                else:
                    overview, sections = "本期要聞集中。後面的句子也很短。", {label: "進展集中。" for label in labels[:1]}
                content = json.dumps({"overview": overview, "sections": sections}, ensure_ascii=False)
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


def node(tmp_path, op, calls, env_extra=None):
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"op": op, "calls": calls}), encoding="utf-8")
    env = {**os.environ, **ENV, "TZ": "UTC", **(env_extra or {})}
    out = subprocess.run(["node", "scripts/reports-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


def test_due_dates_and_texts(settings_env, tmp_path):
    from newsroom.engine import reports_compose as R

    nows = ["2026-11-05T03:00:00Z", "2026-11-05T00:30:00Z", "2026-11-02T01:59:00Z", "2026-11-02T02:30:00Z", "2026-11-01T02:29:00Z", "2026-11-01T02:31:00Z", "2026-01-01T00:00:00Z", "2026-12-31T23:59:00Z"]
    texts = ["本期要聞。後面還有。", "第一句。第二句。第三句。", "沒有句號的一段話", "", "這是一句很長很長很長很長很長很長很長很長很長很長很長很長很長很長的話。短。"]
    corpus = "OpenAI released GPT-5 with 845 points; Google followed 谷歌 129.3 40%"
    claims = ["OpenAI 發布 GPT-5", "Acme 發布 Nova-2.5", "得分 845", "得分 12", "提升 40%", "提升 41%", "谷歌也跟進", "百度也跟進", "ai 與 API", "129.3 分", "Anthropic 發布 Claude"]
    groups = [{"label": "模型", "items": [{"title": f"事件 {i}", "summary": "摘要" * 80} for i in range(3)]}, {"label": "產品", "items": [{"title": "單一事件", "summary": "x"}]}]
    calls = [[f"due{k}", n] for k in ("Daily", "Weekly", "Monthly") for n in nows]
    calls += [["fitted", t, m] for t in texts for m in (5, 12, 400)] + [["grounded", c, corpus] for c in claims]
    calls += [["periodPrompt", "weekly", "2026-11-02", "2026-11-08", groups], ["periodPrompt", "monthly", "2026-11-01", "2026-11-30", groups[1:]], ["periodPrompt", "weekly", "2026-11-02", "2026-11-08", []]]
    want = node(tmp_path, "pure", calls)
    got = [getattr(R, f"due_{k.lower()}")(__import__("datetime").datetime.fromisoformat(n.replace("Z", "+00:00"))) for k in ("Daily", "Weekly", "Monthly") for n in nows]
    got += [R.fitted(t, m) for t in texts for m in (5, 12, 400)] + [R.grounded(c, corpus) for c in claims]
    got += [R.period_prompt("weekly", "2026-11-02", "2026-11-08", groups), R.period_prompt("monthly", "2026-11-01", "2026-11-30", groups[1:]), R.period_prompt("weekly", "2026-11-02", "2026-11-08", [])]
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, (i, calls[i][:2], g, w)


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["rc_node", "rc_py"])
    admin.close()
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DELETE FROM receipts")
    return urls


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {
            "reports": c.execute("SELECT kind, key, window_start, window_end, content, model, origin, revision FROM reports ORDER BY kind, key").fetchall(),
            "revisions": c.execute("SELECT r.kind, r.key, v.revision, v.content, v.reason FROM report_revisions v JOIN reports r ON r.id = v.report_id ORDER BY r.kind, r.key, v.revision").fetchall(),
            "receipts": c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall(),
        }


def test_composing_issues(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import reports_compose as R
    from newsroom.engine.models import invalidate_model_cache

    node_fake, py_fake = Fake(), Fake()
    base = {"MODEL_CALLS_ENABLED": "true", "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}
    for k, v in {**base, "LLM_BASE_URL": py_fake.url}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()
    calls = [
        ["composeDaily", "2026-11-03"],  # already there
        ["composeDaily", "2026-11-01"], ["composeDaily", "2026-11-02"], ["composeDaily", "2026-11-04"], ["composeDaily", "2026-11-05"], ["composeDaily", "2026-11-06"],
        ["composeDaily", "2026-11-04", "regenerated"],
        ["composeWeekly", "2026-W45"], ["composeWeekly", "2026-W44"], ["composeWeekly", "2026-W45", "again"], ["composeWeekly", "bad"],
        ["composeMonthly", "2026-11"], ["composeMonthly", "2026-10"], ["composeMonthly", "nope"],
        ["composeDueReports", "2026-11-05T03:00:00Z", 8],
        ["composeDueReports", "2026-11-12T03:00:00Z", 3],  # days with nothing judged fail without holding up the rest
    ]  # fmt: skip
    env = {**base, "DATABASE_URL": world["rc_node"], "LLM_BASE_URL": node_fake.url, "AIHOT_DATA_DIR": str(tmp_path / "node-data")}
    want = node(tmp_path, "run", calls, env)

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    got = []
    with ConnectionPool(world["rc_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        from datetime import datetime

        for name, *args in calls:
            try:
                if name == "composeDueReports":
                    got.append(R.compose_due_reports(pool, datetime.fromisoformat(args[0].replace("Z", "+00:00")), args[1]))
                else:
                    fn = {"composeDaily": R.compose_daily, "composeWeekly": R.compose_weekly, "composeMonthly": R.compose_monthly}[name]
                    r = fn(pool, *args)
                    got.append(r)
            except Exception as error:
                got.append({"error": str(error)})
    for call, w, g in zip(calls, want, got, strict=True):
        assert g == w, (call, w, g)
    a, b = dump(world["rc_node"]), dump(world["rc_py"])
    for part in a:
        assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:3000]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:3000]}"
    assert sorted(py_fake.requests) == sorted(node_fake.requests)
    print([(c[1], g) for c, g in zip(calls, got, strict=True)])
    assert any(isinstance(g, dict) and g.get("entries", 0) > 0 for g in got) and any(isinstance(g, dict) and "error" in g for g in got)
    kinds = {r["kind"] for r in b["reports"]}
    assert kinds == {"daily", "weekly", "monthly"} and b["revisions"], (kinds, len(b["revisions"]))
