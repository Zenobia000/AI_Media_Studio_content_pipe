"""Translating selected items: the same articles handled by the TypeScript engine and by the Python port, against fake model servers that sometimes drop a link, lose
count or refuse, in their own copies of one database, must end with the same translations, attempts, quoted-post translations and receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

EN = "This is an English paragraph that talks about models, evaluation and how researchers measure progress over time. "
QUOTED = "The quoted post is about large language model evaluation methods and benchmarks."
SCENARIOS = {  # key: (channel, html, text, language, x_post, x_article)
    "body_ok": ("news", '<h2>Heading words</h2><p>First paragraph with <a href="https://a.example/x?a=1&amp;b=2" title="t">a link</a> and <code>code()</code>.</p>'
                       '<p>Second <img src="https://i.example/a.png" width="10" height="10"> picture <em>here</em>.</p><ul><li>Item one</li><li>Item two</li></ul>', EN, "en", None, None),
    "droplink": ("news", '<p>DROPLINK paragraph with <a href="https://a.example/d">a link</a>.</p><p>Plain second paragraph.</p>', EN, "en", None, None),
    "alwaysdrop": ("news", '<p>ALWAYSDROP has <a href="https://a.example/e">a link</a>.</p><p>Plain second paragraph here.</p>', EN, "en", None, None),
    "wrongcount": ("news", "<p>WRONGCOUNT one.</p><p>WRONGCOUNT two.</p>", EN, "en", None, None),
    "split": ("news", "<p>Alpha paragraph.</p><p>Beta paragraph.</p><p>SPLITME gamma paragraph.</p>", EN, "en", None, None),
    "multi": ("news", "".join(f"<p>{EN * 15} number {i}</p>" for i in range(5)), EN, "en", None, None),
    "chinese": ("news", "<p>這是一段中文內容，不需要翻譯。</p>", "這是一段中文內容，不需要翻譯。", "zh", None, None),
    "code_only": ("news", "<pre><code>just code</code></pre>", "just code", "en", None, None),
    "x_long": ("x", None, "Long English post about GPT models and what they can do for research teams in practice today. " * 2, "en", {"text": "Long English post about GPT models and what they can do for research teams in practice today. https://t.co/abc " * 2}, None),
    "x_short": ("x", None, "Short post here", "en", {"text": "Short post here"}, None),
    "x_link": ("x", None, "https://t.co/abc", "en", {"text": "https://t.co/abc"}, None),
    "x_zh": ("x", None, "這是一則中文貼文，不用翻譯，內容也夠長了，所以只是跳過而已，沒有別的原因。", "zh", {"text": "這是一則中文貼文，不用翻譯，內容也夠長了，所以只是跳過而已，沒有別的原因。"}, None),
    "x_article": ("x", None, "A post that links a saved long article, long enough to pass the short rule easily.", "en", {"text": "A post that links a saved long article, long enough to pass the short rule easily."}, {"text": "The saved long article body."}),
    "x_bad": ("x", None, "WRONGCOUNT post in English that is long enough to be translated by the model, really.", "en", {"text": "WRONGCOUNT post in English that is long enough to be translated by the model, really."}, None),
    "x_quote": ("x", None, "Interesting thread about models and evals by researchers today, worth reading in full.", "en",
                {"text": "Interesting thread about models and evals by researchers today, worth reading in full.", "quoted": {"url": "https://x.com/u/status/12345", "text": QUOTED}}, None),
    "x_quote2": ("x", None, "Another thread quoting the same post, which also says things about evaluation.", "en",
                 {"text": "Another thread quoting the same post, which also says things about evaluation.", "quoted": {"url": "https://x.com/v/status/12345", "text": QUOTED}}, None),
    "x_quote_own": ("x", None, "Third thread quoting a post we collected and translated ourselves, in English.", "en",
                    {"text": "Third thread quoting a post we collected and translated ourselves, in English.", "quoted": {"url": "https://x.com/w/status/777", "text": "Quoted post we also have as an item."}}, None),
    "x_quote_done": ("x", None, "Fourth thread quoting a post already translated by the earlier run.", "en",
                     {"text": "Fourth thread quoting a post already translated by the earlier run.", "quoted": {"url": "https://x.com/z/status/999", "text": "Already translated quoted text goes here."}}, None),
    "x_quote_zh": ("x", None, "Fifth thread quoting a Chinese post, so the quote needs nothing at all.", "en",
                   {"text": "Fifth thread quoting a Chinese post, so the quote needs nothing at all.", "quoted": {"url": "https://x.com/q/status/555", "text": "這是被引用的中文貼文，不需要翻譯。"}}, None),
    "plain_unselected": ("news", "<p>Not selected paragraph.</p>", EN, "en", None, None),
    "fail429": ("news", "<p>FAIL429 paragraph.</p>", EN, "en", None, None),
}
RETRY_SYSTEM_MARK = "retry"


class Fake:
    """Translates by putting 譯: in front of each segment, with the faults named in the segments' words."""

    def __init__(self, retry_system: str):
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
                outer.requests.append((hashlib.sha1(system.encode()).hexdigest()[:8], user))
                if "FAIL429" in user:
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"code": "x", "message": "slow down"}}).encode())
                    return
                segs = json.loads(user)["segments"]
                if "WRONGCOUNT" in user:
                    out = []
                elif "SPLITME" in user and len(segs) > 1:
                    out = ["x"] * (len(segs) - 1)
                else:
                    out = []
                    for s in segs:
                        z = "譯:" + s
                        if "ALWAYSDROP" in s or ("DROPLINK" in s and system != retry_system):
                            z = re.sub(r"</?a[^>]*>", "", z)
                        out.append(z)
                data = json.dumps({"id": "c", "choices": [{"message": {"content": json.dumps({"t": out}, ensure_ascii=False)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["tr_node", "tr_py"])
    admin.close()
    ids = {}
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            c.execute("UPDATE publications SET selected = false")
            c.execute("DELETE FROM receipts")
            c.execute("DELETE FROM translations")
            c.execute("DELETE FROM translation_attempts")
            c.execute("DELETE FROM quote_translations")
            rows = c.execute("SELECT a.id FROM articles a JOIN publications p ON p.article_id = a.id ORDER BY a.identity_key LIMIT %s", (len(SCENARIOS) + 1,)).fetchall()
            ids[name] = {}
            for n, (r, (key, (channel, html, text, language, x_post, x_article))) in enumerate(zip(rows, SCENARIOS.items(), strict=False)):
                ids[name][key] = r["id"]
                c.execute(
                    """UPDATE articles SET body_html = %s, body_text = %s, language = %s, x_post = %s, x_article = %s, revision = 1 WHERE id = %s""",
                    (html, text, language, Jsonb(x_post) if x_post else None, Jsonb(x_article) if x_article else None, r["id"]),
                )
                c.execute(
                    "UPDATE publications SET channel = %s, selected = %s, visibility = 'public', body_mode = 'full', discovered_at = now() - %s * interval '1 minute' WHERE article_id = %s",
                    (channel, key != "plain_unselected", n, r["id"]),
                )
            spare = rows[len(SCENARIOS)]["id"] if len(rows) > len(SCENARIOS) else None
            assert spare
            c.execute("UPDATE articles SET identity_key = 'x:777', revision = 1 WHERE id = %s", (spare,))
            c.execute("UPDATE publications SET selected = false WHERE article_id = %s", (spare,))
            c.execute("INSERT INTO translations (article_id, lang, revision, title, body_html, body_text, complete, origin) VALUES (%s, 'zh', 1, 't', '<p>x</p>', 'Own zh text', true, 'model')", (spare,))
            c.execute("INSERT INTO quote_translations (tweet_id, text_hash, text_zh, origin) VALUES ('999', %s, '早就翻好', 'model')", (hashlib.sha256(b"Already translated quoted text goes here.").hexdigest(),))
    return urls, ids


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {
            "translations": c.execute(
                """SELECT a.identity_key, t.lang, t.revision, t.title, t.body_html, t.body_text, t.complete, t.origin FROM translations t JOIN articles a ON a.id = t.article_id
                   ORDER BY a.identity_key, t.lang"""
            ).fetchall(),
            "attempts": c.execute(
                "SELECT a.identity_key, t.revision, t.attempts, t.outcome, t.reason FROM translation_attempts t JOIN articles a ON a.id = t.article_id ORDER BY a.identity_key"
            ).fetchall(),
            "quotes": c.execute("SELECT tweet_id, text_hash, text_zh, origin FROM quote_translations ORDER BY tweet_id").fetchall(),
            "receipts": c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall(),
        }


def run_node(urls, fake, op: dict, tmp_path):
    path = tmp_path / "in.json"
    path.write_text(json.dumps(op), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": urls["tr_node"], "TZ": "UTC", "MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}
    out = subprocess.run(["node", "scripts/translate-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line[:1] in "[{"))


def same(world, label, node_out, py_out, node_fake, py_fake):
    urls, _ = world
    assert py_out == node_out, (label, node_out, py_out)
    a, b = dump(urls["tr_node"]), dump(urls["tr_py"])
    for part in a:
        assert b[part] == a[part], f"{label}: {part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:1500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:1500]}"
    assert sorted(py_fake.requests) == sorted(node_fake.requests), label


def test_translating_items(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.models import invalidate_model_cache
    from newsroom.engine.prompts import prompt_text
    from newsroom.engine.receipts import failure_kind
    from newsroom.engine.translate import translate_article, translate_pending

    urls, ids = world
    retry = prompt_text("translate-body-retry")
    node_fake, py_fake = Fake(retry), Fake(retry)
    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    keys = [k for k in SCENARIOS if k != "fail429"]
    with ConnectionPool(urls["tr_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        # 1. each item translated on its own
        node_out = run_node(urls, node_fake, {"op": "article", "ids": [ids["tr_node"][k] for k in keys] + ["no-such-article"]}, tmp_path)
        py_out = []
        for k in [*keys, None]:
            py_out.append(translate_article(pool, ids["tr_py"][k] if k else "no-such-article"))
        # the articles' ids are the same in both copies of the database
        same(world, "articles", node_out, py_out, node_fake, py_fake)
        statuses = {r["articleId"]: r["status"] for r in py_out}
        assert {"translated", "partial", "failed", "skipped"} <= set(statuses.values()), statuses

        # 2. the periodic run over what is left, twice (attempts add up), with the quoted posts
        for url in (urls["tr_node"], urls["tr_py"]):
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("DELETE FROM translations WHERE origin = 'model' AND article_id IN (SELECT id FROM articles WHERE identity_key <> 'x:777')")
                c.execute("DELETE FROM receipts")
        node_fake.requests.clear()
        py_fake.requests.clear()
        for round_ in (1, 2):
            node_out = run_node(urls, node_fake, {"op": "pending"}, tmp_path)
            py_out = translate_pending(pool)
            same(world, f"pending {round_}", node_out, py_out, node_fake, py_fake)
        assert py_out["quotes"] >= 0 and any(r["status"] == "failed" for r in py_out["done"]) or round_ == 2

        # 3. a refusal ends the run without counting against the item
        for url in (urls["tr_node"], urls["tr_py"]):
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("UPDATE publications SET selected = false")
                c.execute("UPDATE publications SET selected = true WHERE article_id = (SELECT id FROM articles WHERE body_html LIKE '%%FAIL429%%')")
        node_id, py_id = ids["tr_node"]["fail429"], ids["tr_py"]["fail429"]
        node_out = run_node(urls, node_fake, {"op": "article", "ids": [node_id]}, tmp_path)
        try:
            py_out = [translate_article(pool, py_id)]
        except Exception as error:
            py_out = [{"error": failure_kind(error), "name": type(error).__name__}]
        same(world, "refusal", node_out, py_out, node_fake, py_fake)
        assert "error" in py_out[0]


def test_quotes_alone(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.models import invalidate_model_cache
    from newsroom.engine.prompts import prompt_text
    from newsroom.engine.translate import translate_quotes

    urls, _ = world
    retry = prompt_text("translate-body-retry")
    node_fake, py_fake = Fake(retry), Fake(retry)
    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()
    for url in (urls["tr_node"], urls["tr_py"]):
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("UPDATE publications SET selected = true WHERE article_id IN (SELECT id FROM articles WHERE x_post->'quoted' IS NOT NULL)")
            c.execute("DELETE FROM quote_translations WHERE tweet_id <> '999'")
            c.execute("DELETE FROM receipts")

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with ConnectionPool(urls["tr_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        node_out = run_node(urls, node_fake, {"op": "quotes"}, tmp_path)
        stored = translate_quotes(pool)
    same(world, "quotes", node_out, {"stored": stored}, node_fake, py_fake)
    assert stored >= 2
