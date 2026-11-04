"""The analysis job (processArticle): the same articles handled by the TypeScript engine and by the Python port, against fake model servers that sometimes fail, in their own
copies of one database, must end in the same states, with the same retries, publications and queued work."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_analyze import BODY, Fake
from tests.parity.test_publish import _admin

LONG = BODY
CASES = [  # (key, title, body, body_status, mode)
    ("pass", "OpenAI releases GPT-5.5 for everyone", LONG, "ok", "editorial"),
    ("block", "BLOCKME gossip", "Gossip. " * 40, "ok", "editorial"),
    ("page", "Waits for its page", None, "pending", "editorial"),
    ("signal", "A hot signal post about GPT", LONG, "ok", "hot_signal"),
    ("isolated", "An isolated post", LONG, "ok", "isolated"),
    ("e429", "FAIL429 throttled", LONG, "ok", "editorial"),
    ("bad", "BADJSON answers", LONG, "ok", "editorial"),
    ("bad", None, None, None, None),  # the second and third attempts of the same article
    ("bad", None, None, None, None),
    ("close", "CLOSE the connection", LONG, "ok", "editorial"),
    ("e402", "FAIL402 payment", LONG, "ok", "editorial"),
    ("after", "OpenAI after the stop", LONG, "ok", "editorial"),  # the model is stopped by the refusal: it waits
    ("gone", None, None, None, None),
]


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["pr_node", "pr_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    ids = {}
    keys = [c[0] for c in CASES if c[1]]
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            if name == "pr_py":
                c.execute(migration.read_text(encoding="utf-8"))
            c.execute("DELETE FROM receipts")
            c.execute("DELETE FROM analyses")
            rows = c.execute("SELECT a.id, a.source_id FROM articles a ORDER BY a.identity_key LIMIT %s", (len(keys),)).fetchall()
            ids[name] = dict(zip(keys, [r["id"] for r in rows], strict=True))
            for r, (key, title, body, status, mode) in zip(rows, [c_ for c_ in CASES if c_[1]], strict=True):
                c.execute(
                    """UPDATE articles SET title = %s, body_text = %s, excerpt = NULL, body_status = %s, x_post = NULL, media = '[]'::jsonb, revision = 1, processing_state = 'new',
                         processing_attempts = 0, processing_error = NULL, published_at = now() - interval '3 hours', publication_time = NULL, url = %s WHERE id = %s""",
                    (title, body, status, f"https://example.org/p/{key}", r["id"]),
                )
                c.execute(
                    "INSERT INTO sources (id, name, kind, config, participation_mode, tier) VALUES (%s, %s, 'rss', '{}', %s, 'T2') ON CONFLICT (id) DO NOTHING", (f"pr-{mode}", f"pr-{mode}", mode)
                )
                c.execute("UPDATE articles SET source_id = %s WHERE id = %s", (f"pr-{mode}", r["id"]))
    return urls, ids


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        articles = c.execute(
            """SELECT identity_key, processing_state, processing_attempts, left(processing_error, 60) AS error, processing_retry_at IS NOT NULL AS retry, processing_queued_at IS NOT NULL AS queued
               FROM articles ORDER BY identity_key"""
        ).fetchall()
        analyses = c.execute("SELECT a.identity_key, an.relevance, an.selected, an.score, an.title_zh, an.summary_zh FROM analyses an JOIN articles a ON a.id = an.article_id ORDER BY a.identity_key, an.id").fetchall()
        pubs = c.execute("SELECT article_id, selected, visibility FROM publications p JOIN articles a ON a.id = p.article_id ORDER BY a.identity_key").fetchall()
        ids = {r["id"]: r["identity_key"] for r in c.execute("SELECT id, identity_key FROM articles").fetchall()}
        try:
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM pgboss.job").fetchall()
        except psycopg.errors.UndefinedTable:
            c.rollback()
            jobs = c.execute("SELECT name, data, priority, singleton_key FROM jobs").fetchall()
        receipts = c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall()
    queue = sorted((j["name"], j["priority"], ids.get(j["data"].get("articleId"), "?"), bool(j["data"].get("signalOnly"))) for j in jobs if j["name"].startswith(("events.", "content.")))
    return articles, analyses, [{**p, "article_id": ids[p["article_id"]]} for p in pubs], queue, receipts


def test_states_retries_and_queued_work(world, monkeypatch):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.jobs.content import process_article

    urls, ids = world
    order = [c[0] for c in CASES]
    node_fake, py_fake = Fake(), Fake()
    env = {**os.environ, **ENV, "DATABASE_URL": urls["pr_node"], "TZ": "UTC", "MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": node_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}
    targets = [ids["pr_node"].get(k, "no-such-article") for k in order]
    out = subprocess.run(["node", "scripts/process-run.ts", *targets], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": py_fake.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(k, v)

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    py = []
    with ConnectionPool(urls["pr_py"], min_size=1, max_size=6, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for key in order:
            r = process_article(pool, ids["pr_py"].get(key, "no-such-article"))
            retry = r.get("retryAt")
            from datetime import UTC, datetime

            py.append({"state": r["state"], "retryInMinutes": None if retry is None else round((retry - datetime.now(UTC)).total_seconds() / 60)})
    for key, n, p in zip(order, node, py, strict=True):
        assert p == n, (key, n, p)
    states = [n["state"] for n in node]
    assert {"block", "fetching-body", "retrying", "failed", "missing", "skipped", "unknown-receipt", "waiting"} <= set(states), states
    print(states)
    na, nb = dump(urls["pr_node"]), dump(urls["pr_py"])
    for label, x, y in zip(("articles", "analyses", "publications", "queue", "receipts"), nb, na, strict=True):
        assert x == y, f"{label} differ:\n py   {json.dumps(x, ensure_ascii=False, default=str)[:900]}\n node {json.dumps(y, ensure_ascii=False, default=str)[:900]}"
    assert Jsonb
