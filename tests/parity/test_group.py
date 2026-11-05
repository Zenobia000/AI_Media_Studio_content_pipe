"""Event grouping: the same reports handled by the TypeScript engine and by the Python port - against fake chat and embedding servers, in their own copies of one
database - must end with the same verdicts, facts, stories, heat evidence, decisions, receipts and queued work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

DIMS = 64
AT = datetime.now(UTC).replace(microsecond=0, second=0) - timedelta(hours=2)  # the same source time for every report, in both copies


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9_]+", text)


def embed(text: str) -> list[float]:
    v = [0.0] * DIMS
    for w in words(text):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIMS] += 1.0
    return v


class Fake:
    """A chat model that answers the grouping prompts from marker words in the reports, and an embeddings endpoint over bags of words."""

    def __init__(self, systems: dict[str, str]):
        self.chat: list[str] = []
        self.embeddings: list[list[str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, body: dict):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.path.endswith("/embeddings"):
                    outer.embeddings.append(body["input"])
                    self.reply({"data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(body["input"])], "usage": {"prompt_tokens": 1, "total_tokens": 1}})
                    return
                system, user = body["messages"][0]["content"], body["messages"][-1]["content"]
                outer.chat.append(user)
                if system == systems["pair"]:
                    a = re.search(r"【報道 A】[^\n]*\n標題：([^\n]*)", user)
                    b = re.search(r"【報道 B】[^\n]*\n標題：([^\n]*)", user)
                    both = f"{a.group(1) if a else ''} {b.group(1) if b else ''}"
                    relation = "UNRELATED" if "REVIEWNO" in both else "SAME_STORY" if "REVIEWSTORY" in both else "SAME_OCCURRENCE"
                    content = {"a": "a", "b": "b", "relation": relation, "difference": "差異", "confidence": 0.9}
                else:
                    query = re.search(r"【(?:新報道|帖子)】[^\n]*\n標題：([^\n]*)", user)
                    q = query.group(1) if query else ""
                    ids = re.findall(r"【候選 (C\d+)】", user)
                    decisions = []
                    for i, cid in enumerate(ids):
                        if "SAMEFACT" in q and i == 0 or "TIEBOTH" in q and i == 0:
                            rel, conf = "SAME_OCCURRENCE", 0.95
                        elif "SAMESTORY" in q and i == 0 or "TIEBOTH" in q and i == 1:
                            rel, conf = "SAME_STORY", 0.9
                        else:
                            rel, conf = "UNRELATED", 0.9
                        decisions.append({"id": cid, "relation": rel, "confidence": conf, "note": "n"})
                    if system == systems["batch"]:
                        content = {"query": "q", "decisions": decisions, "selection": {"addsValue": "NOVALUE" not in q, "reason": "判斷"}}
                    else:
                        content = {"decisions": [{k: v for k, v in d.items() if k != "note"} for d in decisions]}
                self.reply({"id": "c", "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"


def fact_words(n: int, shared: int, marker: str, total: int = 10, extra: str = "") -> str:
    """A text sharing `shared` of fact n's ten words, with a marker and fillers."""
    parts = [f"f{n}_{i}" for i in range(1, shared + 1)] + [marker] + [f"fill{n}{marker}{i}" for i in range(total - shared - 1)]
    return " ".join(parts) + (" " + extra if extra else "")


# (key, mode, scenario) in the order they are grouped
KEYS = ["hi", "mid", "reviewno", "samestory", "tie", "fresh", "sameurl", "composite", "unsupported", "notpass", "kept", "manual", "historical", "stale",
        "sig_native", "sig_auto", "sig_judged", "sig_none"]  # fmt: skip


@pytest.fixture(scope="module", params=["embeddings", "lexical"])
def world(request, database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    tags = [f"gr_node_{request.param}", f"gr_py_{request.param}"]
    made = clone_template(admin, database_url, tags)
    urls = {"gr_node": made[tags[0]], "gr_py": made[tags[1]], "mode": request.param}
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    ids = {}
    for name, url in ((n, u) for n, u in urls.items() if n != "mode"):
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            if name == "gr_py":
                c.execute(migration.read_text(encoding="utf-8"))
            c.execute("DELETE FROM receipts")
            c.execute("DELETE FROM embeddings")
            c.execute("DELETE FROM grouping_decisions")
            c.execute("INSERT INTO sources (id, name, kind, config, participation_mode, tier) VALUES ('gr-sig', 'Signal', 'rss', '{}', 'hot_signal', 'T2') ON CONFLICT (id) DO NOTHING")
            # the facts the new reports are compared with: ten words each
            facts = c.execute("SELECT f.id, f.story_id, min(fa.article_id) AS article_id FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id GROUP BY f.id ORDER BY f.id").fetchall()
            for n, f in enumerate(facts, 1):
                text = [f"f{n}_{i}" for i in range(1, 11)]
                c.execute("UPDATE facts SET title = %s WHERE id = %s", (f"事實 {n}", f["id"]))
                c.execute("UPDATE publications SET title = %s, summary = %s WHERE article_id = %s", (" ".join(text[:3]), " ".join(text[3:]), f["article_id"]))
                c.execute("UPDATE articles SET url = %s, identity_key = %s WHERE id = %s", (f"https://example.org/fact/{n}", f"x:{500 + n}" if n == 2 else f"fact:{n}", f["article_id"]))
            fresh = c.execute("SELECT a.id FROM articles a WHERE NOT EXISTS (SELECT 1 FROM fact_articles fa WHERE fa.article_id = a.id) ORDER BY a.identity_key LIMIT %s", (len(KEYS),)).fetchall()
            ids[name] = {}
            for n, (key, r) in enumerate(zip(KEYS, fresh, strict=True)):
                ids[name].setdefault(key, []).append(r["id"])
                scope, relevance, text, extra = "single", "pass", "", {}
                frame = {"title": f"事實標題 {key}", "subject": "OpenAI", "action": "發布", "object": key, "evidence": "原文證據", "conditions": [{"text": "條件", "quote": "引文"}]}
                state, mode = "analyzed", "editorial"
                if key == "hi":
                    text = fact_words(3, 9, "SAMEFACT")
                elif key == "mid":
                    text = fact_words(5, 7, "SAMEFACT")
                elif key == "reviewno":
                    text = fact_words(7, 7, "SAMEFACT REVIEWNO", total=11)
                elif key == "samestory":
                    text = fact_words(9, 7, "SAMESTORY")
                elif key == "tie":
                    text = " ".join([f"f1_{i}" for i in range(1, 9)] + [f"f10_{i}" for i in range(1, 9)] + ["TIEBOTH"])
                elif key == "fresh":
                    text = " ".join(f"brandnew{i}" for i in range(10))
                elif key == "sameurl":
                    text = " ".join(f"other{i}" for i in range(10))
                    extra["url"] = "https://example.org/fact/4"
                elif key == "composite":
                    text, scope = fact_words(6, 7, "SAMEFACT"), "composite"
                elif key == "unsupported":
                    text, scope, frame = "nothing certain here", "unknown", None
                elif key == "notpass":
                    text, relevance = "weak material", "block"
                elif key == "kept":
                    text = fact_words(11, 9, "SAMEFACT")
                elif key == "manual":
                    text = "manually placed words"
                elif key == "historical":
                    text, extra["backfill"] = "old news words", True
                elif key == "stale":
                    text = fact_words(12, 9, "NOVALUE SAMEFACT")
                elif key.startswith("sig_"):
                    state, mode = "skipped", "hot_signal"
                    text = {"sig_native": "a reply to a post", "sig_auto": " ".join(f"f4_{i}" for i in range(1, 11)), "sig_judged": fact_words(8, 8, "SAMEFACT", total=12), "sig_none": "unrelated chatter words"}[key]
                source = "gr-sig" if mode == "hot_signal" else c.execute("SELECT source_id FROM articles WHERE id = %s", (r["id"],)).fetchone()["source_id"]
                x_post = {"tweetId": f"900{n}", "replyTo": "502"} if key == "sig_native" else None
                c.execute(
                    """UPDATE articles SET title = %s, body_text = '', url = coalesce(%s, 'https://example.org/new/' || %s), revision = 1, processing_state = %s, grouping_status = 'pending',
                         grouped_at = NULL, selection_adds_value = NULL, backfill = %s, published_at = %s, discovered_at = %s,
                         source_id = %s, x_post = %s, identity_key = %s WHERE id = %s""",
                    (text if mode == "hot_signal" else f"original {key}", extra.get("url"), f"{key}{n}", state, extra.get("backfill", False), AT - timedelta(days=30) if key == "historical" else AT, AT, source, Jsonb(x_post) if x_post else None, f"new:{key}{n}", r["id"]),
                )
                c.execute("DELETE FROM analyses WHERE article_id = %s", (r["id"],))
                if mode == "editorial":
                    c.execute(
                        """INSERT INTO analyses (article_id, input_revision, origin, relevance, title_zh, summary_zh, output, model, prompt_version)
                           VALUES (%s, 1, 'model', %s, %s, '', %s, 'fake', 'v')""",
                        (r["id"], relevance, text, Jsonb({"scope": scope, "fact": frame})),
                    )
                if key == "manual":
                    c.execute("INSERT INTO fact_articles (fact_id, article_id, role, manual) VALUES (%s, %s, 'report', true)", (facts[0]["id"], r["id"]))
    return urls, ids


def dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        jobs = []
        for table in ("pgboss.job", "jobs"):
            try:
                jobs = c.execute(f"SELECT name, data, priority, singleton_key FROM {table} WHERE name IN ('events.group', 'events.digest')").fetchall()
                break
            except psycopg.errors.UndefinedTable:
                c.rollback()
        return {
            "articles": c.execute("SELECT identity_key, grouping_status, grouping_error IS NOT NULL AS failed, grouped_at IS NOT NULL AS grouped, selection_adds_value, selection_value_reason FROM articles ORDER BY identity_key").fetchall(),
            "facts": c.execute("SELECT id, story_id, title, subject, action, object, conditions, occurred_at, manual FROM facts ORDER BY id").fetchall(),
            "stories": c.execute("SELECT id, title, origin, merged_into, version, first_report_at, latest_at FROM stories ORDER BY id").fetchall(),
            "members": c.execute("SELECT fa.fact_id, a.identity_key, fa.role, fa.evidence, fa.manual FROM fact_articles fa JOIN articles a ON a.id = fa.article_id ORDER BY fa.fact_id, a.identity_key").fetchall(),
            "signals": c.execute("SELECT s.story_id, a.identity_key, s.participant_key, s.kind FROM story_signals s JOIN articles a ON a.id = s.article_id ORDER BY s.story_id, a.identity_key").fetchall(),
            "decisions": c.execute("SELECT a.identity_key, d.fact_id, d.story_id, d.verdict, d.candidates, d.receipt_id IS NOT NULL AS paid FROM grouping_decisions d JOIN articles a ON a.id = d.article_id ORDER BY d.id").fetchall(),
            "aliases": c.execute("SELECT story_id FROM story_aliases ORDER BY story_id").fetchall(),
            "links": c.execute("SELECT story_id, other_id, relation FROM story_links ORDER BY 1, 2").fetchall(),
            "receipts": c.execute("SELECT service, purpose, status, attempts FROM receipts ORDER BY purpose, status, attempts").fetchall(),
            "jobs": sorted((j["name"], j["priority"], j["singleton_key"], json.dumps(j["data"], sort_keys=True)) for j in jobs),
        }


def test_grouping_reports_and_posts(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.events.consolidate import link_related_stories
    from newsroom.engine.events.group import group_article
    from newsroom.engine.events.relate import batch_system, pair_system, signal_system
    from newsroom.engine.models import invalidate_model_cache
    from newsroom.engine.receipts import failure_kind

    urls, ids = world
    systems = {"batch": batch_system(), "pair": pair_system(), "signal": signal_system()}
    node_fake, py_fake = Fake(systems), Fake(systems)
    lexical = urls["mode"] == "lexical"  # without an embedding key, recall compares the texts themselves
    base = {"MODEL_CALLS_ENABLED": "true", "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake", **({} if lexical else {"EMBEDDING_API_KEY": "embed-key", "EMBEDDING_MODEL": "fake-embed", "EMBEDDING_DIMS": "0"})}
    for k in ("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY", "EMBEDDING_MODEL", "EMBEDDING_DIMS", "EMBEDDING_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**base, "LLM_BASE_URL": py_fake.url, **({} if lexical else {"EMBEDDING_BASE_URL": py_fake.url})}.items():
        monkeypatch.setenv(k, v)
    invalidate_model_cache()

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    plan: list[dict] = []
    for key in KEYS:
        pass
    seen: dict[str, int] = {}
    for key in KEYS:
        i = seen.get(key, 0)
        seen[key] = i + 1
        plan.append({"key": key, "idx": i, "signalOnly": key.startswith("sig_"), "regroup": False})
    plan.insert(len(plan) - 4, {"key": "kept", "idx": 0, "signalOnly": False, "regroup": False})  # the same report again: its membership is kept
    plan.append({"links": True})

    def run_node(specs):
        path = tmp_path / "in.json"
        path.write_text(json.dumps([{**({"id": ids["gr_node"][s["key"]][s["idx"]]} if "key" in s else {}), **{k: v for k, v in s.items() if k not in ("key", "idx")}} for s in specs]), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY", "EMBEDDING_MODEL", "EMBEDDING_DIMS", "EMBEDDING_BASE_URL")}
        env = {**env, **ENV, **base, "DATABASE_URL": urls["gr_node"], "TZ": "UTC", "LLM_BASE_URL": node_fake.url, **({} if lexical else {"EMBEDDING_BASE_URL": node_fake.url}), "AIHOT_DATA_DIR": str(tmp_path / "node-data")}
        out = subprocess.run(["node", "scripts/group-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=900)
        return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    def run_py(pool, specs):
        out = []
        for s in specs:
            try:
                if s.get("links"):
                    out.append(link_related_stories(pool))
                else:
                    out.append(group_article(pool, ids["gr_py"][s["key"]][s["idx"]], signal_only=s["signalOnly"], regroup=s["regroup"]))
            except Exception as error:
                out.append({"error": failure_kind(error), "name": type(error).__name__})
        return out

    def normalise(results, side):
        """Fact and story ids are the same in both copies; article-independent."""
        return results

    with ConnectionPool(urls["gr_py"], min_size=1, max_size=6, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        node = run_node(plan)
        py = run_py(pool, plan)
        for spec, n, p in zip(plan, normalise(node, "n"), normalise(py, "p"), strict=True):
            assert p == n, (spec, n, p)
        a, b = dump(urls["gr_node"]), dump(urls["gr_py"])
        for part in a:
            assert b[part] == a[part], f"{part} differ:\n py   {json.dumps(b[part], ensure_ascii=False, default=str)[:2500]}\n node {json.dumps(a[part], ensure_ascii=False, default=str)[:2500]}"
        verdicts = {r.get("verdict") for r in py}
        print(sorted(v for v in verdicts if v))
        assert {"new-story", "standalone", "kept", "roundup", "manual", "historical"} <= verdicts, verdicts
        if not lexical:
            assert {"same-fact", "signal", "signal-native", "same-url"} <= verdicts, verdicts
            assert any("consolidated" in r for r in py), py
        assert sorted(py_fake.chat) == sorted(node_fake.chat)
        assert sorted(map(tuple, py_fake.embeddings)) == sorted(map(tuple, node_fake.embeddings))
        assert bool(py_fake.embeddings) == (not lexical)
