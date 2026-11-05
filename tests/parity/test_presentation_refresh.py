"""Refreshing how a body is shown (the admin's "presentation" rerun): the same previews and applications by the TypeScript engine and by the Python port, each on its own copy of one
database, against a local page whose layout changed, must answer the same, and move a model translation onto the new layout only when its wording still fits."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin


def page(link: str, extra: str = "") -> str:
    para = "The body of this article says a number of things in some detail, which is enough text for the reader to find it. "
    return (
        f"<html><head><title>An article</title></head><body><article><h1>An article about models</h1><p>{para * 3}See <a href=\"{link}\">the report</a> for more.</p>"
        f"<p>{para * 3}</p><p>{para * 3}{extra}</p></article></body></html>"
    )


class Site:
    def __init__(self):
        self.html = {"/p": page("https://new.example/report")}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = outer.html.get(self.path)
                self.send_response(200 if body else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write((body or "no").encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["pr2_node", "pr2_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["pr2_py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    return urls


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {
            "articles": c.execute("SELECT identity_key, body_html, media, revision FROM articles WHERE body_html IS NOT NULL ORDER BY identity_key").fetchall(),
            "translations": c.execute("SELECT a.identity_key, t.body_html, t.origin FROM translations t JOIN articles a ON a.id = t.article_id ORDER BY a.identity_key").fetchall(),
            "audit": c.execute("SELECT actor, action, subject, reason, before, after, request_id FROM audit_log WHERE action = 'content.rerun.presentation' ORDER BY id").fetchall(),
        }


def test_previews_and_applications(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.admin.content import rerun
    from newsroom.engine.extract import extract_from_url

    site = Site()
    ids: dict[str, str] = {}
    old_html = ""
    for name, url in world.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            row = c.execute("SELECT a.id FROM articles a JOIN sources s ON s.id = a.source_id WHERE s.kind = 'rss' ORDER BY a.identity_key LIMIT 1").fetchone()
            ids[name] = row["id"]
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_FETCH", "true")
    got = extract_from_url(f"{site.url}/p")
    assert got, "the local page reads as an article"
    old_html = got.html.replace("https://new.example/report", "https://old.example/report")
    zh = re.sub(r">([^<>]+)<", ">中文<", old_html)  # the same blocks, links and images, in other words
    for name, url in world.items():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("UPDATE articles SET url = %s, body_html = %s, body_text = %s, media = '[]'::jsonb WHERE id = %s", (f"{site.url}/p", old_html, got.text, ids[name]))
            c.execute("DELETE FROM translations WHERE article_id = %s", (ids[name],))
            c.execute("INSERT INTO translations (article_id, lang, revision, title, body_html, body_text, complete, origin) SELECT id, 'zh', revision, 't', %s, 'x', true, 'model' FROM articles WHERE id = %s", (zh, ids[name]))

    def node(calls):
        path = tmp_path / "in.json"
        path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
        env = {**os.environ, **ENV, "DATABASE_URL": world["pr2_node"], "TZ": "UTC"}
        out = subprocess.run(["node", "scripts/paid-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
        return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    def calls(version):
        return [
            ["refreshPresentation", ids["pr2_node"], "request-0001", {"preview": True}],
            ["refreshPresentation", ids["pr2_node"], "request-0002", {}],  # no preview and no version
            ["refreshPresentation", ids["pr2_node"], "request-0003", {"version": "0" * 64}],  # a version that is not the page's now
            ["refreshPresentation", ids["pr2_node"], "request-0004", {"version": version}],
            ["refreshPresentation", ids["pr2_node"], "request-0004", {"version": version}],  # the same request again
            ["refreshPresentation", ids["pr2_node"], "request-0005", {"preview": True}],  # nothing left to change
            ["refreshPresentation", "no-such-article", "request-0006", {"preview": True}],
        ]

    preview = node(calls("x" * 64)[:1])[0]
    assert preview["ready"] and preview["changed"] and preview["translation"] == "reused", (json.dumps({k: preview[k] for k in ("ready", "changed", "reason", "translation")}, ensure_ascii=False))
    batch = calls(preview["version"])
    want = node(batch)
    py_calls = [[a if a != ids["pr2_node"] else ids["pr2_py"], *rest] for _n, a, *rest in batch]
    got_ = []
    with ConnectionPool(world["pr2_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=lambda c: c.adapters.register_loader("numeric", _FloatNumeric), open=True) as pool:
        for article, request, options in py_calls:
            try:
                got_.append(rerun(pool, article, "presentation", request, "tester", options))
            except Exception as error:
                got_.append({"error": str(error)})
    for call, w, g in zip(batch, want, got_, strict=True):
        if isinstance(w, dict) and set(w) == {"error", "name"}:
            w = {"error": w["error"]}
        assert g == w, (call[2], w, g)
    a, b = dump(world["pr2_node"]), dump(world["pr2_py"])
    for part in a:
        assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:2500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:2500]}"
    assert any(isinstance(r, dict) and r.get("applied") for r in got_) and "new.example" in "".join(t["body_html"] or "" for t in b["translations"])
