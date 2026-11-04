"""/api/admin/sources: the same requests to the TypeScript server and the Python app, each on its own copy of one database, must answer the same and leave
the same rows - the lists, detail, previews of the three kinds of listings, creation with its duplicate checks, edits with their conflicts and
validation, pause and resume, and the manual collection."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.parity.test_admin import PASSWORD, both, cookie_of, same, sides  # noqa: F401  (the fixture)

FIXTURES = json.loads((Path(__file__).parents[1] / "engine" / "admin_validation_fixtures.json").read_text(encoding="utf-8"))
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>First preview item</title><link>https://example.org/p/1</link><pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate><description>About the first</description></item>
<item><title>Second preview item</title><link>https://example.org/p/2</link></item></channel></rss>"""
LIST = "<html><head><title>L</title></head><body><h1>List</h1>" + "<p>filler to be long enough as a page. </p>" * 8 + '<div class="c"><a href="/w/1"><h3>Web preview one</h3></a></div></body></html>'
API = json.dumps({"items": [{"t": "Json preview one", "slug": "one", "ts": 1760000000}]})


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hit = {"/feed.xml": ("application/rss+xml", RSS), "/list.html": ("text/html", LIST), "/api.json": ("application/json", API)}.get(self.path.split("?")[0])
            self.send_response(200 if hit else 404)
            if hit:
                self.send_header("Content-Type", hit[0])
            self.end_headers()
            if hit:
                self.wfile.write(hit[1].encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def admin(sides):  # noqa: F811
    from newsroom.api.routes import admin_auth

    admin_auth._attempts.clear()  # the sign-in limit counts every module's logins in this one process
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(sides["urls"]["py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    for name, res in zip(("node", "py"), both(sides, "POST", "/api/auth/password", form={"password": PASSWORD, "return": "/admin"}), strict=True):
        assert res.status_code == 303 and "set-cookie" in res.headers, (name, res.status_code, res.headers.get("location"), res.text[:200])
        sides[name].cookie = cookie_of(res).split(";")[0]
    a, b = both(sides, "GET", "/api/admin/me")
    sides["node"].csrf, sides["py"].csrf = a.json()["csrf"], b.json()["csrf"]
    server = serve()
    sides["origin"] = f"http://127.0.0.1:{server.server_port}"
    yield sides
    server.shutdown()


def versions(admin_, source_id: str):
    """The version of a source in each side's own database."""
    return lambda side: {"node": a, "py": b}[side] if False else None


def version_of(admin_, side: str, source_id: str) -> str:
    return admin_[side].request("GET", f"/api/admin/sources/{source_id}").json()["source"]["updated_at"]


def test_validation_messages(admin):
    for body, expected in zip(FIXTURES["createInputs"], FIXTURES["creates"], strict=True):
        a, b = both(admin, "POST", "/api/admin/sources", json_body=body)
        if expected == "passed":
            continue
        assert (a.status_code, b.status_code) == (400, 400), (body, a.text[:200], b.text[:200])
        assert b.json()["detail"] == a.json()["detail"], body
        if isinstance(body, dict) and body:  # a body that is no object reaches the TypeScript function as {} over HTTP
            assert b.json()["detail"] == expected, body
    for body, expected in zip(FIXTURES["patchInputs"], FIXTURES["patches"], strict=True):
        a, b = both(admin, "PATCH", "/api/admin/sources/no-such-source", json_body={"patch": body, "version": "v"})
        assert a.status_code == b.status_code and a.json().get("detail") == b.json().get("detail"), (body, a.text[:200], b.text[:200])


def test_list_and_detail(admin):
    same(admin, "GET", "/api/admin/sources", 200)
    for query in ("?kind=rss", "?q=ithome", "?health=ok", "?enabled=false", "?mode=hot_signal", "?page=2", "?q=%25", "?use=x"):
        same(admin, "GET", "/api/admin/sources" + query, 200)
    ids = [r["id"] for r in admin["node"].request("GET", "/api/admin/sources").json()["rows"]]
    assert ids
    for source_id in ids[:3]:
        same(admin, "GET", f"/api/admin/sources/{source_id}", 200)
    same(admin, "GET", "/api/admin/sources/missing", 404)


def test_previews(admin):
    origin = admin["origin"]
    drafts = [
        {"id": "p-rss", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml"}},
        {"id": "p-web", "kind": "web_list", "config": {"url": f"{origin}/list.html", "itemSelector": ".c", "titleSelector": "h3"}},
        {"id": "p-json", "kind": "json_list", "config": {"url": f"{origin}/api.json", "itemsPath": "items", "titlePaths": ["t"], "urlTemplate": "https://e.org/{slug}", "publishedAtPath": "ts", "publishedAtUnit": "epoch_s"}},
        {"id": "p-bad", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml", "bogus": 1}},
        {"id": "p-404", "kind": "rss", "config": {"feedUrl": f"{origin}/missing.xml"}},
        {"id": "p-window", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml", "publishedAfter": "2026-11-01T00:00:00Z"}},
        {"id": "p-mp", "kind": "mp_account", "config": {}},
    ]
    for draft in drafts:
        a, b = both(admin, "POST", "/api/admin/sources/preview", json_body=draft)
        assert a.status_code == b.status_code, (draft["id"], a.text[:200], b.text[:200])
        ja, jb = a.json(), b.json()
        ja.pop("ms", None), jb.pop("ms", None), ja.pop("requestId", None), jb.pop("requestId", None)
        assert ja == jb, draft["id"]
    ids = [r["id"] for r in admin["node"].request("GET", "/api/admin/sources").json()["rows"]]
    for source_id in ids[:2]:
        a, b = both(admin, "POST", f"/api/admin/sources/{source_id}/preview")
        assert a.status_code == b.status_code


def rows(url: str, query: str) -> list[dict]:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(query).fetchall()


def test_create_edit_pause_fetch(admin):
    origin = admin["origin"]
    new = {"id": "t-new-source", "name": "New source", "kind": "rss", "config": {"feedUrl": f"{origin}/feed.xml"}, "tags": ["a"], "tier": "T1"}
    a, b = both(admin, "POST", "/api/admin/sources", json_body=new)
    assert a.status_code == b.status_code == 200
    assert a.json()["created"] is b.json()["created"] is True
    ra, rb = a.json()["source"], b.json()["source"]
    assert {k: v for k, v in ra.items() if k not in ("created_at", "updated_at", "next_fetch_at")} == {k: v for k, v in rb.items() if k not in ("created_at", "updated_at", "next_fetch_at")}
    # a duplicate address, a duplicate id, an unsupported key
    same(admin, "POST", "/api/admin/sources", 200, json_body={**new, "id": "t-dup-address"})
    same(admin, "POST", "/api/admin/sources", 409, json_body={**new, "id": "t-new-source", "config": {"feedUrl": f"{origin}/other.xml"}})
    same(admin, "POST", "/api/admin/sources", 400, json_body={**new, "id": "t-other", "config": {"feedUrl": f"{origin}/o.xml", "bogus": 1}})

    def patch(body, status):
        a, b = both(admin, "PATCH", "/api/admin/sources/t-new-source", json_body=None) if False else (None, None)
        res = []
        for side in ("node", "py"):
            version = version_of(admin, side, "t-new-source")
            payload = {"patch": body, "version": version, "reason": "test"}
            res.append(admin[side].request("PATCH", "/api/admin/sources/t-new-source", json_body=payload))
        assert res[0].status_code == res[1].status_code == status, (body, res[0].text[:300], res[1].text[:300])
        ja, jb = res[0].json(), res[1].json()
        for j in (ja, jb):
            for k in ("created_at", "updated_at", "next_fetch_at", "requestId"):
                j.pop(k, None)
        assert ja == jb, body

    patch({"name": "Renamed"}, 200)
    patch({"enabled": False}, 200)
    patch({"enabled": True, "interval_minutes": 15}, 200)
    patch({"tier": "T2", "tags": ["x", "y"]}, 200)
    patch({"participation_mode": "hot_signal"}, 200)
    patch({"participation_mode": "editorial"}, 200)
    patch({"config": {"feedUrl": f"{origin}/feed.xml", "summaryIsBody": True}}, 200)
    patch({"config": {"feedUrl": f"{origin}/feed.xml", "bogus": 1}}, 400)
    patch({"kind": "web_list"}, 400)
    patch({"kind": "web_list", "config": {"url": "not a url"}}, 400)
    patch({"kind": "web_list", "config": {"url": f"{origin}/list.html", "itemSelector": ".c"}}, 200)
    patch({"kind": "json_list", "config": {"url": f"{origin}/api.json"}}, 400)
    patch({"config": {"feedUrl": f"{origin}/api.json"}}, 400)  # a feed key on a web list
    patch({}, 200)
    # a stale version, an unknown source
    stale = both(admin, "PATCH", "/api/admin/sources/t-new-source", json_body={"patch": {"name": "x"}, "version": "2020-01-01T00:00:00.000Z"})
    assert stale[0].status_code == stale[1].status_code == 409 and stale[0].json()["detail"] == stale[1].json()["detail"]
    same(admin, "PATCH", "/api/admin/sources/nope-nope", 404, json_body={"patch": {"name": "x"}, "version": "v"})
    # duplicate address on edit
    both(admin, "POST", "/api/admin/sources", json_body={"id": "t-second", "name": "S", "kind": "web_list", "config": {"url": f"{origin}/api.json"}})
    res = [admin[s].request("PATCH", "/api/admin/sources/t-new-source", json_body={"patch": {"config": {"url": f"{origin}/api.json"}}, "version": version_of(admin, s, "t-new-source")}) for s in ("node", "py")]
    assert res[0].status_code == res[1].status_code == 409 and res[0].json()["detail"] == res[1].json()["detail"]
    # a manual collection
    for side in ("node", "py"):
        out = admin[side].request("POST", "/api/admin/sources/t-new-source/fetch")
        assert out.status_code == 200 and out.json()["jobId"]
    same(admin, "POST", "/api/admin/sources/nope-nope/fetch", 404)

    # the rows both sides left behind
    q = "SELECT id, name, kind, config, enabled, health, interval_minutes, tier, first_party, participation_mode, tags, uses, cursor FROM sources WHERE id LIKE 't-%' ORDER BY id"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
    q = "SELECT actor, action, subject, reason, before, after FROM audit_log WHERE subject LIKE 'source:t-%' ORDER BY id"
    masked = [[{**r, "after": {**r["after"], "jobId": "<id>"} if r["action"] == "source.fetch" else r["after"]} for r in rows(admin["urls"][s], q)] for s in ("py", "node")]
    assert masked[0] == masked[1]
    q = "SELECT key, value->>'status' AS status FROM settings WHERE key LIKE 'republish.source:t-%' ORDER BY key"
    assert rows(admin["urls"]["py"], q) == rows(admin["urls"]["node"], q)
