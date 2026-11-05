"""The page reader (engine/page_reader.py) must read pages as the engine's readable() does (AIHOT/scripts/extract-run.ts): the source's rules, the
page's publication date, the article Readability finds, and the sanitised result."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from newsroom.engine.page_reader import PageRules, PreserveRule, readable
from tests.engine.reader_pages import PAGES, body, page, para, random_pages

AIHOT = Path(__file__).parents[2] / "AIHOT"
URL = "https://www.example.org/news/story-1"

LONG = body(6)
CSS = (
    ".search-button{align-items:center;background-color:var(--bg);border:1px solid var(--border);border-radius:9999px;color:var(--text);cursor:pointer;display:flex;margin:0;padding:.25rem .75rem}"
    * 6
)
REACT = (
    f'<div id="root"><!--$?--><template id="B:0"></template><p>Loading…</p><!--/$--></div><div hidden id="S:0">{LONG}</div>'
    '<script>$RC("B:0","S:0")</script>'
)
EXTRA: dict[str, tuple[str, dict]] = {
    "react_streaming": (page("Streamed", REACT), {}),
    "react_streaming_runtime": (
        page("Streamed", REACT.replace('<script>$RC("B:0","S:0")</script>', '<script>$RC=function(a,b){x};$RC("B:0","S:0")</script>')),
        {},
    ),
    "inline_visibility": (
        page(
            "Styles",
            f'<div><div style="Display : NONE !important; display: block">{LONG}</div><div style="color:red;VISIBILITY:Hidden">{LONG}</div>{body(4)}</div>',
        ),
        {},
    ),
    "preserve_pre": (
        page(
            "Code",
            f'<div>{body(3)}<div class="snippet"><span class="label">main.py</span><div class="src"><span aria-hidden="true">1</span>print(1)\nprint(2)</div></div>{body(3)}</div>',
        ),
        {"preserve": [{"selector": ".snippet", "as": "pre", "code": ".src", "caption": ".label"}]},
    ),
    "preserve_table": (
        page(
            "Compare",
            f'<div>{body(3)}<div class="cmp"><div><h4>A</h4><dl><dt>x</dt><dd>1</dd><dt>y</dt><dd>2</dd></dl></div><div><h4>B</h4><dl><dt>x</dt><dd>3</dd><dt>y</dt><dd>4</dd></dl></div></div>{body(3)}</div>',
        ),
        {"preserve": [{"selector": ".cmp", "as": "table"}]},
    ),
    "figure_drawings": (
        page(
            "Drawings",
            f'<article>{body(3)}<figure><div role="img" aria-label="A bar chart of sales"><svg viewBox="0 0 1 1"><rect/></svg></div><figcaption>Sales by quarter</figcaption></figure>'
            f'<figure><svg aria-labelledby="t1"><title id="t1x">x</title></svg><span id="t1">Labelled drawing text</span></figure><figure><svg><title>Only title</title><desc>and desc</desc></svg></figure>{body(3)}</article>',
        ),
        {},
    ),
    "asides": (
        page(
            "Asides",
            f'<div>{para(1)}<aside><p>Update: this article was corrected after publication, with new facts and figures.</p></aside>{para(2)}<aside><p><a href="/x">Links</a></p></aside>{body(3)}</div>',
        ),
        {},
    ),
    "layout_classes": (
        page("Layout", f'<div class="overflow-hidden md:scroll-mt-24 content">{LONG}</div><div class="sidebar">{body(2)}</div>'),
        {},
    ),
    "trailing_chrome": (
        page("Chrome", f"<article>{LONG}<h4>Topics</h4><p>Subscribe for the industry's biggest tech news</p><p>Latest in AI</p></article>"),
        {},
    ),
    "trailing_chrome_kept": (page("Chrome kept", f"<article>{LONG}<p>Read more about it.</p><h4>Topics</h4></article>"), {}),
    "dates_meta": (
        page(
            "Dated",
            f"<div>{LONG}</div>",
            head='<meta property="article:published_time" content="2026-10-29"><meta itemprop="datePublished" content="2026-10-29T08:15:00+08:00">',
        ),
        {},
    ),
    "dates_jsonld": (
        page(
            "Dated",
            f"<div>{LONG}</div>",
            head='<script type="application/ld+json">{"@graph":[{"@type":"Article","datePublished":"2026-09-01T10:00:00Z"}]}</script>',
        ),
        {"utcOffset": "+00:00"},
    ),
    "dates_time_tag": (
        page("Dated", f'<div><time itemprop="datePublished" datetime="2026-08-08 10:30">Aug 8</time>{LONG}</div>'),
        {"utcOffset": "-05:00"},
    ),
    "relative_urls": (
        page(
            "Urls",
            f'<div><p>{"Text with a link, and more words, to make it long enough. " * 6}<a href="/other?utm_source=x">link</a> <a href="../up">up</a> <a href="//cdn.example.org/x">proto</a></p>'
            f'<img src="/img/a.png" width="300" height="200"><img src="img/b.jpg?x=1&y=2"><picture><source srcset="/s.webp 1x, /s2.webp 2x"><img src="/p.jpg"></picture>{body(4)}</div>',
        ),
        {},
    ),
    "video_url": (page("Video", f"<div>{LONG}</div>"), {}),
    # A page that declares a shadow root (a <template> with a <style>, as MDN does): querySelectorAll does not go into it, so Readability's removal of <style>
    # leaves that CSS, which counts as text of the elements around it. The article sits in a container whose class reads as unlikely, so the first pass
    # finds nothing and the second pass (the one that counts) scores the page with that text in it.
    "shadow_root_style": (
        page(
            "Shadow",
            f'<div class="page"><div class="navigation"><my-search><template shadowroot="open" shadowrootmode="open"><style>{CSS}</style><button>Search</button>'
            "</template></my-search></div>"
            f'<main id="content"><div class="layout-sidebars-inline reference-layout"><div class="layout-body reference-layout-body">{body(6)}</div></div></main></div>',
        ),
        {},
    ),
}
EXTRA_URLS = {"video_url": "https://www.youtube.com/watch?v=abc"}


@pytest.fixture(scope="module")
def cases():
    pages = {name: (html, URL, {}) for name, html in {**PAGES, **random_pages(150, seed=11)}.items()}
    pages.update({name: (html, EXTRA_URLS.get(name, URL), rules) for name, (html, rules) in EXTRA.items()})
    return pages


@pytest.fixture(scope="module")
def node_results(cases, tmp_path_factory):
    if not shutil.which("node") or not (AIHOT / "node_modules").exists():
        pytest.skip("needs Node with the AIHOT dependencies installed")
    path = tmp_path_factory.mktemp("page") / "pages.json"
    path.write_text(json.dumps([{"html": h, "url": u, "rules": r} for h, u, r in cases.values()]), encoding="utf-8")
    env = {**os.environ, "TZ": "UTC", "DATABASE_URL": "postgres://x"}
    out = subprocess.run(["node", "scripts/extract-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    return dict(zip(cases, json.loads(out.stdout.decode().strip().splitlines()[-1]), strict=True))


def rules_of(raw: dict) -> PageRules:
    return PageRules(
        raw.get("utcOffset"),
        [PreserveRule(r["selector"], r["as"], r.get("code"), r.get("caption")) for r in raw.get("preserve", [])],
        raw.get("acceptLanguage"),
    )


def test_every_page(cases, node_results):
    failures = []
    read = 0
    for name, (html, url, raw) in cases.items():
        node = node_results[name]
        try:
            got = readable(html, url, rules_of(raw))
        except Exception as e:
            got = {"error": str(e)}
        if node is None or got is None:
            if not (node is None and got is None):
                failures.append(
                    f"{name}: {'nothing' if node is None else 'an article'} from TypeScript, {'nothing' if got is None else 'an article'} from Python"
                )
            continue
        if "error" in node or isinstance(got, dict):
            if ("error" in node) != isinstance(got, dict):
                failures.append(f"{name}: error mismatch {node} / {got}")
            continue
        read += 1
        py = {
            "html": got.html, "text": got.text, "images": got.images, "via": got.via,
            "publishedAt": got.published_at.isoformat(timespec="milliseconds").replace("+00:00", "Z") if got.published_at else None,
            "publicationTime": got.publication_time,
        }  # fmt: skip
        for key, value in py.items():
            if node.get(key) != value:
                failures.append(f"{name}.{key}:\n py   {str(value)[:300]!r}\n node {str(node.get(key))[:300]!r}")
                break
    assert not failures, "\n".join(failures[:8]) + f"\n({len(failures)} of {len(cases)} pages differ)"
    assert read > 80
