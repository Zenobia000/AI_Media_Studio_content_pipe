"""Source icons: the candidates a home page declares, and the icons found and stored for sources on a local site, must be the same from the TypeScript engine and the Python port."""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from PIL import Image
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

SNIPPETS = [
    ('<link rel="icon" href="/favicon-32.png" sizes="32x32"><link rel="apple-touch-icon" href="/touch.png" sizes="180x180"><link rel="shortcut icon" href="/x.ico">', "https://a.example/"),
    ('<link rel="icon" type="image/svg+xml" href="/logo.svg"><link rel="icon" href="/a.png" sizes="16x16 512x512">', "https://a.example/dir/page"),
    ("<link rel=icon href=bare.png><LINK REL='ICON' HREF='upper.png'><link rel=\"stylesheet\" href=\"/s.css\"><link rel=\"icon\" href=\"data:image/png;base64,AAAA\">", "https://a.example/x/"),
    ('<link rel="icon" href="//cdn.example/i.png"><link rel="icon" href="http://other.example/j.png?v=2">', "https://a.example/"),
    ("<link rel='icon'>", "https://a.example/"),
    ("no links at all", "http://plain.example:8080/deep/page.html"),
    ('<link rel="icon" href="/a.png"><link rel="apple-touch-icon-precomposed" href="/t.png"><link rel="mask-icon" href="/m.svg">', "https://a.example/"),
]


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                body, ctype = b'<html><head><link rel="icon" href="/broken.png" sizes="512x512"><link rel="icon" href="/good.png" sizes="64x64"></head><body>home</body></html>', "text/html"
            elif path == "/good.png":
                body, ctype = png(), "image/png"
            elif path == "/bare/":
                body, ctype = b"<html><body>no declared icon</body></html>", "text/html"
            elif path == "/bare/favicon.ico":
                body, ctype = png(), "image/png"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_candidates(settings_env, tmp_path):
    from newsroom.engine.sources.icons import icon_candidates

    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"html": h, "base": b} for h, b in SNIPPETS]), encoding="utf-8")
    out = subprocess.run(["node", "scripts/icons-run.ts", str(path)], cwd=AIHOT, env={**os.environ, **ENV, "DATABASE_URL": "postgres://x"}, check=True, capture_output=True, timeout=120)
    node = json.loads(out.stdout.decode().strip().splitlines()[-1])
    for (html, base), want in zip(SNIPPETS, node, strict=True):
        assert icon_candidates(html, base) == want, (html, base)


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["ico_node", "ico_py"])
    admin.close()
    server = serve()
    origin = f"http://127.0.0.1:{server.server_port}"
    for url in urls.values():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            c.execute("UPDATE sources SET icon_url = NULL, icon_checked_at = NULL, enabled = false")
            c.execute(
                "INSERT INTO sources (id, name, kind, config) VALUES ('ic-site', 'Site', 'rss', %s), ('ic-bare', 'Bare', 'web_list', %s), ('ic-none', 'None', 'rss', %s), ('ic-x', 'X', 'x_search', %s)",
                (Jsonb({"feedUrl": f"{origin}/feed.xml"}), Jsonb({"url": f"{origin}/bare/"}), Jsonb({"feedUrl": "http://127.0.0.1:9/never.xml"}), Jsonb({"query": "from:SomeAccount -is:retweet"})),
            )
            c.execute("UPDATE sources SET icon_url = 'https://old.example/avatar.png' WHERE id = 'ic-x'")
            rows = c.execute("SELECT id FROM articles ORDER BY id LIMIT 3").fetchall()
            c.execute("UPDATE articles SET source_id = 'ic-site', url = %s WHERE id = %s", (f"{origin}/p/1", rows[0]["id"]))
            c.execute("UPDATE articles SET source_id = 'ic-x', x_post = %s WHERE id = %s", (Jsonb({"handle": "someaccount", "avatarUrl": "https://pbs.example/avatar_new.jpg"}), rows[1]["id"]))
            c.execute(
                "UPDATE articles SET source_id = 'ic-x', x_post = %s, discovered_at = now() - interval '10 days' WHERE id = %s",
                (Jsonb({"handle": "someaccount", "avatarUrl": "https://pbs.example/older.jpg"}), rows[2]["id"]),
            )
    yield urls
    server.shutdown()


def test_icons_found_and_stored(world):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.sources.icons import refresh_source_icons

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    env = {**os.environ, **ENV, "DATABASE_URL": world["ico_node"], "AIHOT_DATA_DIR": os.path.join(os.environ.get("TEMP", "/tmp"), "ico-node-data")}
    out = subprocess.run(["node", "scripts/icons-run.ts"], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("{")))
    with ConnectionPool(world["ico_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        py = refresh_source_icons(pool)
    assert py == node and node["found"] >= 2 and node["xAvatars"] == 1
    q = "SELECT id, icon_url IS NOT NULL AS has_icon, regexp_replace(coalesce(icon_url, ''), '127.0.0.1:[0-9]+', 'LOCAL') AS icon, icon_checked_at IS NOT NULL AS checked FROM sources WHERE id LIKE 'ic-%' ORDER BY id"
    with psycopg.connect(world["ico_node"], row_factory=dict_row) as a, psycopg.connect(world["ico_py"], row_factory=dict_row) as b:
        assert b.execute(q).fetchall() == a.execute(q).fetchall()
