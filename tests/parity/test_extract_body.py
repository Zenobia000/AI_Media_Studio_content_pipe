"""extractArticleBody: articles whose pages a local site serves, read by the TypeScript engine and by the Python port, each in its own copy of one
database, must end up with the same bodies, media, dates and revisions."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.engine.reader_pages import PAGES, body, page
from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

DATED = page(
    "Dated story",
    f'<div><img src="/img/lead.jpg" width="640" height="360">{body(6)}</div>',
    head='<meta property="article:published_time" content="2026-11-03T08:30:00+08:00">',
)
SITE = {
    "news": PAGES["news"], "blog": PAGES["blog_lazy_images"], "jsonld": PAGES["jsonld_meta"], "table": PAGES["layout_table"], "dated": DATED, "chinese": PAGES["chinese"],
    "dated-day": page("Day", f"<div>{body(6)}</div>", head='<meta itemprop="datePublished" content="2026-10-20">'),
    "video": page("Video", f"<div>{body(6)}</div>"),
}  # fmt: skip


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            key = self.path.split("?")[0].removeprefix("/a/")
            if key not in SITE:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SITE[key].encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["ext_node", "ext_py"])
    admin.close()
    server = serve()
    origin = f"http://127.0.0.1:{server.server_port}"
    ids = {}
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            rows = c.execute("SELECT id FROM articles ORDER BY identity_key LIMIT %s", (len(SITE) + 1,)).fetchall()
            ids[name] = [r["id"] for r in rows]
            for r, key in zip(rows, [*SITE, "missing"], strict=False):
                c.execute(
                    "UPDATE articles SET url = %s, body_status = 'pending', body_text = NULL, body_html = NULL, media = '[]'::jsonb, published_at = NULL, "
                    "publication_time = NULL, published_at_claim = NULL WHERE id = %s",
                    (f"{origin}/a/{key}", r["id"]),
                )
            # one article already has its body: it is skipped; another has the content hash of its future body
            c.execute("UPDATE articles SET body_status = 'ok' WHERE id = %s", (rows[-1]["id"],))
    yield urls, ids
    server.shutdown()


def _plain(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def dump(url: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        arts = c.execute(
            """SELECT a.url, a.title, a.body_html, a.body_text, a.body_status, a.media, a.revision, a.published_at, a.publication_time, a.backfill, a.backfill_reason,
                      a.content_hash, a.processing_state, (SELECT count(*) FROM article_revisions r WHERE r.article_id = a.id) AS revisions
               FROM articles a ORDER BY a.identity_key"""
        ).fetchall()
    return [{**_plain(a), "url": a["url"].split("/a/")[-1]} for a in arts]


def test_bodies_dates_and_revisions(world):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.extract import extract_article_body

    urls, ids = world
    env = {**os.environ, **ENV, "DATABASE_URL": urls["ext_node"], "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/extract-body-run.ts", *ids["ext_node"]], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    node_outcomes = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(urls["ext_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        py_outcomes = []
        for i in ids["ext_py"]:
            try:
                py_outcomes.append(extract_article_body(pool, i))
            except Exception as e:
                py_outcomes.append(f"error: {e}")
    # a page that does not read as an article makes the TypeScript engine ask the paid renderer, which is not configured here
    for n, p in zip(node_outcomes, py_outcomes, strict=True):
        assert (n.startswith("error"), p) == (False, p) or (n.startswith("error") and p == "unconfirmed"), (node_outcomes, py_outcomes)
        if not n.startswith("error"):
            assert n == p, (node_outcomes, py_outcomes)
    node, py = dump(urls["ext_node"]), dump(urls["ext_py"])
    for a, b in zip(py, node, strict=True):
        if b["body_status"] != "unconfirmed" and b["body_status"] != "pending":
            assert a == b, f"{b['url']} differs:\n py   {json.dumps(a, ensure_ascii=False)[:1200]}\n node {json.dumps(b, ensure_ascii=False)[:1200]}"
    assert sum(1 for o in node_outcomes if o == "ok") >= 6 and "skipped" in node_outcomes
