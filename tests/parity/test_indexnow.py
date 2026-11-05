"""The complete discovery inventory and IndexNow: the same inventory read by the TypeScript engine and by the Python port from one database must list the same addresses
with the same content identities (a topic page's identity is the one exception, documented in operations_indexnow), and a submission must find the same changes."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV
from tests.parity.test_publish import _admin  # noqa: F401


def run_node(db, calls, env=None):
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"calls": calls}, f)
    full = {**os.environ, **ENV, **(env or {}), "DATABASE_URL": db, "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/ops-run.ts", f.name], cwd=AIHOT, env=full, check=True, capture_output=True, timeout=600)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


def test_inventory_and_submission(database_url, settings_env, monkeypatch):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.operations_indexnow import load_discovery_entries, submit_index_now

    now = datetime.now(UTC).replace(microsecond=0)
    stamp = now.isoformat().replace("+00:00", "Z")
    want = run_node(database_url, [["discovery", stamp]])[0]
    with ConnectionPool(database_url, min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=lambda c: c.adapters.register_loader("numeric", _FloatNumeric), open=True) as pool:
        got = [{"loc": e["loc"], "revision": e.get("revision"), "lastmod": e["lastmod"].isoformat().replace("+00:00", ".000Z") if e.get("lastmod") else None} for e in load_discovery_entries(pool, now)]
    assert [e["loc"] for e in got] == [e["loc"] for e in want]
    different = [(g["loc"], g["revision"], w["revision"]) for g, w in zip(got, want, strict=True) if g != w]
    # only the topic pages' own identities (and the topic list that holds them) may differ
    assert all(loc.startswith("/topics") for loc, _g, _w in different), different
    kinds = {e["loc"].split("/")[1] for e in want if e["loc"] != "/"}
    assert {"daily", "weekly", "monthly", "story", "items"} <= kinds | {"daily"}, kinds
    assert len(got) > 20

    class Endpoint(BaseHTTPRequestHandler):
        seen: list = []

        def do_POST(self):
            Endpoint.seen.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("INDEXNOW_ENDPOINT", f"http://127.0.0.1:{server.server_port}/indexnow")
    monkeypatch.setenv("INDEXNOW_SUBMIT_ENABLED", "true")
    monkeypatch.setenv("INDEXNOW_KEY", "0123456789abcdef0123456789abcdef")
    with ConnectionPool(database_url, min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=lambda c: c.adapters.register_loader("numeric", _FloatNumeric), open=True) as pool:
        with pool.connection() as conn:
            before = conn.execute("SELECT value FROM settings WHERE key = 'indexnow.watermark'").fetchone()
            conn.execute("DELETE FROM settings WHERE key = 'indexnow.watermark'")
        first = submit_index_now(pool, now)
        second = submit_index_now(pool, now)  # everything acknowledged: nothing to send
        with pool.connection() as conn:
            saved = conn.execute("SELECT value FROM settings WHERE key = 'indexnow.watermark'").fetchone()["value"]
            conn.execute("DELETE FROM settings WHERE key = 'indexnow.watermark'")
            if before:
                conn.execute("INSERT INTO settings (key, value, updated_by) VALUES ('indexnow.watermark', %s, 'worker')", (json.dumps(before["value"]),))
    server.shutdown()
    assert first["status"] == "sent" and first["urls"] == len(got) and second == {"status": "empty", "httpStatus": None, "urls": 0, "more": False, "sample": []}
    assert Endpoint.seen[0]["host"] == "127.0.0.1:3000" and len(Endpoint.seen[0]["urlList"]) == len(got) and set(saved["fingerprints"]) == {e["loc"] for e in got}
