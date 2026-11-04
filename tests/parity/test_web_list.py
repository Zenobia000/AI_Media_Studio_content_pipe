"""fetchWebList: listing pages served by a local server, read by the TypeScript collector and by the Python port, must give the same candidates."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.parity.conftest import AIHOT, ENV
from tests.parity.test_rss import _diff, _plain, _snake

PAD = "<p>" + "Filler paragraph so the page is long enough to be a real page. " * 6 + "</p>"
HTML_LIST = f"""<html><head><title>Blog</title></head><body><h1>Blog index</h1>{PAD}
<nav><a href="/login">Log in</a><a href="/blog/">Blog</a><a href="/blog/page/2">Older</a><a href="#top">Top</a></nav>
<div class="card"><a class="lnk" href="/blog/one?utm_source=x"><h3>First  post</h3></a><time datetime="2026-09-26T10:00:00+08:00">Sep 26</time><span class="d">x</span></div>
<div class="card"><a class="lnk" href="http://127.0.0.1/blog/two#frag" title="Title only">  </a><time>2026年9月25日</time></div>
<div class="card"><a class="lnk" href="https://other.example/post/3"><h3>Third post &amp; more</h3></a><span class="d">Posted on 2026-09-20 by me</span></div>
<div class="card"><a class="lnk" href="/blog/one?utm_source=y"><h3>Duplicate</h3></a></div>
<div class="card"><a class="lnk" href="mailto:me@example.org"><h3>Mail</h3></a></div>
<div class="card"><a class="lnk" href="/blog/four"><h3>Fourth</h3></a><div title="2026-09-18">tooltip</div><time>no date</time></div>
<div class="card"><a href="/blog/five">Five</a><span class="d">&nbsp;Updated 2026-09-10&nbsp;</span></div>
<a href="/a/six">Six plain link</a><a href="/2026/09/">archive</a><a href="/tag/ai">tag</a>
</body></html>"""
MARKDOWN = (
    "# Blog\n\n" + "Some intro text to make the page long enough to look like a page of content. " * 6 + "\n\n"
    "## [First article title](/blog/first \"First article title\")\n"
    "- [Second article about things](https://127.0.0.1/blog/second)\n"
    "Prose mentioning a [chaotic three weeks](https://127.0.0.1/blog/third) in the middle.\n"
    "[![cover](https://img.example/a.png) ##### Card title here and more](/blog/card \"Card title here\")\n"
    "[![only image](https://img.example/b(1).png)](/blog/imageonly)\n"
    "[Read more about the thing](/blog/readmore)\n[Read more](/blog/readmore2)\n"
    "[Log in to continue](/login)\n[Tag page for ai](/tag/ai)\n[Short](/blog/s)\n"
    "1. [Numbered item title](/blog/numbered)\n"
)
DOCUSAURUS = f"""<html><head><title>Changelog</title></head><body><h1>Changelog</h1>{PAD}<article>
<h2 id="v-2026-09-10">2026-09-10​#</h2><h3 id="feat-a">Feature A</h3><p>Adds <b>A</b> to the product and makes it much better for everyone.</p><ul><li>one</li></ul>
<h3 id="feat-b">Feature B 2026-09-08</h3><p>Adds B.</p><h2 id="v-plain">Release notes</h2><p>Published 2026-08-01 with changes.</p>
<h3 id="fix-c">Fix C</h3><p>Fixes C.</p><h2 id="v-2026-08-30">時間：2026年8月30日</h2><h3 id="deny-me">Denied</h3><p>nope</p></article></body></html>"""
INTERCOM = f"""<html><head><title>Releases</title></head><body><h1>Releases</h1>{PAD}<article>
<div class="intercom-interblocks-subheading3"><h3 id="sep-26">September 26, 2026</h3></div>
<div class="intercom-interblocks-paragraph"><p><b>Big feature</b></p></div><div class="intercom-interblocks-paragraph"><p>It does <i>things</i> well.</p></div>
<div class="intercom-interblocks-subheading3"><h3 id="sep-20">September 20, 2026</h3></div>
<div class="intercom-interblocks-paragraph"><p><strong>Another</strong></p></div>
<div class="intercom-interblocks-subheading3"><h3 id="undated">Not a date</h3></div><div class="x"><p>skipped</p></div>
<div class="intercom-interblocks-subheading3"><h3 id="empty">September 1, 2026</h3></div>
</article></body></html>"""
MIMO_HOME = f'<html><head><title>MiMo</title></head><body><h1>MiMo</h1>{PAD}<script src="/lib.js"></script><script src="/static/js/main.abc.js"></script></body></html>'
MIMO_MAIN = 'var n={};n.p="/";n.u=e=>"static/js/async/"+e+"."+({100:"aaa111",200:"bbb222"})[e]+".js";var r=[{path:"/",component:x,loader:()=>Promise.all([n.e("100"),n.e("200")])}];'
MIMO_CHUNK = ('x=1;sectionTitle:"Other",rows:[{title:"no"}];sectionTitle:"Blog",blogs:[{title:"Hello \\x41 \\"world\\"",link:"/blog/a",desc:"First desc"},'
              '{title:"Second",link:"/blog/b",desc:"Second"},{title:"Third",link:"/blog/a",desc:"dup"},{title:"Home",link:"/",desc:""}],sectionTitle:"Next"')
PAGES = {
    "/list.html": ("text/html", HTML_LIST), "/list.md": ("text/html", MARKDOWN), "/changelog.html": ("text/html", DOCUSAURUS), "/releases.html": ("text/html", INTERCOM),
    "/mimo.html": ("text/html", MIMO_HOME), "/lib.js": ("application/javascript", "var lib=1;"), "/static/js/main.abc.js": ("application/javascript", MIMO_MAIN),
    "/static/js/async/200.bbb222.js": ("application/javascript", MIMO_CHUNK), "/static/js/async/100.aaa111.js": ("application/javascript", "none"),
    "/nomatch.html": ("text/html", f"<html><head><title>Empty</title></head><body><h1>Empty</h1>{PAD}</body></html>"),
}


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hit = PAGES.get(self.path.split("?")[0])
            self.send_response(200 if hit else 404)
            if hit:
                self.send_header("Content-Type", hit[0] + "; charset=utf-8")
            self.end_headers()
            if hit:
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


def source_of(config: dict) -> dict:
    return {"id": "t-web", "name": "T", "kind": "web_list", "config": config, "tier": "T2", "participation_mode": "editorial", "uses": [], "first_party": False,
            "interval_minutes": 30, "enabled": True, "cursor": None, "fail_count": 0}  # fmt: skip


def run_both(tmp_path, config: dict):
    from newsroom.engine.sources.web_list import fetch_web_list

    path = tmp_path / "source.json"
    source = source_of(config)
    path.write_text(json.dumps({"source": source}), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x", "TZ": "UTC"}
    out = subprocess.run(["node", "scripts/web-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=120)
    node = json.loads(out.stdout.decode().strip().splitlines()[-1])
    try:
        py = {"ok": True, "candidates": fetch_web_list(source)}
    except Exception as e:
        py = {"ok": False, "error": str(e)[:200], "name": type(e).__name__}
    return _snake(node), _plain(py)


CASES = {
    "html generic": {"url": "/list.html"},
    "html cards": {"url": "/list.html", "itemSelector": ".card", "linkSelector": ".lnk", "titleSelector": "h3", "publishedAtSelector": "time"},
    "html cards regex": {"url": "/list.html", "itemSelector": ".card", "publishedAtSelector": "time", "publishedAtRegex": r"(\d{4}-\d{2}-\d{2})"},
    "html cards fragment": {"url": "/list.html", "itemSelector": ".card", "preserveUrlFragment": True, "publishedAtSelector": ".d", "publishedAtUtcOffset": "+00:00"},
    "html allow deny": {"url": "/list.html", "itemSelector": ".card", "allowUrlPrefixes": ["http://127.0.0.1"], "denyUrlPrefixes": ["http://127.0.0.1/blog/four"]},
    "markdown": {"url": "/list.md", "parseMode": "markdown"},
    "markdown starts line": {"url": "/list.md", "parseMode": "markdown", "linksStartLine": True},
    "markdown base": {"url": "/list.md", "parseMode": "markdown", "baseUrl": "https://example.org/dir/"},
    "docusaurus": {"url": "/changelog.html", "parseMode": "docusaurus_changelog"},
    "docusaurus offset": {"url": "/changelog.html", "parseMode": "docusaurus_changelog", "publishedAtUtcOffset": "-05:00", "baseUrl": "https://example.org/log"},
    "intercom": {"url": "/releases.html", "parseMode": "intercom_changelog"},
    "mimo": {"url": "/mimo.html", "adapter": "mimo_home"},
}


@pytest.mark.parametrize("name", list(CASES))
def test_listings(origin, tmp_path, name):
    config = {**CASES[name], "url": origin + CASES[name]["url"]}
    if "allowUrlPrefixes" in config:
        config["allowUrlPrefixes"] = [origin if p == "http://127.0.0.1" else p for p in config["allowUrlPrefixes"]]
        config["denyUrlPrefixes"] = [origin + "/blog/four"]
    node, py = run_both(tmp_path, config)
    assert node["ok"] and py["ok"], (node, py)
    assert py["candidates"] == node["candidates"], f"{name}: {_diff(node['candidates'], py['candidates'])}"
    assert node["candidates"]


def test_nothing_matched(origin, tmp_path):
    node, py = run_both(tmp_path, {"url": origin + "/nomatch.html"})
    assert node["ok"] is py["ok"] is False and py["error"] == node["error"]


def test_missing_url_and_page(origin, tmp_path):
    for config in ({}, {"url": origin + "/gone.html"}):
        node, py = run_both(tmp_path, config)
        assert node["ok"] is py["ok"] is False and py["error"].split(":")[0] == node["error"].split(":")[0]


def test_mimo_without_tables(origin, tmp_path):
    node, py = run_both(tmp_path, {"url": origin + "/list.html", "adapter": "mimo_home"})
    assert node["ok"] is py["ok"] is False and py["error"] == node["error"]
