"""The Python worker end to end: a source on a local site is queued by the schedule, collected by a worker thread, its article pages are fetched by the extraction
job, and the articles end up with bodies and with their analysis queued - no TypeScript involved."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tests.engine.reader_pages import PAGES
from tests.parity.conftest import clone_template
from tests.parity.test_publish import _admin

NOW = datetime.now(UTC)
ARTICLES = {"one": PAGES["news"], "two": PAGES["blog_lazy_images"], "three": PAGES["jsonld_meta"]}


def feed(origin: str) -> str:
    items = "".join(
        f"<item><title>Story {name}</title><link>{origin}/a/{name}</link><guid>{name}</guid><pubDate>{(NOW - timedelta(hours=i + 1)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>"
        f"<description>Short summary of {name}</description></item>"
        for i, name in enumerate(ARTICLES)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{items}</channel></rss>'


def c_count(url: str, query: str) -> int:
    with psycopg.connect(url, row_factory=dict_row) as c:
        return c.execute(query).fetchone()["n"]


def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            path = self.path.split("?")[0]
            if path == "/feed.xml":
                body, ctype = feed(origin), "application/rss+xml"
            elif path.startswith("/a/") and path[3:] in ARTICLES:
                body, ctype = ARTICLES[path[3:]], "text/html"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def database(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    url = clone_template(admin, database_url, ["e2e_py"])["e2e_py"]
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
        c.execute("UPDATE sources SET enabled = false")
    return url


def test_collect_extract_analyse_and_publish(database, monkeypatch):
    from newsroom.engine.jobs.sources import schedule_due_sources
    from newsroom.engine.worker import build_worker, open_pool
    from tests.parity.test_analyze import Fake

    model = Fake()
    for k, v in {"MODEL_CALLS_ENABLED": "true", "LLM_BASE_URL": model.url, "LLM_API_KEY": "secret-key", "LLM_MODEL": "fake", "COLLECT_ENABLED": "true"}.items():
        monkeypatch.setenv(k, v)
    server = serve()
    origin = f"http://127.0.0.1:{server.server_port}"
    with psycopg.connect(database, autocommit=True) as c:
        c.execute(
            "INSERT INTO sources (id, name, kind, config, created_at) VALUES ('e2e-rss', 'E2E', 'rss', %s, now() - interval '30 days')",
            (Jsonb({"feedUrl": f"{origin}/feed.xml", "fetchPublicContent": True}),),
        )
    pool = open_pool(database, size=12)
    worker = build_worker(pool)
    worker.start()
    try:
        assert schedule_due_sources(pool)["enqueued"] == 1
        deadline = time.monotonic() + 90
        state = {}
        while time.monotonic() < deadline:
            with psycopg.connect(database, row_factory=dict_row) as c:
                arts = c.execute("SELECT title, body_status, length(body_text) AS chars, processing_queued_at IS NOT NULL AS queued FROM articles WHERE source_id = 'e2e-rss' ORDER BY title").fetchall()
                src = c.execute("SELECT health, fail_count, last_error, cursor FROM sources WHERE id = 'e2e-rss'").fetchone()
                queued = c.execute("SELECT name, count(*)::int AS n FROM jobs WHERE state IN ('created', 'active', 'retry') GROUP BY 1 ORDER BY 1").fetchall()
                runs = c.execute("SELECT status, found_count, new_count FROM fetch_runs WHERE source_id = 'e2e-rss'").fetchall()
            state = {"arts": arts, "src": src, "queued": queued, "runs": runs}
            analysed = c_count(database, "SELECT count(*) AS n FROM articles WHERE source_id = 'e2e-rss' AND processing_state IN ('analyzed', 'blocked')")
            # An article is marked analysed inside its job, a moment before the job itself is marked done: the queue has to be empty of those too.
            working = any(q["name"] in ("content.analyze", "content.extract-body") for q in queued)
            if len(arts) == 3 and all(a["body_status"] == "ok" for a in arts) and analysed == 3 and not working:
                state["queued"] = queued
                break
            time.sleep(1)
        else:
            pytest.fail(f"the pipeline did not finish: {state}")
    finally:
        worker.stop(timeout=30)
        pool.close()
        server.shutdown()
    assert state["src"]["health"] == "ok" and state["src"]["fail_count"] == 0 and state["src"]["cursor"]["initializedAt"]
    assert state["runs"] == [{"status": "ok", "found_count": 3, "new_count": 3}]
    assert all(a["chars"] > 200 for a in state["arts"])
    assert not any(q["name"] in ("content.analyze", "content.extract-body") for q in state["queued"])
    with psycopg.connect(database, row_factory=dict_row) as c:
        assert c.execute("SELECT count(*)::int AS n FROM analyses an JOIN articles a ON a.id = an.article_id WHERE a.source_id = 'e2e-rss'").fetchone()["n"] == 3
        assert c.execute("SELECT count(*)::int AS n FROM publications p JOIN articles a ON a.id = p.article_id WHERE a.source_id = 'e2e-rss'").fetchone()["n"] == 3
        groups = c.execute("SELECT count(*)::int AS n FROM jobs WHERE name = 'events.group'").fetchone()["n"]
    assert groups >= 1  # what passed the analysis waits for event grouping, which is not ported yet
    assert len(model.requests) >= 12  # prefilter, two scores, structure and the writing for each of the three
