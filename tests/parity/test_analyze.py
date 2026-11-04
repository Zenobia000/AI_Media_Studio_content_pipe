"""analyzeArticle: the same articles analysed by the TypeScript engine and by the Python port, each against its own fake model server and its own copy of one database,
must send the same prompts, reach the same judgement and leave the same analyses, receipts and article states."""

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

BODY = "OpenAI announced GPT-5.5 today, a model that improves reasoning and coding. The company says it is faster. " * 6
ARTICLES = {
    "pass": {"title": "OpenAI releases GPT-5.5", "body_text": BODY, "excerpt": "short", "body_status": "ok", "x_post": None},
    "block": {"title": "BLOCKME celebrity gossip", "body_text": "Gossip about a singer. " * 12, "excerpt": None, "body_status": "ok", "x_post": None},
    "chinese": {"title": "百度發布新模型", "body_text": "百度今天發布了新的大模型，性能提升明顯，" * 20, "excerpt": None, "body_status": "ok", "x_post": None},
    "excerpt": {"title": "Feed summary only", "body_text": None, "excerpt": "A summary that is long enough to be material for the judges, and some more words.", "body_status": "none", "x_post": None},
    "title_only": {"title": "Only a title here", "body_text": None, "excerpt": None, "body_status": "none", "x_post": None},
    "waits": {"title": "Waits for its page", "body_text": None, "excerpt": None, "body_status": "pending", "x_post": None},
    "tweet": {"title": "we shipped", "body_text": None, "excerpt": None, "body_status": "ok", "x_post": {"authorName": "Sam", "handle": "sama", "text": "we shipped a new model today, it is much faster than before #ai", "quoted": None, "media": []}},
    "zh_tweet": {"title": "新模型", "body_text": None, "excerpt": None, "body_status": "ok", "x_post": {"authorName": "A", "handle": "a", "text": "今天我們發布了新的模型，速度更快。", "quoted": None, "media": []}},
    "media_tweet": {"title": "https://t.co/x", "body_text": None, "excerpt": None, "body_status": "ok", "x_post": {"authorName": "A", "handle": "a", "text": "https://t.co/abc", "quoted": None, "media": []}},
    "long_tweet": {"title": "thread", "body_text": None, "excerpt": None, "body_status": "ok", "x_post": {"authorName": "K", "handle": "k", "text": "A long thread about how training works. " * 30, "quoted": {"handle": "z", "text": "quoted words"}, "media": []}},
}


class Fake:
    """Answers each step of the analysis from its request alone, so both engines get the same replies."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.requests.append(body)
                messages = body["messages"]
                user = messages[-1]["content"]
                user = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False)
                digest = int(hashlib.sha1(user.encode()).hexdigest(), 16)
                has_system = messages[0]["role"] == "system"
                tokens, temp = body["max_tokens"], body["temperature"]
                if not has_system:  # the title/summary prompts are one user message
                    content = "title_zh: 譯題 " + str(digest % 100) + "\nsummary_zh: 這是一段摘要，說明了事件的經過和影響，內容足夠長，方便檢查長度規則是否通過，並提到了數字" + str(digest % 7) + "。\nbody_zh: 正文翻譯"
                elif tokens == 512 and temp == 0:  # prefilter
                    content = json.dumps({"label": "BLOCK" if "BLOCKME" in user else "PASS", "reason": "because"})
                elif tokens == 1200:  # structure
                    m = re.search(r"(?:正文|帖子)：\n(.{20})", user, re.S)
                    evidence = m.group(1) if m else None
                    content = json.dumps({"scope": "single", "category": None, "tags": ["模型發布", "openai", "unknown-tag"], "subjects": ["openai", "Nobody"],
                                          "fact": {"title": "發布新模型", "subject": "OpenAI", "action": "發布", "object": "GPT", "evidence": evidence, "conditions": [{"quote": evidence}] if evidence else []}}, ensure_ascii=False)
                elif tokens == 16384:  # understand
                    content = json.dumps({"itemType": "model_release", "authorRole": "principal", "tags": ["x"], "editorialJudgment": "值得關注" if digest % 2 else "", "titleZh": "理解標題 " + str(digest % 100),
                                          "summaryZh": "這是理解步驟寫的摘要。它說明了發布內容，並給出關鍵細節。"}, ensure_ascii=False)
                else:  # score
                    content = json.dumps({"attentionScore": 55 + digest % 40})
                data = json.dumps({"id": "cmpl", "choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
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
    urls = clone_template(admin, database_url, ["an_node", "an_py"])
    admin.close()
    ids = {}
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            c.execute("DELETE FROM receipts")
            rows = c.execute("SELECT a.id FROM articles a JOIN sources s ON s.id = a.source_id ORDER BY a.identity_key LIMIT %s", (len(ARTICLES) + 1,)).fetchall()
            ids[name] = [r["id"] for r in rows]
            for r, (key, a) in zip(rows, ARTICLES.items(), strict=False):
                c.execute(
                    """UPDATE articles SET title = %s, body_text = %s, excerpt = %s, body_status = %s, x_post = %s, media = '[]'::jsonb, revision = 1, processing_state = 'new',
                         published_at = now() - interval '3 hours', publication_time = NULL WHERE id = %s""",
                    (a["title"], a["body_text"], a["excerpt"], a["body_status"], Jsonb(a["x_post"]) if a["x_post"] else None, r["id"]),
                )
            c.execute("DELETE FROM analyses")
    return urls, ids


def snapshot(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        analyses = c.execute(
            """SELECT a.identity_key, an.input_revision, an.origin, an.model, an.prompt_version, an.relevance, an.category, an.tags, an.subjects, an.title_zh, an.summary_zh, an.reason_zh,
                      an.score, an.selected, an.output, (SELECT array_agg(r.logical_key ORDER BY r.logical_key) FROM receipts r WHERE r.id = ANY(an.receipt_ids)) AS receipts
               FROM analyses an JOIN articles a ON a.id = an.article_id ORDER BY a.identity_key, an.id"""
        ).fetchall()
        receipts = c.execute("SELECT logical_key, service, model, purpose, subject, status, request, attempts, usage FROM receipts ORDER BY logical_key").fetchall()
        states = c.execute("SELECT identity_key, processing_state, processing_error FROM articles ORDER BY identity_key").fetchall()
    ids = {}
    for r in receipts:
        r["subject"] = r["subject"]
    return analyses, receipts, states, ids


def test_analysis_of_ten_articles(world, tmp_path, monkeypatch):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine import analyze as A

    urls, ids = world
    node_fake, py_fake = Fake(), Fake()
    env = {**os.environ, **ENV, "DATABASE_URL": urls["an_node"], "TZ": "UTC", "MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": node_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}
    out = subprocess.run(["node", "scripts/analyze-run.ts", *ids["an_node"][: len(ARTICLES)]], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=600)
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(k, v)

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    py = []
    with ConnectionPool(urls["an_py"], min_size=1, max_size=6, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for article_id in ids["an_py"][: len(ARTICLES)]:
            try:
                r = A.analyze_article(pool, article_id)
                py.append(r and {"stale": r["stale"], "needsBody": r.get("needsBody", False), "reused": r["reused"], "output": r["output"], "receipts": len(r["receiptIds"])})
            except Exception as e:
                from newsroom.engine.receipts import failure_kind

                py.append({"error": failure_kind(e), "name": type(e).__name__})
    for (name, _), n, p in zip(ARTICLES.items(), node, py, strict=True):
        assert p == n, f"{name}: {json.dumps(p, ensure_ascii=False)[:600]}\n  vs {json.dumps(n, ensure_ascii=False)[:600]}"
    # the prompts sent
    key = lambda body: json.dumps(body, sort_keys=True, ensure_ascii=False)  # noqa: E731
    assert sorted(map(key, py_fake.requests)) == sorted(map(key, node_fake.requests)) and len(node_fake.requests) > 20
    na, nb = snapshot(urls["an_node"]), snapshot(urls["an_py"])
    assert nb[0] == na[0] and na[0]
    assert nb[1] == na[1]
    assert nb[2] == na[2]
    # every kind of judgement happened
    outputs = [r["output"] for r in node if r and r.get("output")]
    assert any(o["relevance"] == "block" for o in outputs) and any(o["selected"] for o in outputs) and any(not o["selected"] and o["relevance"] == "pass" for o in outputs)
    assert any(r.get("needsBody") for r in node) and any(o["relevance"] == "unknown" for o in outputs)
