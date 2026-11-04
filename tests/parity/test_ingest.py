"""POST /api/ingest/items: the same pushes to the TypeScript server and the Python app, each on its own copy of one database, must be answered the same and leave the
same sources, articles, events and queued work."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from tests.parity.conftest import ENV
from tests.parity.test_admin import both, sides  # noqa: F401  (the fixture)
from tests.parity.test_admin_sources import admin  # noqa: F401  (creates the Python queue's tables)

TOKEN = ENV["INGEST_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NOW = datetime.now(UTC)


def push(admin_, body, headers=None, raw=None, ctype="application/json"):
    out = []
    for side in ("node", "py"):
        h = {**({"Content-Type": ctype} if ctype else {}), **(headers if headers is not None else AUTH)}
        out.append(admin_[side].send("POST", "/api/ingest/items", headers=h, content=raw if raw is not None else json.dumps(body)))
    return out


def same(admin_, body, status, headers=None, raw=None, ctype="application/json"):
    a, b = push(admin_, body, headers, raw, ctype)
    assert a.status_code == b.status_code == status, (body, a.status_code, a.text[:200], b.status_code, b.text[:200])
    assert a.headers.get("content-type", "").split(";")[0] == b.headers.get("content-type", "").split(";")[0]
    assert a.headers.get("cache-control") == b.headers.get("cache-control")
    ja, jb = (r.json() if "json" in r.headers.get("content-type", "") else r.text for r in (a, b))
    if isinstance(ja, dict):
        ja.pop("requestId", None), jb.pop("requestId", None)
    assert ja == jb, (body, ja, jb)
    return ja


def rows(url, query):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(query).fetchall()


def item(n, **kw):
    return {"title": f"Reported item {n}", "url": f"https://news.example.org/p/{n}", "publishedAt": (NOW - timedelta(hours=n)).isoformat(), **kw}


def test_refusals(admin):
    ok = {"sourceId": "ext-one", "items": [item(1)]}
    same(admin, ok, 401, headers={})
    same(admin, ok, 401, headers={"Authorization": "Bearer wrong-token-0123456789abcdef"})
    same(admin, ok, 401, headers={"Authorization": TOKEN})
    same(admin, {}, 400)
    same(admin, {"sourceId": "  ", "items": [item(1)]}, 400)
    same(admin, {"sourceId": "x", "items": []}, 400)
    same(admin, {"sourceId": "x", "items": "no"}, 400)
    same(admin, {"sourceId": "x", "items": [item(i) for i in range(51)]}, 413)
    same(admin, None, 400, raw="{broken json")
    same(admin, None, 400, raw="")
    same(admin, None, 415, raw="sourceId=x", ctype="application/x-www-form-urlencoded")
    same(admin, None, 401, raw="null", headers={})
    assert rows(admin["urls"]["py"], "SELECT count(*) AS n FROM sources WHERE id LIKE 'ext-%'") == rows(admin["urls"]["node"], "SELECT count(*) AS n FROM sources WHERE id LIKE 'ext-%'")


def test_pushes_and_their_rows(admin):
    body = {
        "sourceId": "ext-news", "sourceName": "External News", "items": [
            item(1), item(2, author="A" * 300), {**item(3), "url": "HTTPS://WWW.News.Example.org/p/3/?utm_source=x"}, {**item(1), "title": "Same url again"},
            {"title": "", "url": "https://news.example.org/p/blank"}, {"title": "No url"}, "not an object", None, {**item(4), "url": "not a url"},
            item(5, raw={"_aihot": {"backfill": True}, "k": 1}), item(6, raw={"_aihot": {"baseline": True}}), item(7, publishedAt="2020-01-01"),
            item(8, publishedAt=(NOW + timedelta(days=3)).isoformat()), item(9, publishedAt=None), item(10, publishedAt="garbage"), {**item(11), "raw": "text"},
        ],
    }  # fmt: skip
    first = same(admin, body, 200)
    assert first["created"] >= 8
    again = same(admin, body, 200)  # nothing new
    assert again["created"] == 0
    same(admin, {"sourceId": "ext-news", "items": [{**item(1), "title": "A changed title"}]}, 200)  # a revision
    same(admin, {"sourceId": "ext-fresh", "items": [item(20)]}, 200)  # an unknown source is created isolated
    for url in admin["urls"].values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("UPDATE sources SET enabled = false WHERE id = 'ext-fresh'")
    same(admin, {"sourceId": "ext-fresh", "items": [item(21)]}, 409)
    q = "SELECT id, name, kind, tier, participation_mode, interval_minutes, enabled, health, tags, last_ok_at IS NOT NULL AS ok_at, last_fetch_at IS NOT NULL AS fetched FROM sources WHERE id LIKE 'ext-%' ORDER BY id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = """SELECT identity_key, source_id, url, title, author, published_at, publication_time, backfill, backfill_reason, raw, revision, body_status FROM articles
           WHERE source_id LIKE 'ext-%' ORDER BY identity_key"""
    a = rows(admin["urls"]["node"], q)
    assert rows(admin["urls"]["py"], q) == a and len(a) >= 8
    q = "SELECT client, kind, status, summary FROM ingest_events ORDER BY id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT a.identity_key, d.source_id, d.via FROM article_discoveries d JOIN articles a ON a.id = d.article_id WHERE a.source_id LIKE 'ext-%' ORDER BY 1, 2, 3"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    node_jobs = rows(admin["urls"]["node"], "SELECT name, count(*)::int AS n FROM pgboss.job WHERE name IN ('content.analyze','content.extract-body','events.group') GROUP BY 1 ORDER BY 1")
    py_jobs = rows(admin["urls"]["py"], "SELECT name, count(*)::int AS n FROM jobs WHERE name IN ('content.analyze','content.extract-body','events.group') GROUP BY 1 ORDER BY 1")
    assert py_jobs == node_jobs
