"""fetchRss: feeds served by a local server, read by the TypeScript collector and by the Python port, must give the same candidates."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.parity.conftest import AIHOT, ENV

LONG = "<p>" + "A long article paragraph that goes on and on. " * 12 + "</p>"
FEEDS = {
    "/rss2.xml": (
        "application/rss+xml",
        f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>Feed</title>
<item><title>First &amp; <b>bold</b> post</title><link>https://example.org/posts/1</link><guid isPermaLink="false">00123</guid>
<pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate><dc:creator>Ada</dc:creator><category>AI</category><category>News</category>
<description><![CDATA[<p>Short summary with <a href="/x">link</a></p>]]></description>
<content:encoded><![CDATA[{LONG}<img src="/img/a.png"><img src="https://cdn.example.org/b.jpg" width="900">]]></content:encoded>
<enclosure url="https://cdn.example.org/cover.jpg" type="image/jpeg" length="1"/></item>
<item><title>Relative link</title><link>/posts/2</link><guid>https://example.org/posts/2</guid><pubDate>星期二, 03 Nov 2026 09:30:00 +0800</pubDate>
<description>Only a description &nbsp; &copy; 2026</description></item>
<item><title>Teaser entry</title><link>https://example.org/posts/3</link><content:encoded><![CDATA[<p>Short teaser text. Read more</p>]]></content:encoded><pubDate>2026-11-03</pubDate></item>
<item><title>Podcast episode</title><guid>episode-7</guid><enclosure url="https://cdn.example.org/ep7.mp3" type="audio/mpeg" length="5"/><pubDate>Tue, 03 Nov 2026 07:00:00 GMT</pubDate></item>
<item><title></title><link>https://example.org/posts/untitled</link></item>
<item><title>No link at all</title></item>
<item><title>Number title 2026</title><link>https://example.org/posts/4</link><guid>12345678901234567890</guid></item>
<item><title>Video</title><link>https://www.youtube.com/watch?v=abc123DEF45</link><media:group><media:description>Video description text</media:description></media:group></item>
</channel></rss>""",
    ),
    "/atom.xml": (
        "application/atom+xml",
        f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://blog.example.org/"><title>Atom</title>
<entry><title type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">An <em>xhtml</em> title</div></title><link rel="self" href="/self"/><link rel="alternate" href="posts/a"/>
<id>tag:blog,2026:a</id><published>2026-11-03T10:00:00Z</published><updated>2026-11-04T01:00:00+08:00</updated><author><name>Bea</name></author>
<category term="ai"/><category term="tools"/><summary>Plain &lt;b&gt;summary&lt;/b&gt;</summary><content type="html">{LONG.replace("<", "&lt;").replace(">", "&gt;")}</content></entry>
<entry xml:base="https://other.example.org/dir/"><title>Second</title><link href="b"/><id>b</id><published>2026-11-02T00:00:00+08:00</published><content type="text">Plain text content that is short.</content></entry>
<entry><title>No link</title><id>c</id></entry></feed>""",
    ),
    "/rdf.xml": (
        "application/rdf+xml",
        """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>RDF</title></channel>
<item><title>RDF item</title><link>https://example.org/rdf/1</link><dc:date>2026-11-01T12:00:00+00:00</dc:date><description>RDF description</description></item></rdf:RDF>""",
    ),
    "/html.html": ("text/html", "<html><body>not a feed</body></html>"),
    "/bad.xml": ("application/xml", "<rss><channel><item><title>unclosed"),
}


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hit = FEEDS.get(self.path.split("?")[0])
            if self.path.startswith("/cond"):
                if self.headers.get("If-None-Match") == '"v1"':
                    self.send_response(304)
                    self.send_header("ETag", '"v1"')
                    self.end_headers()
                    return
                hit = ("application/rss+xml", FEEDS["/rdf.xml"][1])
                self.send_response(200)
                self.send_header("Content-Type", hit[0])
                self.send_header("ETag", '"v1"')
                self.send_header("Last-Modified", "Mon, 02 Nov 2026 08:00:00 GMT")
                self.end_headers()
                self.wfile.write(hit[1].encode())
                return
            if not hit:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", hit[0])
            self.end_headers()
            self.wfile.write(hit[1].encode())

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


def _plain(read: dict) -> dict:
    def fix(v):
        if isinstance(v, datetime):
            return v.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v

    return fix(read)


SNAKE = {"publishedAt": "published_at", "publicationTime": "publication_time", "sourceUpdatedAt": "source_updated_at", "bodyHtml": "body_html", "bodyText": "body_text",
         "bodyStatus": "body_status", "identityKey": "identity_key"}  # fmt: skip


def _snake(v):
    if isinstance(v, dict):
        return {SNAKE.get(k, k): _snake(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_snake(x) for x in v]
    return v


def _diff(node: list, py: list) -> str:
    if len(node) != len(py):
        return f"{len(node)} candidates from TypeScript, {len(py)} from Python"
    for i, (a, b) in enumerate(zip(node, py, strict=True)):
        if a != b:
            keys = sorted(set(a) | set(b))
            return f"#{i} " + "; ".join(f"{k}: {str(a.get(k, '<absent>'))[:120]!r} vs {str(b.get(k, '<absent>'))[:120]!r}" for k in keys if a.get(k, "<absent>") != b.get(k, "<absent>"))
    return ""


def run_both(tmp_path, source: dict, force: bool = False):
    from newsroom.engine.sources.rss import fetch_rss

    path = tmp_path / "source.json"
    path.write_text(json.dumps({"source": source, "force": force}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x", "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/rss-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=120)
    node = json.loads(out.stdout.decode().strip().splitlines()[-1])
    try:
        py = {"ok": True, **fetch_rss(source, force)}
    except Exception as e:
        py = {"ok": False, "error": str(e)[:200], "name": type(e).__name__}
    return _snake(node), _plain(py)


def source_of(url: str, **config):
    return {"id": "t-rss", "name": "T", "kind": "rss", "config": {"feedUrl": url, **config}, "tier": "T2", "participation_mode": "editorial", "uses": [], "first_party": False,
            "interval_minutes": 30, "enabled": True, "cursor": None, "fail_count": 0}  # fmt: skip


@pytest.mark.parametrize("name", ["rss2.xml", "atom.xml", "rdf.xml"])
def test_feeds(origin, tmp_path, name):
    for extra in ({}, {"summaryIsBody": True}, {"publishedAtUtcOffset": "+00:00"}):
        node, py = run_both(tmp_path, source_of(f"{origin}/{name}", **extra))
        assert node["ok"] and py["ok"], (node, py)
        assert py["candidates"] == node["candidates"], f"{name} {extra}: {_diff(node['candidates'], py['candidates'])}"
        assert py["validator"]["configHash"] == node["validator"]["configHash"]
        assert node["candidates"]


@pytest.mark.parametrize("path", ["html.html", "missing.xml"])
def test_failures(origin, tmp_path, path):
    node, py = run_both(tmp_path, source_of(f"{origin}/{path}"))
    assert node["ok"] is False and py["ok"] is False
    assert py["error"].split(":")[0] == node["error"].split(":")[0]


def test_no_feed_url(tmp_path):
    node, py = run_both(tmp_path, {**source_of(""), "config": {}})
    assert (node["ok"], node["error"]) == (py["ok"], py["error"]) == (False, "feedUrl missing")


def test_not_modified(origin, tmp_path):
    first_node, first_py = run_both(tmp_path, source_of(f"{origin}/cond.xml"))
    assert first_py["validator"] == first_node["validator"] and first_py["validator"]["etag"] == '"v1"'
    again = {**source_of(f"{origin}/cond.xml"), "cursor": {"rss": first_node["validator"]}}
    node, py = run_both(tmp_path, again)
    assert node["notModified"] is True and py["notModified"] is True and py["candidates"] == node["candidates"] == []
    forced_node, forced_py = run_both(tmp_path, again, force=True)
    assert forced_py["candidates"] == forced_node["candidates"] and forced_node["candidates"]


def test_a_feed_that_is_not_well_formed_is_read_as_far_as_it_goes(origin, tmp_path):
    node, py = run_both(tmp_path, source_of(f"{origin}/bad.xml"))
    assert node["ok"] and py["ok"] and py["candidates"] == node["candidates"] == []
