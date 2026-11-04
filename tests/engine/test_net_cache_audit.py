"""The network guard (checked against the TypeScript one), guarded fetch against a local server, the read caches and the audit log."""

from __future__ import annotations

import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from newsroom.engine.audit import audit, audit_history, list_audit
from newsroom.engine.lib.cache import cached, cached_by_key, shared, shared_search
from newsroom.engine.lib.http_fetch import HttpStatusError, decode_body, guarded_fetch, language_headers, require_ok
from newsroom.engine.lib.netguard import BlockedAddressError, assert_public_url, guarded_lookup, is_blocked_address
from newsroom.engine.lib.page_content import UnavailablePageError, assert_page_content, content_document

N = json.loads((Path(__file__).parent / "net_fixtures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("address,expected", N["addrs"])
def test_blocked_addresses_match_the_typescript_guard(address, expected):
    assert is_blocked_address(address) is expected


@pytest.mark.parametrize("url,verdict", N["urls"])
def test_urls_the_guard_refuses(url, verdict):
    if verdict == "ok":
        assert assert_public_url(url) == url
    else:
        with pytest.raises((BlockedAddressError, ValueError)):
            assert_public_url(url)


@pytest.mark.parametrize("title,text,verdict", N["titles"])
def test_error_pages_are_not_content(title, text, verdict):
    if verdict == "ok":
        assert_page_content(title, text)
    else:
        with pytest.raises(UnavailablePageError):
            assert_page_content(title, text)


def test_a_listed_private_host_may_reach_a_private_service_but_not_metadata():
    assert assert_public_url("http://10.0.0.5/feed", private_hosts=("10.0.0.5",)) == "http://10.0.0.5/feed"
    with pytest.raises(BlockedAddressError):
        assert_public_url("http://169.254.169.254/", private_hosts=("169.254.169.254",))
    assert guarded_lookup("feeds.internal.example", ("feeds.internal.example",), resolve=lambda h: ["10.0.0.7"]) == ["10.0.0.7"]
    with pytest.raises(BlockedAddressError):
        guarded_lookup("rebind.example", resolve=lambda h: ["8.8.8.8", "127.0.0.1"])


def test_content_document_checks_the_title_heading_and_opening_text():
    ok = content_document("<html><head><title>Real</title></head><body><h1>Heading</h1><p>text</p></body></html>")
    assert ok.xpath("//h1/text()") == ["Heading"]
    with pytest.raises(UnavailablePageError):
        content_document("<html><head><title>404 Not Found</title></head><body><p>x</p></body></html>")
    with pytest.raises(UnavailablePageError):
        content_document("<html><head><title>Fine</title></head><body><h1>Access denied</h1></body></html>")


# ── guarded fetch ───────────────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):  # noqa: N802
        Handler.seen.append(
            {"path": self.path, "ua": self.headers.get("user-agent"), "lang": self.headers.get("accept-language"), "x": self.headers.get("x-key")}
        )
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("location", "/ok")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("location", "/loop")
            self.end_headers()
        elif self.path == "/other-origin":
            self.send_response(302)
            self.send_header("location", f"http://localhost:{self.server.server_port}/ok")
            self.end_headers()
        elif self.path == "/big":
            body = b"x" * 5000
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gzip":
            body = gzip.compress("壓縮的內容".encode() * 50)
            self.send_response(200)
            self.send_header("content-encoding", "gzip")
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/gbk":
            self.send_response(200)
            self.send_header("content-type", 'text/html; charset="gb2312"')
            self.end_headers()
            self.wfile.write("中文頁面".encode("gbk"))
        elif self.path == "/404":
            self.send_response(404)
            self.end_headers()
        else:
            body = b"hello"
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_the_guard_refuses_a_local_server_by_default(server, monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_NETWORK_FETCH", raising=False)
    with pytest.raises(BlockedAddressError):
        guarded_fetch(server + "/ok")


def test_fetch_follows_redirects_limits_size_and_decodes(server, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_FETCH", "true")
    res = guarded_fetch(server + "/redirect", headers={"User-Agent": "Mine/1"})
    assert (res.status, res.text(), res.url.endswith("/ok")) == (200, "hello", True)
    assert Handler.seen[-1]["ua"] == "Mine/1" and "zh-CN" in Handler.seen[-1]["lang"]
    with pytest.raises(RuntimeError, match="Too many redirects"):
        guarded_fetch(server + "/loop", max_redirects=2)
    with pytest.raises(RuntimeError, match="too large"):
        guarded_fetch(server + "/big", max_bytes=100)
    cut = guarded_fetch(server + "/big", max_bytes=100, truncate=True)
    assert cut.truncated and len(cut.body) == 100
    assert guarded_fetch(server + "/gzip").text().startswith("壓縮的內容")
    assert guarded_fetch(server + "/gbk").text() == "中文頁面"
    with pytest.raises(HttpStatusError) as e:
        require_ok(guarded_fetch(server + "/404"), "the page")
    assert e.value.status == 404 and "HTTP 404 for the page" in str(e.value)


def test_a_request_with_a_credential_header_stays_on_its_first_origin(server, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_FETCH", "true")
    with pytest.raises(RuntimeError, match="cross-origin"):
        guarded_fetch(server + "/other-origin", headers={"x-key": "secret"})
    assert guarded_fetch(server + "/other-origin").status == 200  # public headers only: the redirect is followed


def test_body_decoding_and_language_headers():
    assert decode_body("é中".encode(), "text/html; charset=utf-8") == "é中"
    assert decode_body(b"a\x00b", None) == "a�b"
    assert decode_body("中".encode("gbk"), 'text/html; charset="gbk"') == "中"
    assert decode_body(b'<meta charset="iso-8859-1">\xe9', None).endswith("é")
    assert decode_body(b"x\xff\xfe\xfdy", "text/plain").count("�") == 1
    assert language_headers("en") == {"accept-language": "en"} and language_headers(None) == {}


# ── caches ──────────────────────────────────────────────────────────────────────────────


def test_shared_reads_overlap_but_nothing_is_kept():
    calls = []
    gate = threading.Event()

    def load(key):
        calls.append(key)
        gate.wait(2)
        return key * 2

    read = shared(lambda k: str(k), load)
    out: list[int] = []
    threads = [threading.Thread(target=lambda: out.append(read(21))) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert out == [42] * 4 and calls == [21]
    assert read(21) == 42 and calls == [21, 21]


def test_cached_serves_stale_while_one_background_read_refreshes():
    n = [0]

    def load(_):
        n[0] += 1
        return n[0]

    c = cached(load, fresh_ms=60, max_stale_ms=2000)
    assert c.get() == 1 and c.get() == 1
    time.sleep(0.1)
    assert c.get() == 1  # stale value now, refresh in the background
    time.sleep(0.2)
    assert c.get() == 2


def test_cached_waits_past_max_stale_and_a_failed_refresh_keeps_the_old_value():
    state = {"fail": False, "n": 0}

    def load(_):
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("down")
        return state["n"]

    c = cached(load, fresh_ms=10, max_stale_ms=5000)
    assert c.get() == 1
    state["fail"] = True
    time.sleep(0.05)
    assert c.get() == 1
    time.sleep(0.1)
    assert c.get() == 1
    short = cached(lambda _: time.time(), fresh_ms=1, max_stale_ms=1)
    first = short.get()
    time.sleep(0.05)
    assert short.get() > first


def test_cached_by_key_drops_the_oldest_key_and_shared_search_reads_alone_with_a_clock():
    loads: list[str] = []
    read = cached_by_key(lambda k: k, lambda k: loads.append(k) or k.upper(), fresh_ms=10_000, max_stale_ms=10_000, max_keys=2)
    assert [read("a"), read("b"), read("a"), read("c"), read("a")] == ["A", "B", "A", "C", "A"]
    assert loads == ["a", "b", "c", "a"]
    seen = []
    search = shared_search(lambda k: k, lambda k, now: seen.append((k, now is not None)) or k, lambda k: k.startswith("q"))
    assert search("plain") == "plain" and search("q1") == "q1" and search("q1", now=object()) == "q1" and len(seen) == 3


# ── audit ───────────────────────────────────────────────────────────────────────────────


def test_every_manual_change_is_recorded_with_before_and_after(conn, tag):
    subject = f"source:test-{tag}"
    audit(conn, "admin", "source.update", subject, "fix the feed", {"enabled": False}, {"enabled": True, "名稱": "中文"}, request_id="r1")
    audit(conn, "admin", "source.note", subject, None, None, None)
    history = audit_history(conn, subject)
    assert [h["action"] for h in history] == ["source.note", "source.update"]
    assert history[1]["before"] == {"enabled": False} and history[1]["after"]["名稱"] == "中文" and history[0]["before"] is None
    page = list_audit(conn, 1, subject=subject)
    assert page["page"] == 1 and len(page["rows"]) == 2
    assert all(r["action"].startswith("source.") for r in list_audit(conn, 1, action="source.")["rows"])
