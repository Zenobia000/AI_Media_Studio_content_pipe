"""Sources that are paid per request - X searches (SocialData), WeChat accounts (Dajiala) and pages read through Jina: the same collections, checks and extractions run by the
TypeScript engine and by the Python port, each on its own copy of one database, against local stand-ins for the three services, must send the same requests, store the same
material and leave the same receipts, cursors and fetch runs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

BASE_ID = 1_900_000_000_000_000_000
PAGE = "<html><head><title>An article</title></head><body><article><h1>An article about models</h1>" + "<p>" + "The body of the article says a number of things in some detail. " * 12 + "</p></article></body></html>"


def tweet(handle: str, n: int, **extra) -> dict:
    return {
        "id_str": str(BASE_ID + n), "tweet_created_at": f"2026-11-04T0{n % 10}:00:00.000000Z", "full_text": f"Post {n} by {handle} &amp; friends <b> https://t.co/abc{n}", "lang": "en",
        "user": {"name": handle.title(), "screen_name": handle, "profile_image_url_https": f"https://pbs.example/{handle}_normal.jpg"},
        "entities": {"urls": [{"url": f"https://t.co/abc{n}", "expanded_url": f"https://example.org/p/{n}"}]},
        "favorite_count": n % 1000, "retweet_count": 1, "views_count": 100 * (n % 1000), **extra,
    }  # fmt: skip


class Services:
    """SocialData, Jina and Dajiala in one local server, recording what each is asked."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def send(self, status: int, body, ctype="application/json", headers=None):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                parts = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                path = unquote(parts.path)
                if path.startswith("/sd/twitter/search"):
                    return self.search(q)
                if path.startswith("/sd/twitter/tweets/"):
                    tid = path.rsplit("/", 1)[1]
                    if tid == "404":
                        return self.send(404, {"status": "error"})
                    if tid == "403":
                        return self.send(403, {"status": "error", "message": "Forbidden: Requested resource cannot be viewed due to user's privacy settings"})
                    return self.send(200, tweet("alice_renamed", int(tid) - BASE_ID if tid.isdigit() else 1))
                if path.startswith("/sd/twitter/article/"):
                    tid = path.rsplit("/", 1)[1]
                    if tid.endswith("77"):
                        blocks = [{"type": "header-two", "text": "Heading"}, {"type": "unstyled", "text": "First paragraph of the article."}, {"type": "atomic", "text": "x"}, {"type": "blockquote", "text": "A quote"}, {"type": "unordered-list-item", "text": "item"}]
                        return self.send(200, {"article": {"title": "The Long Read", "content_state": {"blocks": blocks}}})
                    return self.send(200, {"status": "error"})
                if path.startswith("/jina/"):
                    return self.jina(path[len("/jina/") :], dict(self.headers))
                if path.startswith("/dj/fbmain/monitor/v3/article_detail"):
                    if "nobody" in q.get("url", ""):
                        return self.send(200, {"code": 101, "msg": "account gone"})
                    return self.send(200, {"code": 0, "title": "Body title", "content": f"<p>{'WeChat body text of the post. ' * 6}</p><img src=\"https://mmbiz.example/a.jpg\">", "author": "Author", "desc": "Desc", "cost_money": 0.03})
                self.send(404, "no")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
                outer.requests.append(("POST", self.path + " " + raw))
                if self.path.startswith("/dj/fbmain/monitor/v3/post_history"):
                    ghid = json.loads(raw)["ghid"]
                    if ghid == "gh_rate":
                        return self.send(200, {"code": -1, "msg": "slow"})
                    if ghid == "gh_broke":
                        return self.send(200, {"code": 20001, "msg": "no balance"})
                    now = int(datetime(2026, 11, 4, tzinfo=UTC).timestamp())
                    posts = [
                        {"position": 1, "url": f"https://mp.weixin.qq.com/s?__biz=B{ghid}&mid=1&idx=1&sn=a", "title": "Newest post", "post_time": now - 3600, "digest": "d1", "sn": "a", "original": 1},
                        {"position": 2, "url": f"https://mp.weixin.qq.com/s?__biz=B{ghid}&mid=1&idx=2&sn=b&nobody=1", "title": "Post without a body", "post_time": now - 7200, "sn": "b"},
                        {"position": 3, "url": f"https://mp.weixin.qq.com/s?__biz=B{ghid}&mid=0&idx=1&sn=c", "title": "An old post", "post_time": now - 40 * 86400, "sn": "c"},
                    ]
                    return self.send(200, {"code": 0, "data": posts, "nickname": "Account", "remain_money": 12.5, "cost_money": 0.14})
                self.send(404, "no")

            def search(self, q):
                query, cursor = q["query"], q.get("cursor")
                handles = re.findall(r"from:([A-Za-z0-9_]+)", query)
                since = re.search(r"since_id:(\d+)", query)
                if cursor == "boom":
                    return self.send(500, {"status": "error", "message": "boom"})
                if "bad request" in query:
                    return self.send(400, {"status": "error", "message": "bad"})
                base = int(since.group(1)) if since else BASE_ID
                tweets = []
                for i, h in enumerate(handles or ["anyone"]):
                    n = (base - BASE_ID) + 10 * (i + 1)
                    tweets += [tweet(h, n + 1), tweet(h, n + 2, quoted_status=tweet("quoted", n + 3), extended_entities={"media": [{"type": "photo", "media_url_https": "https://pbs.example/m.jpg", "original_info": {"width": 10, "height": 20}}]})]
                if handles:
                    tweets.append({**tweet(handles[0], base - BASE_ID + 9), "retweeted_status": tweet("someone", 5)})
                if cursor is None and since:
                    return self.send(200, {"tweets": tweets[:3], "next_cursor": "p2"})
                if cursor == "p2":
                    return self.send(200, {"tweets": tweets[2:], "next_cursor": "boom" if "A_FAIL_PAGE" in query else None})
                self.send(200, {"tweets": tweets, "next_cursor": None})

            def jina(self, target, headers):
                if "missing" in target:
                    return self.send(200, "Title: Not found\nURL Source: x\nWarning: Target URL returned error 404: Not Found\nMarkdown Content:\nnothing", "text/plain")
                if target.endswith("/list") or target.endswith("/list2"):
                    md = "\n".join(f"- [A listed post number {i} with a long title](https://example.org/posts/{i})" for i in range(1, 5))
                    return self.send(200, f"Title: Posts\nURL Source: {target}\nMarkdown Content:\n{md}\n", "text/plain", {"x-usage-tokens": "1500"})
                if "/posts/" in target:
                    return self.send(200, f"Title: Post\nURL Source: {target}\nPublished Time: 2026-10-30T08:00:00Z\nMarkdown Content:\n# A post\n\nPublished Time: 2026-10-30T08:00:00Z\n\n{'Some text. ' * 5}", "text/plain")
                body = "# The page\n\n" + "A paragraph of text that is long enough to be a body. " * 10 + "\n\n- one\n- two\n\n[a link](https://example.org/x)\n"
                return self.send(200, f"Title: The page\nURL Source: {target}\nMarkdown Content:\n{body}", "text/plain", {"x-usage-tokens": "800"})

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def env(self) -> dict:
        return {
            "SOCIALDATA_API_KEY": "sd-key", "SOCIALDATA_BASE_URL": f"{self.url}/sd", "JINA_API_KEY": "jina-key", "JINA_BASE_URL": f"{self.url}/jina", "DAJIALA_KEY": "dj-key",
            "DAJIALA_BASE_URL": f"{self.url}/dj", "COLLECT_ENABLED": "true",
        }  # fmt: skip


def node(tmp_path, calls, env, db):
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"calls": calls}), encoding="utf-8")
    full = {**os.environ, **ENV, **env, "DATABASE_URL": db, "TZ": "UTC", "AIHOT_DATA_DIR": str(tmp_path / "node-data")}
    out = subprocess.run(["node", "scripts/paid-run.ts", str(path)], cwd=AIHOT, env=full, check=True, capture_output=True, timeout=900)
    return json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))


def test_pure_parts(settings_env, tmp_path):
    from newsroom.engine.socialdata import tweet_text
    from newsroom.engine.sources.x import plan_x_shards, tweet_to_candidate, x_article_text

    tweets = [tweet("alice", 1), tweet("bob", 2, quoted_status=tweet("carol", 3)), {**tweet("dan", 4), "full_text": "&amp;amp; &lt;tag&gt; https://t.co/zz", "entities": {"media": [{"type": "photo", "media_url_https": "u"}]}},
              {**tweet("eve", 5), "full_text": "x" * 200 + "\nsecond"}, {**tweet("fay", 6), "full_text": "\n\nSecond line wins https://x.com/i/article/12345"}]  # fmt: skip
    sources = [{"id": f"s{i:02d}", "kind": "x_search", "participation_mode": "editorial" if i % 3 else "hot_signal", "config": {"query": f"from:acct{i} -filter:replies", "publisherRole": "organization" if i % 4 == 0 else None}, "cursor": {"lastTweetId": "5"}} for i in range(60)]
    sources += [{"id": "plain", "kind": "x_search", "participation_mode": "editorial", "config": {"query": "ai agents"}, "cursor": {"lastTweetId": "5"}}, {"id": "nocursor", "kind": "x_search", "participation_mode": "editorial", "config": {"query": "from:z -filter:replies"}, "cursor": None}]
    articles = [{"title": "T", "content_state": {"blocks": [{"type": "header-one", "text": " H "}, {"type": "atomic", "text": "m"}, {"type": "x", "text": "plain"}, {"text": ""}]}}, {"title": "Only media", "content_state": {"blocks": [{"type": "atomic", "text": "m"}]}}, {}]
    calls = [["tweetText", t] for t in tweets] + [["tweetToCandidate", t] for t in tweets] + [["planXShards", sources]] + [["xArticleText", a] for a in articles]
    want = node(tmp_path, calls, {}, "postgres://x")
    got = [tweet_text(t) for t in tweets]
    for c in (tweet_to_candidate(t) for t in tweets):
        c = {**c, "publishedAt": c.pop("published_at").isoformat().replace("+00:00", ".000Z")}
        got.append({"url": c["url"], "identityKey": c["identity_key"], "title": c["title"], "author": c["author"], "language": c["language"], "publishedAt": c["publishedAt"], "bodyText": c["body_text"], "bodyStatus": c["body_status"], "xPost": c["x_post"], "media": c["media"], "raw": c["raw"]})  # fmt: skip
    got.append(plan_x_shards(sources))
    got += [x_article_text(a) for a in articles]
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, (i, calls[i][0], g, w)


@pytest.fixture(scope="module")
def world(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["pd_node", "pd_py"])
    admin.close()
    from pathlib import Path

    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    for name, url in urls.items():
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as c:
            if name == "pd_py":
                c.execute(migration.read_text(encoding="utf-8"))
            for t in ("receipt_attempts", "receipts", "fetch_runs"):
                c.execute(f"DELETE FROM {t}")
            c.execute("UPDATE budgets SET per_minute = 1000, per_hour = 10000, per_day = 100000")
            ins = "INSERT INTO sources (id, name, kind, config, cursor, participation_mode, tier, interval_minutes, enabled) VALUES (%s, %s, %s, %s, %s, 'editorial', 'T2', 60, true)"
            mark = {"lastTweetId": str(BASE_ID), "initializedAt": "2026-11-01T00:00:00.000Z", "lastOkAt": "2026-11-04T00:00:00.000Z"}
            for sid, handle in (("x-alice", "alice"), ("x-bob", "bob"), ("x-carol", "carol")):
                c.execute(ins, (sid, f"{handle} on X", "x_search", Jsonb({"query": f"from:{handle} -filter:replies", "publisherRole": "organization" if handle == "bob" else None}), Jsonb(mark)))
            c.execute(ins, ("x-topic", "AI topic", "x_search", Jsonb({"query": "ai agents lang:en"}), None))
            c.execute(ins, ("x-first", "First fetch", "x_search", Jsonb({"query": "from:newcomer -filter:replies"}), None))
            c.execute(ins, ("x-pages", "Long search", "x_search", Jsonb({"query": "from:pager A_FAIL_PAGE -filter:replies"}), Jsonb({"lastTweetId": str(BASE_ID), "xBacklog": [{"query": "from:pager -filter:replies since_id:5", "next": "boom"}, {"query": "bad request", "next": "p9"}]})))
            c.execute(ins, ("wx-one", "A WeChat account", "mp_account", Jsonb({"ghid": "gh_one"}), None))
            c.execute(ins, ("wx-rate", "Rate limited", "mp_account", Jsonb({"ghid": "gh_rate"}), None))
            c.execute(ins, ("wx-broke", "Out of money", "mp_account", Jsonb({"ghid": "gh_broke"}), None))
            c.execute(ins, ("jn-list", "Through Jina", "web_list", Jsonb({"url": "https://r.jina.ai/https://example.org/list", "detail": {"publishedAtRegex": "Published Time: (\\S+)", "maxFetches": 3}}), None))
            c.execute(ins, ("jn-list2", "Through Jina plain", "web_list", Jsonb({"url": "https://r.jina.ai/https://example.org/list2"}), None))
    return urls


def _times(value):
    """Instants of the run itself (the cursor's lastOkAt and the like) are the clock's."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if isinstance(value, str):
        return "T" if value.startswith(today) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+Z", value) else value
    if isinstance(value, list):
        return [_times(v) for v in value]
    if isinstance(value, dict):
        return {k: _times(v) for k, v in value.items()}
    return value


def dump(url: str):
    return _times(_dump(url))


def _dump(url: str):
    with psycopg.connect(url, row_factory=dict_row) as c:
        return {
            "sources": c.execute("SELECT id, name, config, cursor, fail_count, health, last_error FROM sources ORDER BY id").fetchall(),
            "articles": c.execute("SELECT identity_key, source_id, title, author, language, published_at, body_text, body_status, x_post, x_article, media, raw, revision, excerpt FROM articles WHERE source_id LIKE ANY(ARRAY['x-%%', 'wx-%%', 'jn-%%']) ORDER BY identity_key").fetchall(),
            "runs": c.execute("SELECT source_id, status, found_count, new_count, error, detail FROM fetch_runs ORDER BY source_id, id").fetchall(),
            "receipts": c.execute("SELECT service, purpose, regexp_replace(subject, 'article:.*', 'article:*') AS subject, status, usage, cost, currency, cost_basis, attempts FROM receipts ORDER BY service, purpose, 3, usage::text, cost").fetchall(),
            "audit": c.execute("SELECT actor, action, subject, reason FROM audit_log WHERE action = 'source.update' ORDER BY id").fetchall(),
        }


def test_collections_checks_and_extractions(world, monkeypatch, tmp_path):
    from psycopg_pool import ConnectionPool

    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.extract import extract_article_body
    from newsroom.engine.models import invalidate_model_cache
    from newsroom.engine.sources.collect import collect_source, collect_x_shard
    from newsroom.engine.sources.mp import check_mp_account, schedule_mp_reconcile
    from newsroom.engine.sources.x_renames import follow_x_renames

    node_svc, py_svc = Services(), Services()
    invalidate_model_cache()
    calls = [
        ["collectXShard", "editorial:k", ["x-alice", "x-bob", "x-carol"]], ["collectXShard", "editorial:none", ["x-topic"]],  # before any other read moves their check times
        ["collectSource", "x-first", False, None], ["collectSource", "x-topic", False, None], ["collectSource", "x-alice", False, None], ["collectSource", "x-alice", False, "1900000000000000005"],
        ["collectSource", "x-pages", False, None],
        ["checkMpAccount", "wx-one", "schedule"], ["checkMpAccount", "wx-one", "manual"], ["checkMpAccount", "wx-rate", "schedule"], ["checkMpAccount", "wx-broke", "schedule"], ["checkMpAccount", "missing", "schedule"],
        ["scheduleMpReconcile", "2026-11-05T05:00:00Z"],
        ["collectSource", "jn-list", False, None], ["collectSource", "jn-list2", False, None],
    ]  # fmt: skip
    want = node(tmp_path, calls, node_svc.env(), world["pd_node"])
    for k, v in {**py_svc.env(), "AIHOT_DATA_DIR": str(tmp_path / "py-data")}.items():
        monkeypatch.setenv(k, v)

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    got = []
    with ConnectionPool(world["pd_py"], min_size=1, max_size=4, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for name, *a in calls:
            try:
                if name == "collectSource":
                    got.append(collect_source(pool, a[0], a[1], a[2]))
                elif name == "collectXShard":
                    got.append(collect_x_shard(pool, a[0], a[1]))
                elif name == "checkMpAccount":
                    got.append(check_mp_account(pool, a[0], a[1]))
                else:
                    got.append(schedule_mp_reconcile(pool, datetime.fromisoformat(a[0].replace("Z", "+00:00"))))
            except Exception as error:
                got.append({"error": str(error)})

        def compare(label, calls_, want_, got_):
            for call, w, g in zip(calls_, want_, got_, strict=True):
                if isinstance(w, dict) and set(w) == {"error", "name"}:
                    w = {"error": w["error"]}
                assert g == w, (label, call[:3], w, g)
            a_, b_ = dump(world["pd_node"]), dump(world["pd_py"])
            for part in a_:
                if b_[part] != a_[part]:
                    rows = [(x, y) for x, y in zip(a_[part], b_[part], strict=False) if x != y]
                    detail = [{k: (x.get(k), y.get(k)) for k in x if x.get(k) != y.get(k)} for x, y in rows[:3]]
                    raise AssertionError(f"{label}: {part} differ ({len(a_[part])} node rows, {len(b_[part])} py rows): {json.dumps(detail, ensure_ascii=False, default=str)[:3000]}")
            assert sorted(py_svc.requests) == sorted(node_svc.requests), (label, [r for r in py_svc.requests if r not in node_svc.requests][:4], [r for r in node_svc.requests if r not in py_svc.requests][:4])

        compare("collections", calls, want, got)
        assert any(isinstance(g, dict) and g.get("created", 0) > 0 for g in got) and sum(1 for g in got if isinstance(g, dict) and g.get("status") == "failed") >= 1

        # renames: an account quiet for a day is looked up by a stored post and followed to its new handle
        for url in world.values():
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("UPDATE articles SET discovered_at = '2026-10-01' WHERE source_id IN ('x-bob', 'x-carol')")
                c.execute("UPDATE articles SET x_post = jsonb_set(x_post, '{tweetId}', '\"404\"') WHERE source_id = 'x-carol' AND identity_key = (SELECT min(identity_key) FROM articles WHERE source_id = 'x-carol')")
        calls2 = [["followXRenames", "2026-11-05T05:00:00Z"]]
        want2 = node(tmp_path, calls2, node_svc.env(), world["pd_node"])
        got2 = [follow_x_renames(pool, datetime.fromisoformat("2026-11-05T05:00:00+00:00").timestamp() * 1000, 0)]
        compare("renames", calls2, want2, got2)
        assert got2[0]["checked"] >= 1

        # the long article of a post, and a page read through Jina when it does not read as an article
        with psycopg.connect(world["pd_node"], row_factory=dict_row) as c:
            ids = {r["identity_key"]: r["id"] for r in c.execute("SELECT id, identity_key FROM articles WHERE source_id IN ('x-alice', 'jn-list')").fetchall()}
        for url in world.values():
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("UPDATE articles SET body_status = 'pending', x_post = jsonb_set(x_post, '{tweetId}', '\"1900000000000000077\"') WHERE source_id = 'x-alice' AND identity_key = (SELECT min(identity_key) FROM articles WHERE source_id = 'x-alice')")
                c.execute("UPDATE articles SET body_status = 'pending', url = 'https://example.org/dynamic-page', body_text = NULL WHERE source_id = 'jn-list' AND identity_key = (SELECT min(identity_key) FROM articles WHERE source_id = 'jn-list')")
                c.execute("UPDATE articles SET body_status = 'pending', url = 'https://example.org/missing-page', body_text = NULL WHERE identity_key = 'url:https://example.org/posts/2'")
        with psycopg.connect(world["pd_node"], row_factory=dict_row) as c:
            targets = [r["id"] for r in c.execute("SELECT id FROM articles WHERE url IN ('https://example.org/dynamic-page', 'https://example.org/missing-page') OR x_post->>'tweetId' = '1900000000000000077' ORDER BY source_id, url").fetchall()]
        assert len(targets) == 3, targets
        calls3 = [["extractArticleBody", t] for t in targets]
        want3 = node(tmp_path, calls3, node_svc.env(), world["pd_node"])
        got3 = []
        with psycopg.connect(world["pd_py"], row_factory=dict_row) as c:
            py_targets = [r["id"] for r in c.execute("SELECT id FROM articles WHERE url IN ('https://example.org/dynamic-page', 'https://example.org/missing-page') OR x_post->>'tweetId' = '1900000000000000077' ORDER BY source_id, url").fetchall()]
        for t in py_targets:
            try:
                got3.append(extract_article_body(pool, t))
            except Exception as error:
                got3.append({"error": str(error)})
        compare("bodies", calls3, want3, got3)
        assert "ok" in got3
    assert ids
