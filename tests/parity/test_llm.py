"""chatJson and the receipts under it: the same calls against a fake model server, made by the TypeScript engine and by the Python port, each with its own copy of one
database, must send the same requests, classify the same outcomes and leave the same receipts."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

CASES = [
    {"subject": "ok-1", "user": "ok: hello"},
    {"subject": "ok-1", "user": "ok: hello"},  # the same request again: its receipt is reused
    {"subject": "fence", "user": "fence: json in a code block"},
    {"subject": "ctrl", "user": "ctrl: raw newline inside a string"},
    {"subject": "bad", "user": "bad: first answer is not JSON"},
    {"subject": "bad", "user": "bad: first answer is not JSON"},  # a fresh attempt gets a fresh answer
    {"subject": "shape", "user": "shape: valid JSON of the wrong shape"},
    {"subject": "length", "user": "length: cut off by the token limit"},
    {"subject": "params", "system": "系統提示 with emoji 😀", "user": "ok: 中文 😀 text", "temperature": 0.7, "maxTokens": 100},
    {"subject": "tagged", "user": "ok: tagged", "attemptTag": "admin:retry-1"},
    {"subject": "tagged", "user": "ok: tagged", "attemptTag": "admin:retry-1"},
    {"subject": "r429", "user": "429: throttled"},
    {"subject": "r500", "user": "500: server error"},
    {"subject": "slow", "user": "slow: answers too late", "timeoutMs": 700},
    {"subject": "slow", "user": "slow: answers too late", "timeoutMs": 700},  # an unknown outcome is not sent again
    {"subject": "r402", "user": "402: payment required"},
    {"subject": "after-stop", "user": "ok: after the stop"},  # the model is stopped after a refusal
]


class Fake:
    def __init__(self, always_ok: bool = False):
        self.always_ok = always_ok
        self.requests: list[dict] = []
        self.seen: dict[str, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("authorization"), "ctype": self.headers.get("content-type")})
                user = body["messages"][-1]["content"]
                kind = "ok" if outer.always_ok else user.split(":")[0] if isinstance(user, str) else "ok"
                n = outer.seen[user] = outer.seen.get(user, 0) + 1
                if kind in ("429", "500", "402"):
                    self.reply(int(kind), json.dumps({"error": {"code": "x", "message": "no"}}))
                    return
                if kind == "slow":
                    time.sleep(1.5)
                content = {
                    "fence": '```json\n{"answer": "fenced"}\n```', "ctrl": '{"answer": "line one\nline two"}', "shape": '{"answer": 5}', "length": "",
                    "bad": "I cannot answer that." if n == 1 else '{"answer": "second try"}',
                }.get(kind, '{"answer": "fine"}')
                self.reply(200, json.dumps({"id": f"cmpl-{kind}", "choices": [{"message": {"content": content}, "finish_reason": "length" if kind == "length" else "stop"}],
                                            "usage": {"prompt_tokens": 12, "completion_tokens": 3}}))

            def reply(self, status, text):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(text.encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


def receipts(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        rs = c.execute("SELECT logical_key, service, model, purpose, subject, status, request, attempts, usage, request_id, left(error, 60) AS error FROM receipts ORDER BY id").fetchall()
        at = c.execute("SELECT receipt_id, attempt, service, model, status FROM receipt_attempts ORDER BY id").fetchall()
        stop = c.execute("SELECT key FROM settings WHERE key LIKE 'provider.stop.%' ORDER BY key").fetchall()
    for r in rs:
        if r["status"] == "unknown":
            r["error"] = "<transport error>"
        if (r["error"] or "").startswith("unusable output: ") and r["subject"] == "shape":
            r["error"] = "unusable output: <schema message>"  # zod and the Python check word a wrong shape differently
    return rs, at, stop


# more request fields for the provider and room for reasoning on every step: both engines must send the same bodies, field order included
PROVIDER_SETTINGS = {"LLM_EXTRA_JSON": '{"enable_thinking": false}', "LLM_REASONING_TOKENS": "256"}

@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["llm_node", "llm_py"])
    admin.close()
    for url in urls.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("DELETE FROM receipts")  # the copies start with no model history
    return urls


def test_calls_requests_and_receipts(world, tmp_path, monkeypatch):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import llm
    from newsroom.engine.config import config
    from newsroom.engine.receipts import failure_kind

    node_fake, py_fake = Fake(), Fake()
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(CASES), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": world["llm_node"], "TZ": "UTC", "MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": node_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake-model", **PROVIDER_SETTINGS}
    out = subprocess.run(["node", "scripts/llm-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake-model", **PROVIDER_SETTINGS}.items():
        monkeypatch.setenv(k, v)
    config.cache_clear() if hasattr(config, "cache_clear") else None

    def schema(data):
        if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
            raise llm.SchemaError("answer must be a string")
        return data

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    py = []
    with ConnectionPool(world["llm_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for c in CASES:
            try:
                r = llm.chat_json(pool, {"model": "default", "purpose": "test_purpose", "prompt_version": "p1", "schema": schema, "subject": c["subject"], "system": c.get("system", "You answer in JSON."),
                                         "user": c["user"], "temperature": c.get("temperature"), "max_tokens": c.get("maxTokens"), "attempt_tag": c.get("attemptTag"), "timeout_ms": c.get("timeoutMs")})
                py.append({"ok": True, "data": r["data"], "reused": r["reused"], "model": r["model"], "usage": r["usage"]})
            except Exception as e:
                py.append({"ok": False, "kind": failure_kind(e), "name": type(e).__name__})
    for case, n, p in zip(CASES, node, py, strict=True):
        if n["ok"]:
            assert p == n, (case, n, p)
        else:
            assert (p["ok"], p["kind"]) == (False, n["kind"]), (case, n, p)
    # the requests the fake server received
    assert len(py_fake.requests) == len(node_fake.requests) and len(node_fake.requests) >= 10
    for a, b in zip(node_fake.requests, py_fake.requests, strict=True):
        assert (a["path"], a["auth"], a["ctype"], a["body"]) == (b["path"], b["auth"], b["ctype"], b["body"])
    assert list(node_fake.requests[0]["body"]) == list(py_fake.requests[0]["body"])  # even the order of the fields
    na, nb = receipts(world["llm_node"]), receipts(world["llm_py"])
    for x, y in zip(na[0], nb[0], strict=True):
        assert x == y
    assert [{k: v for k, v in a.items() if k != "receipt_id"} for a in na[1]] == [{k: v for k, v in a.items() if k != "receipt_id"} for a in nb[1]]
    assert na[2] == nb[2] and na[2]
    kinds = [n.get("kind") for n in node if not n["ok"]]
    assert {"unusable", "outage", "unknown", "refused", "budget"} <= set(kinds)


def test_a_backup_continues_a_request_the_primary_could_not_take(world, monkeypatch):
    """No named preset declares a backup, so the failover is checked on the Python side alone, with a registry of two services."""
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import llm
    from newsroom.engine import models as M

    primary, backup = Fake(), Fake(always_ok=True)
    registry = {
        "main": {"key": "main", "service": "svc-a", "model": "a-1", "baseUrlEnv": "A_URL", "apiKeyEnv": "A_KEY", "jsonMode": True},
        "spare": {"key": "spare", "service": "svc-b", "model": "b-1", "baseUrlEnv": "B_URL", "apiKeyEnv": "B_KEY", "jsonMode": True, "fallbackFor": {"score": "main"}},
    }
    monkeypatch.setattr(M, "models", lambda: registry)
    for k, v in {"MODEL_CALLS_ENABLED": "true", "A_URL": primary.url, "A_KEY": "ka", "B_URL": backup.url, "B_KEY": "kb"}.items():
        monkeypatch.setenv(k, v)
    assert M.fallback_model_for("score", "main") == "spare" and M.fallback_model_for("score", "spare") is None
    schema = lambda d: d  # noqa: E731

    def opts(subject, user, **kw):
        return {"model": "main", "fallback_model": "spare", "purpose": "score_article", "prompt_version": "p1", "schema": schema, "subject": subject, "system": "s", "user": user, **kw}

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(world["llm_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        ok = llm.chat_json(pool, opts("fo-1", "ok: primary answers"))
        assert ok["model"] == "main" and len(primary.requests) == 1 and not backup.requests
        down = llm.chat_json(pool, opts("fo-2", "500: primary is down"))
        assert down["model"] == "spare" and down["data"] == {"answer": "fine"} and len(backup.requests) == 1
        # The backup's answer is kept: asking again does not call anybody.
        again = llm.chat_json(pool, opts("fo-2", "500: primary is down"))
        assert again["model"] == "spare" and again["reused"] and len(primary.requests) == 2 and len(backup.requests) == 1
        # An answer the provider gives but we cannot use is not an outage: no backup.
        with pytest.raises(Exception) as e:
            llm.chat_json(pool, opts("fo-3", "length: cut off", schema=lambda d: (_ for _ in ()).throw(ValueError("no"))))
        assert type(e.value).__name__ == "ModelOutputError" and len(backup.requests) == 1
        with psycopg.connect(world["llm_py"], row_factory=dict_row) as c:
            row = c.execute("SELECT request FROM receipts WHERE service = 'svc-a' AND subject = 'fo-2'").fetchone()
        assert row["request"]["fallbackReceiptId"]
