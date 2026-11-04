"""collectSource: the same sources, served by a local site, collected by the TypeScript engine and by the Python port, each into its own copy of one
database, must leave the same sources, runs, articles, revisions, discoveries and queued work - on the first import, on a quiet second run and after
the site changed."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

NOW = datetime.now(UTC).replace(microsecond=0)
STATE: dict = {"round": 0}


def rfc(d: datetime) -> str:
    return d.strftime("%a, %d %b %Y %H:%M:%S GMT")


def body(n: int) -> str:
    return f"<p>{f'Article number {n} goes on for quite a while so that it counts as a real body. ' * 6}</p>"


def feed_xml(round_: int) -> str:
    items = [
        ("Fresh one", "/p/fresh1", NOW - timedelta(hours=3)),
        ("Fresh two", "/p/fresh2", NOW - timedelta(hours=30)),
        ("Two months old", "/p/old60", NOW - timedelta(days=60)),
        ("Over a year old", "/p/old400", NOW - timedelta(days=400)),
        ("Sponsored: buy now", "/p/ad", NOW - timedelta(hours=5)),
        ("No date at all", "/p/nodate", None),
        ("Tracking link", "/p/fresh1?utm_source=feed", NOW - timedelta(hours=3)),
    ]
    if round_ >= 2:
        items.insert(0, ("Brand new", "/p/new3", NOW - timedelta(minutes=20)))
        items.append(("Archive added later", "/p/old90", NOW - timedelta(days=90)))
        items[2] = ("Fresh two, retitled", "/p/fresh2", NOW - timedelta(hours=30))
    rows = "".join(
        f"<item><title>{t}</title><link>https://{{origin}}{p}</link><guid>{p}</guid>{'<pubDate>' + rfc(d) + '</pubDate>' if d else ''}"
        f"<description>Summary of {t}</description><content:encoded><![CDATA[{body(i)}]]></content:encoded></item>"
        for i, (t, p, d) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>T</title>{rows}</channel></rss>'


def list_html(round_: int) -> str:
    cards = [("Web first", "/w/one", NOW - timedelta(hours=2)), ("Web second", "/w/two", NOW - timedelta(days=3)), ("Web archive", "/w/old", NOW - timedelta(days=500))]
    if round_ >= 2:
        cards.append(("Web newest", "/w/three", NOW - timedelta(minutes=10)))
    pad = "<p>" + "Filler paragraph so the page is long enough to be a real page. " * 6 + "</p>"
    rows = "".join(f'<div class="card"><a href="{p}"><h3>{t}</h3></a><time datetime="{d.isoformat()}">{d:%b %d}</time></div>' for t, p, d in cards)
    return f"<html><head><title>L</title></head><body><h1>List</h1>{pad}{rows}</body></html>"


def api_json(round_: int) -> str:
    rows = [
        {"id": 7, "t": "Api seven", "slug": "seven", "ts": int((NOW - timedelta(hours=4)).timestamp())},
        {"id": 8, "t": "Api eight", "slug": "eight", "ts": int((NOW - timedelta(days=2)).timestamp())},
    ]
    if round_ >= 2:
        rows[0] = {**rows[0], "slug": "seven-renamed"}  # the same entry under a new address: its identity is reused
        rows.append({"id": 9, "t": "Api nine", "slug": "nine", "ts": int((NOW - timedelta(minutes=5)).timestamp())})
    return json.dumps({"items": rows})


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            round_ = STATE["round"]
            origin = f"127.0.0.1:{self.server.server_port}"
            if path == "/feed.xml":
                content, ctype = feed_xml(round_).replace("{origin}", origin), "application/rss+xml"
            elif path == "/list.html":
                content, ctype = list_html(round_), "text/html"
            elif path == "/api.json":
                content, ctype = api_json(round_), "application/json"
            else:
                self.send_response(404)
                self.end_headers()
                return
            etag = f'"{abs(hash((path, round_)))}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(content.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def source_rows(origin: str) -> list[dict]:
    rss = {"feedUrl": f"{origin}/feed.xml", "ingestNoiseFilter": {"dropMarkersTitleOnly": ["sponsored"]}}
    return [
        {"id": "t-rss", "kind": "rss", "config": rss},
        {"id": "t-rss-fulltext", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml", "summaryIsBody": True, "allowUrlPrefixes": [f"{origin}/p/fresh"]}, "participation_mode": "hot_signal"},
        {"id": "t-web", "kind": "web_list", "config": {"url": f"{origin}/list.html", "itemSelector": ".card", "titleSelector": "h3", "publishedAtSelector": "time"}},
        {"id": "t-json", "kind": "json_list", "config": {"url": f"{origin}/api.json", "itemsPath": "items", "titlePaths": ["t"], "urlTemplate": f"{origin}/a/{{slug}}",
                                                          "externalIdPath": "id", "publishedAtPath": "ts", "publishedAtUnit": "epoch_s"}},
        {"id": "t-404", "kind": "rss", "config": {"feedUrl": f"{origin}/missing.xml"}},
        {"id": "t-paused", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml"}, "enabled": False},
        {"id": "t-bogus", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml", "bogusKey": 1}},
        {"id": "t-mp", "kind": "mp_account", "config": {}},
    ]  # fmt: skip


IDS = ["t-rss", "t-rss-fulltext", "t-web", "t-json", "t-404", "t-paused", "t-bogus", "t-mp", "nope"]


def install(url: str, origin: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM sources WHERE id LIKE 't-%'")
        for s in source_rows(origin):
            conn.execute(
                "INSERT INTO sources (id, name, kind, config, participation_mode, enabled, created_at) VALUES (%s, %s, %s, %s, %s, %s, now() - interval '30 days')",
                (s["id"], s["id"], s["kind"], Jsonb(s["config"]), s.get("participation_mode", "editorial"), s.get("enabled", True)),
            )


def _plain(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if hasattr(v, "as_tuple"):
        return float(v)
    return v


def dump(url: str) -> dict:
    with psycopg.connect(url, row_factory=dict_row) as c:
        sources = c.execute(
            """SELECT id, health, fail_count, last_error, cursor, last_fetch_at IS NOT NULL AS fetched, last_ok_at IS NOT NULL AS ok_at,
                      next_fetch_at IS NOT NULL AS scheduled FROM sources WHERE id LIKE 't-%' ORDER BY id"""
        ).fetchall()
        for s in sources:
            for k in ("lastOkAt", "initializedAt"):
                if s["cursor"] and k in s["cursor"]:
                    s["cursor"][k] = "<time>"
        runs = c.execute("SELECT source_id, status, found_count, new_count, error, detail FROM fetch_runs WHERE source_id LIKE 't-%' ORDER BY source_id, id").fetchall()
        articles = c.execute(
            """SELECT identity_key, source_id, title, url, published_at, publication_time, backfill, backfill_reason, excerpt, body_text, body_status, revision, raw,
                      processing_queued_at IS NOT NULL AS queued, processing_attempt_tag FROM articles WHERE source_id LIKE 't-%' ORDER BY identity_key"""
        ).fetchall()
        revisions = c.execute(
            """SELECT a.identity_key, r.revision, r.title, r.content_hash FROM article_revisions r JOIN articles a ON a.id = r.article_id
               WHERE a.source_id LIKE 't-%' ORDER BY a.identity_key, r.revision"""
        ).fetchall()
        discoveries = c.execute(
            """SELECT a.identity_key, d.source_id, d.via FROM article_discoveries d JOIN articles a ON a.id = d.article_id
               WHERE a.source_id LIKE 't-%' ORDER BY a.identity_key, d.source_id, d.via"""
        ).fetchall()
        ids = {r["id"]: r["identity_key"] for r in c.execute("SELECT id, identity_key FROM articles").fetchall()}
        try:
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM pgboss.job WHERE name IN ('content.analyze','content.extract-body','events.group')").fetchall()
        except psycopg.errors.UndefinedTable:
            c.rollback()
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM jobs WHERE name IN ('content.analyze','content.extract-body','events.group')").fetchall()
    queue = sorted(
        (j["name"], j["priority"], (j["singleton_key"] or "").replace(j["data"]["articleId"], ids.get(j["data"]["articleId"], "?")), ids.get(j["data"]["articleId"], "?"),
         json.dumps({k: v for k, v in j["data"].items() if k != "articleId"}, sort_keys=True))
        for j in jobs
    )  # fmt: skip
    return _plain({"sources": sources, "runs": runs, "articles": articles, "revisions": revisions, "discoveries": discoveries, "queue": queue})


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["col_node", "col_py"])
    admin.close()
    server = serve()
    origin = f"http://127.0.0.1:{server.server_port}"
    for u in urls.values():
        install(u, origin)
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["col_py"], autocommit=True) as conn:  # the Python queue's own tables
        conn.execute(migration.read_text(encoding="utf-8"))
    yield urls, origin
    server.shutdown()


def run_node(url: str) -> list[dict]:
    env = {**os.environ, **ENV, "DATABASE_URL": url, "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/collect-run.ts", *IDS], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


def run_python(url: str) -> list[dict]:
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.sources.collect import collect_source

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(url, min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        return [collect_source(pool, i) for i in IDS]


def same(world, label: str) -> tuple[dict, dict]:
    urls, _ = world
    node_results = run_node(urls["col_node"])
    py_results = run_python(urls["col_py"])
    for n, p in zip(node_results, py_results, strict=True):
        assert (p["sourceId"], p["status"], p["found"], p["created"], p["revised"]) == (n["sourceId"], n["status"], n["found"], n["created"], n["revised"]), (label, n, p)
        assert p.get("error", "").split(":")[0] == n.get("error", "").split(":")[0], (label, n, p)
    node, py = dump(urls["col_node"]), dump(urls["col_py"])
    for key in node:
        assert py[key] == node[key], f"{label}: {key} differ\n py   {json.dumps(py[key], ensure_ascii=False)[:1500]}\n node {json.dumps(node[key], ensure_ascii=False)[:1500]}"
    return node, py


def test_first_import(world):
    node, _ = same(world, "first import")
    assert any(a["backfill_reason"] == "first-import" for a in node["articles"]) and len(node["articles"]) >= 8
    assert any(r["status"] == "failed" for r in node["runs"]) and node["queue"]


def test_quiet_second_run(world):
    same(world, "second run")


def test_site_changed(world):
    STATE["round"] = 2
    node, _ = same(world, "after change")
    assert any(r["revision"] > 1 for r in node["revisions"]) or any(a["revision"] > 1 for a in node["articles"])
    assert any(a["title"] == "Api nine" for a in node["articles"])
    same(world, "after change, again")
