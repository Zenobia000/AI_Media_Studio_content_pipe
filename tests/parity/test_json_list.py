"""fetchJsonList: JSON served by a local server, read by the TypeScript collector and by the Python port, must give the same candidates."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.parity.conftest import AIHOT, ENV
from tests.parity.test_rss import _diff, _plain, _snake

API = {
    "data": {"items": [
        {"id": 101, "headline": " First <b>post</b> ", "slug": "first post/α", "ts": 1760000000, "summary": "A <i>short</i>   summary", "by": {"name": "Ada"}, "ok": True, "score": 5},
        {"id": 102, "headline": "Second", "slug": "second", "ts": "1760086400", "summary": "", "by": {"name": ""}, "ok": True, "score": "7"},
        {"id": 12345678901234567890, "headline": "Big id", "slug": "big", "ts": None, "summary": None, "ok": False, "score": 1},
        {"headline": "No slug", "ts": "x"},
        {"id": 5, "headline": 2026, "slug": "num", "date": "20260926", "ok": True, "score": 0},
        {"id": 6, "headline": "Dated", "slug": "dated", "date": "Sep 26, 2026", "alt": "fallback/ü", "ok": True, "score": 9},
    ]}
}  # fmt: skip
NEXT = {"props": {"pageProps": {"posts": [{"t": "Next one", "u": "n1", "d": "2026-09-26T10:00:00Z"}, {"t": "Next two", "u": "n2", "d": "2026-09-25"}]}}}
FLIGHT_BODY = '3:["$","x",null,{"entries":[{"t":"Flight one","u":"f1","tags":[1,2]},{"t":"Flight } two","u":"f2"}],"more":1}]\n'
FLIGHT = "self.__next_f.push([1," + json.dumps(FLIGHT_BODY) + "])"
PAGES = {
    "/api.json": ("application/json", json.dumps(API)),
    "/obj.json": ("application/json", json.dumps({"map": {"a": {"n": "A one", "u": "a"}, "b": {"n": "B one", "u": "b"}}})),
    "/empty.json": ("application/json", json.dumps({"data": {"items": []}})),
    "/notarray.json": ("application/json", json.dumps({"data": {"items": "x"}})),
    "/text.html": ("text/html", "<html>plain</html>"),
    "/next.html": ("text/html", '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(NEXT) + "</script></html>"),
    "/flight.html": ("text/html", "<html><script>" + FLIGHT + "</script></html>"),
    "/window.html": ("text/html", '<html><script>window.__STATE__ = {"list":[{"t":"Win one","u":"w1","s":"has } and \\" quote"}], "note":"x"};</script></html>'),
    "/window-open.html": ("text/html", '<html><script>window.__STATE__ = {"list":[</script></html>'),
}


def serve():
    class Handler(BaseHTTPRequestHandler):
        def respond(self, hit):
            if not hit:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", hit[0])
            self.end_headers()
            self.wfile.write(hit[1].encode())

        def do_GET(self):
            self.respond(PAGES.get(self.path.split("?")[0]))

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
            echo = {"seen": json.loads(body), "ctype": self.headers.get("content-type"), "items": [{"t": "Posted " + body, "u": "p1"}]}
            self.respond(("application/json", json.dumps(echo)))

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def origin(settings_env):
    server = serve()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def source_of(config: dict) -> dict:
    return {"id": "t-json", "name": "T", "kind": "json_list", "config": config, "tier": "T2", "participation_mode": "editorial", "uses": [], "first_party": False,
            "interval_minutes": 30, "enabled": True, "cursor": None, "fail_count": 0}  # fmt: skip


def run_both(tmp_path, config: dict):
    from newsroom.engine.sources.json_list import fetch_json_list

    path = tmp_path / "source.json"
    source = source_of(config)
    path.write_text(json.dumps({"source": source}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x", "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/json-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=120)
    from newsroom.engine.sources.json_list import json_parse

    node = json_parse(out.stdout.decode().strip().splitlines()[-1])
    try:
        py = {"ok": True, "candidates": fetch_json_list(source)}
    except Exception as e:
        py = {"ok": False, "error": str(e)[:200], "name": type(e).__name__}
    return _snake(node), _plain(py)


BASE = {"itemsPath": "data.items", "titlePaths": ["headline"], "urlTemplate": "https://example.org/p/{slug}", "externalIdPath": "id"}
VARIANTS = [
    BASE,
    {**BASE, "summaryPaths": ["summary"], "authorPaths": ["by.name"], "publishedAtPath": "ts", "publishedAtUnit": "epoch_s"},
    {**BASE, "summaryPaths": ["summary"], "summaryIsBody": True, "publishedAtPath": "ts", "publishedAtUnit": "epoch_ms"},
    {**BASE, "publishedAtPath": "date", "publishedAtUnit": "yyyymmdd", "publishedAtUtcOffset": "+00:00"},
    {**BASE, "publishedAtPath": "date", "urlTemplate": "https://example.org/{raw:slug}", "urlTemplateFallback": "https://example.org/alt/{alt}"},
    {**BASE, "requireBoolean": {"path": "ok", "equals": True}, "minNumeric": {"path": "score", "min": 5}},
    {**BASE, "titlePaths": ["missing", "headline"], "urlTemplate": "https://example.org/{slug}", "urlTemplateFallback": "https://example.org/alt/{alt}"},
]


@pytest.mark.parametrize("n", range(len(VARIANTS)))
def test_api_variants(origin, tmp_path, n):
    node, py = run_both(tmp_path, {"url": f"{origin}/api.json", **VARIANTS[n]})
    assert node["ok"] and py["ok"], (node, py)
    assert py["candidates"] == node["candidates"], f"variant {n}: {_diff(node['candidates'], py['candidates'])}"
    assert node["candidates"]


PAGE_CASES = {
    "object values": {"url": "/obj.json", "itemsPath": "map", "itemsObjectValues": True, "titlePaths": ["n"], "urlTemplate": "https://example.org/{u}"},
    "next data": {"url": "/next.html", "mode": "html_json_key", "jsonKey": "posts", "titlePaths": ["t"], "urlTemplate": "https://example.org/{u}", "publishedAtPath": "d", "itemsPath": "posts"},
    "flight": {"url": "/flight.html", "mode": "html_json_key", "jsonKey": "entries", "titlePaths": ["t"], "urlTemplate": "https://example.org/{u}", "itemsPath": "entries"},
    "window var": {"url": "/window.html", "mode": "html_window_var", "windowVar": "__STATE__", "itemsPath": "list", "titlePaths": ["t"], "urlTemplate": "https://example.org/{u}", "summaryPaths": ["s"]},
    "post body": {"url": "/post", "method": "POST", "bodyJson": {"q": "é", "n": [1, 2]}, "itemsPath": "items", "titlePaths": ["t"], "urlTemplate": "https://example.org/{u}"},
}  # fmt: skip


@pytest.mark.parametrize("name", list(PAGE_CASES))
def test_pages(origin, tmp_path, name):
    config = {**PAGE_CASES[name], "url": origin + PAGE_CASES[name]["url"]}
    node, py = run_both(tmp_path, config)
    assert node["ok"] and py["ok"], (node, py)
    assert py["candidates"] == node["candidates"], _diff(node["candidates"], py["candidates"])
    assert node["candidates"]


FAILURES = {
    "empty list is fine": ({"url": "/empty.json", "itemsPath": "data.items", "titlePaths": ["t"], "urlTemplate": "https://e/{u}"}, True),
    "not an array": ({"url": "/notarray.json", "itemsPath": "data.items", "titlePaths": ["t"], "urlTemplate": "https://e/{u}"}, False),
    "not json": ({"url": "/text.html", "titlePaths": ["t"], "urlTemplate": "https://e/{u}"}, False),
    "missing page": ({"url": "/gone.json", "titlePaths": ["t"], "urlTemplate": "https://e/{u}"}, False),
    "nothing mapped": ({"url": "/api.json", "itemsPath": "data.items", "titlePaths": ["nope"], "urlTemplate": "https://e/{slug}"}, False),
    "window var absent": ({"url": "/next.html", "mode": "html_window_var", "windowVar": "__MISSING__", "itemsPath": "x"}, False),
    "window var open": ({"url": "/window-open.html", "mode": "html_window_var", "windowVar": "__STATE__", "itemsPath": "list"}, False),
    "key absent": ({"url": "/next.html", "mode": "html_json_key", "jsonKey": "nothing", "itemsPath": "nothing"}, False),
}  # fmt: skip


@pytest.mark.parametrize("name", list(FAILURES))
def test_failures(origin, tmp_path, name):
    config, ok = FAILURES[name]
    node, py = run_both(tmp_path, {**config, "url": origin + config["url"]})
    assert node["ok"] is py["ok"] is ok, (node, py)
    if not ok:
        assert py["error"].split(":")[0] == node["error"].split(":")[0]
    else:
        assert py["candidates"] == node["candidates"]
