"""The parts of translation that work on markup - shielding a block from the model, putting it back, finding the blocks, and reusing translated wording on a
changed layout - must give what the TypeScript engine gives, on the same markup."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests.parity.conftest import AIHOT, ENV

BLOCKS = [
    "Plain text with <strong>bold</strong> and <em>emphasis</em>.",
    'See <a href="https://a.example/x?a=1&amp;b=2" title="T">the report</a> and <a href="/rel">another</a>.',
    'Look: <img src="https://i.example/a.png" width="100" height="50" alt="a &amp; b"> then more text.',
    "Run <code>npm install &lt;pkg&gt;</code> now, then <code>npm test</code>.",
    '<picture><source type="image/avif" srcset="a.avif"><img src="a.jpg"></picture> caption <a href="https://a.example">link</a>',
    'Non-breaking&nbsp;space, “quotes”, 中文 and <a href="https://a.example/?q=1&amp;r=&quot;x&quot;">a &quot;quoted&quot; link</a>',
    'A <video src="v.mp4" controls></video> clip and <a href="https://x.example"><img src="b.png"></a>',
    "Nothing special at all",
    "Tokens ⟦0⟧ already in text",
]
BODY = (
    "<h2>Heading one</h2><p>First paragraph with <a href=\"https://a.example\">a link</a>.</p><ul><li>Item <code>x</code></li><li>Second</li></ul>"
    "<pre><code>code block text</code></pre><blockquote><p>Quoted words</p></blockquote><table><tr><td>Cell A</td><td>12</td></tr></table>"
    "<figure><img src=\"f.png\"><figcaption>Figure caption</figcaption></figure><p>12345</p><p>   </p><div><p>Inside div</p>loose text</div><dl><dt>Term</dt><dd>Definition</dd></dl>"
)


def node(tmp_path, payload: dict):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": "postgres://x"}
    out = subprocess.run(["node", "scripts/translate-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line[:1] in "[{n\""))


@pytest.mark.parametrize("html", BLOCKS)
def test_shield_and_unshield(settings_env, tmp_path, html):
    from newsroom.engine.translate import shield, unshield

    want = node(tmp_path, {"op": "shield", "html": html})
    got = shield(html)
    assert got["html"] == want["html"] and got["tokens"] == want["tokens"]
    assert [dict(a) for a in got["links"]] == want["links"]
    # the model's good answer: the shielded text itself, then answers that lose, repeat or invent a placeholder or a link
    answers = [got["html"], got["html"].replace("⟦0⟧", ""), got["html"] + "⟦0⟧", got["html"].replace("<a", "<a data-x='1'"), "譯文 " + got["html"], got["html"].replace('id="L0"', 'id="L7"')]
    for answer in answers:
        assert unshield(answer, got) == node(tmp_path, {"op": "unshield", "translated": answer, "html": html}), answer


def test_segments(settings_env, tmp_path):
    from newsroom.engine.fragment import inner_html, parse
    from newsroom.engine.translate import segments_of

    got = [inner_html(el) for el in segments_of(parse(BODY))]
    assert got == node(tmp_path, {"op": "segments", "html": BODY}) and len(got) >= 7


A = '<p>Hello <a href="https://a.example/old">world</a> and <img src="https://i.example/1.png"> done.</p><p>Second block.</p>'
A_ZH = '<p>你好<a href="https://a.example/old">世界</a>，<img src="https://i.example/1.png">完成。</p><p>第二段。</p>'
REBASE = [
    (A, A, A_ZH),  # nothing changed
    (A, A.replace("/old", "/new").replace("1.png", "2.png"), A_ZH),  # links and media moved
    (A, A.replace("Second block.", "Another block."), A_ZH),  # text changed
    (A, A + "<p>Extra</p>", A_ZH),  # a block more
    (A, A, A_ZH.replace("世界", "世界和")),  # still fine
    (A, A, A_ZH.replace('<a href="https://a.example/old">世界</a>', "世界")),  # a link lost
    (A, A, A_ZH.replace('<img src="https://i.example/1.png">', "")),  # an image lost
    (A, A, A_ZH.replace('<a href="https://a.example/old">', '<a href="https://other.example">')),  # a link redirected
    (
        '<p><img src="a.png"> and <img src="a.png"> again</p>', '<p><img src="b.png"> and <img src="b.png"> again</p>', '<p><img src="a.png">與<img src="a.png">再次</p>',
    ),  # repeated identical media
    (
        '<p><img src="a.png"> and <img src="a.png"> again</p>', '<p><img src="b.png"> and <img src="c.png"> again</p>', '<p><img src="a.png">與<img src="a.png">再次</p>',
    ),  # repeated media with different replacements
    (
        '<p><picture><source srcset="a.avif"><img src="a.png"></picture> text <a href="x">y</a></p>',
        '<p><picture><source srcset="b.avif"><img src="b.png"></picture> text <a href="z">y</a></p>',
        '<p>文字 <a href="x">y</a><picture><source srcset="a.avif"><img src="a.png"></picture></p>',
    ),  # media moved within the sentence
    ("<p>One<br>two &amp; three</p><ul><li>a</li><li>b</li></ul>", "<p>One<br>two &amp; three</p><ul><li>a</li><li>b</li></ul>", "<p>一<br>二和三</p><ul><li>甲</li><li>乙</li></ul>"),
]


@pytest.mark.parametrize("previous,next_,translated", REBASE)
def test_rebase_translation(settings_env, tmp_path, previous, next_, translated):
    from newsroom.engine.presentation import PresentationMismatch, rebase_translation

    want = node(tmp_path, {"op": "rebase", "previous": previous, "next": next_, "translated": translated})
    try:
        got = {"html": rebase_translation(previous, next_, translated)}
    except PresentationMismatch as error:
        got = {"mismatch": str(error)}
    assert got == want
