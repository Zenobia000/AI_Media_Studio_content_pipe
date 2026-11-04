"""upsertMaterial: the same reports stored by the TypeScript engine and by the Python port, each in its own copy of one database, must leave the same
articles, revisions, discoveries and publications."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import _admin

T0 = "2026-11-04T09:00:00.000Z"


def _at(minutes: int) -> str:
    from datetime import timedelta

    return (datetime.fromisoformat(T0.replace("Z", "+00:00")) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def scenario(sources: list[str]) -> list[dict]:
    a, b = sources[0], sources[1]

    def item(url, **kw):
        return {"sourceId": a, "url": url, "title": "Title of " + url.rsplit("/", 1)[-1], "via": "fetch", "discoveredAt": _at(0), **kw}

    return [
        item("https://example.org/m/1"),
        item("https://example.org/m/1", discoveredAt=_at(10)),  # the same again
        item("https://example.org/m/1", title="A changed title", discoveredAt=_at(20)),  # a revision
        item("https://example.org/m/1", title="Title of 1", discoveredAt=_at(30)),  # back to a version seen before
        item("https://example.org/m/2", publishedAt=_at(-60), publicationTime=_at(-60), discoveredAt=_at(0)),
        item("https://example.org/m/2", sourceId=b, title="Another source's own wording", discoveredAt=_at(40)),  # a discovery only
        item("https://example.org/m/3", publishedAt="2026-10-30T00:00:00.000Z", publicationTime="2026-10-30"),  # stale on discovery
        item("https://example.org/m/4", publishedAt=_at(300)),  # a time in the future
        item("https://example.org/m/5", backfill="first-import", publishedAt=_at(-10)),
        item(
            "https://example.org/m/6",
            bodyText="Some article text. " * 5,
            bodyHtml="<p>Some article text.</p>",
            language="en",
            author="Ada",
            excerpt="Some excerpt",
        ),
        item(
            "https://example.org/m/6", bodyText="Some article text. " * 5 + "More.", bodyHtml="<p>Some article text. More.</p>", discoveredAt=_at(50)
        ),
        item("https://example.org/m/7"),
        item("https://example.org/m/7", publishedAt=_at(-5), publicationTime=_at(-5), discoveredAt=_at(60)),  # the date arrives later
        item("https://example.org/m/8", title="Café  — “quoted” &amp; more", excerpt="a�b"),
        item("https://example.org/m/8", title="Café — “quoted” & more", excerpt="aXb", discoveredAt=_at(70)),  # characters lost in transit
        {
            "sourceId": a,
            "url": "https://x.com/someone/status/1234567890",
            "title": "A post",
            "via": "fetch",
            "discoveredAt": _at(0),
            "publishedAt": _at(-20),
            "xPost": {"tweetId": "1234567890", "authorName": "Someone", "handle": "someone", "text": "A post", "media": []},
        },  # fmt: skip
        item("https://pub.example/blog/post-1", discoveredAt=_at(0)),  # a verified publisher takes over the attribution
        item("https://pub.example/blog/post-1", discoveredAt=_at(80)),
        item("https://example.org/m/9", media=[{"kind": "image", "url": "https://example.org/i.png"}], bodyHtml="<p>x</p>", raw={"externalId": 42}),
        item("https://example.org/m/9", media=[{"kind": "image", "url": "https://example.org/i2.png"}], bodyHtml="<p>x</p>", discoveredAt=_at(90)),
    ]


def to_material(d: dict):
    from newsroom.engine.materials import UNSET, Material

    def when(v):
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if isinstance(v, str) else v

    return Material(
        source_id=d["sourceId"], url=d["url"], title=d["title"], via=d["via"], identity_key=d.get("identityKey"), author=d.get("author"),
        language=d.get("language"), published_at=when(d.get("publishedAt")), publication_time=d.get("publicationTime", UNSET),
        source_updated_at=when(d.get("sourceUpdatedAt")), excerpt=d.get("excerpt"), body_html=d.get("bodyHtml"), body_text=d.get("bodyText"),
        body_status=d.get("bodyStatus"), media=d.get("media"), x_post=d.get("xPost"), raw=d.get("raw", UNSET), discovered_at=when(d.get("discoveredAt")),
        backfill=d.get("backfill"),
    )  # fmt: skip


QUERIES = {
    "articles": """SELECT identity_key, source_id, url, title, author, language, published_at, published_at_claim, publication_time, discovered_at, source_updated_at,
                     timeline_at, backfill, backfill_reason, revision, content_hash, excerpt, body_text, body_html, body_status, media, x_post, raw,
                     grouping_status, processing_state FROM articles WHERE identity_key LIKE ANY(ARRAY['url:%%example.org/m/%%', 'url:%%pub.example%%', 'x:1234567890'])
                   ORDER BY identity_key""",
    "revisions": """SELECT a.identity_key, r.revision, r.content_hash, r.title, r.body_text FROM article_revisions r JOIN articles a ON a.id = r.article_id
                    WHERE a.identity_key LIKE ANY(ARRAY['url:%%example.org/m/%%', 'url:%%pub.example%%', 'x:1234567890']) ORDER BY a.identity_key, r.revision""",
    "discoveries": """SELECT a.identity_key, d.source_id, d.via, d.discovered_at FROM article_discoveries d JOIN articles a ON a.id = d.article_id
                      WHERE a.identity_key LIKE ANY(ARRAY['url:%%example.org/m/%%', 'url:%%pub.example%%', 'x:1234567890']) ORDER BY a.identity_key, d.source_id, d.discovered_at""",
    "publications": """SELECT a.identity_key, p.revision, p.source_id, p.visibility, p.eligible, p.selected, p.title, p.summary, p.timeline_at, p.backfill FROM publications p
                       JOIN articles a ON a.id = p.article_id WHERE a.identity_key LIKE ANY(ARRAY['url:%%example.org/m/%%', 'url:%%pub.example%%', 'x:1234567890']) ORDER BY a.identity_key""",
    "audit": "SELECT actor, action, reason, before, after FROM audit_log WHERE action = 'article.attribution' AND subject IN (SELECT 'article:' || id FROM articles WHERE identity_key LIKE 'url:%%pub.example%%')",
}


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@pytest.fixture(scope="module")
def copies(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["mat_node", "mat_py"])
    yield urls
    admin.close()


def test_materials(copies, tmp_path):
    from newsroom.core.db import _FloatNumeric
    from newsroom.core.migrate import apply_dir
    from newsroom.engine.materials import upsert_material

    with psycopg.connect(copies["mat_node"], row_factory=dict_row) as c:
        sources = [r["id"] for r in c.execute("SELECT id FROM sources WHERE participation_mode = 'editorial' ORDER BY id LIMIT 2").fetchall()]
    setup = """INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health)
               VALUES ('par-publisher', 'The Publisher', 'web_list', '{"publisherUrlPrefixes":["https://pub.example/blog"]}'::jsonb, 'T1', 'editorial', 60, true, 'ok')
               ON CONFLICT DO NOTHING"""
    for url in copies.values():
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(setup)
    items = scenario(sources)
    path = tmp_path / "materials.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    env = {**os.environ, **ENV, "DATABASE_URL": copies["mat_node"], "AIHOT_DATA_DIR": str(tmp_path / "data")}
    out = subprocess.run(["node", "scripts/material-run.ts", str(path)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    node_results = json.loads(out.stdout.decode().strip().splitlines()[-1])

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with psycopg.connect(copies["mat_py"], autocommit=True, row_factory=dict_row) as c:
        apply_dir(c)
    py_results = []
    with ConnectionPool(
        copies["mat_py"], min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True
    ) as pool:
        for item in items:
            r = upsert_material(pool, to_material(item))
            py_results.append(
                {"created": r.created, "revised": r.revised, "backfill": r.backfill, **({"processingNeeded": True} if r.processing_needed else {})}
            )
    node_flags = [{k: v for k, v in r.items() if k != "articleId"} for r in node_results]
    assert py_results == node_flags
    dumps = {}
    for name, url in copies.items():
        with psycopg.connect(url, row_factory=dict_row) as c:
            dumps[name] = {k: _plain([dict(r) for r in c.execute(q).fetchall()]) for k, q in QUERIES.items()}
    for table in QUERIES:
        assert dumps["mat_py"][table] == dumps["mat_node"][table], table
    assert len(dumps["mat_node"]["articles"]) >= 10 and dumps["mat_node"]["audit"]
