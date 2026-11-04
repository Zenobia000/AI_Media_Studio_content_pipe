"""publishArticle: the same inputs in two copies of one database, one republished by the TypeScript engine and one by the Python port, must end
with the same publications, pool search rows and selected ledger. Each scenario changes the inputs (judgements, overrides, sources, facts)
the same way in both copies first."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template

SCENARIOS = {
    "unchanged": "SELECT 1",
    "judgements": """UPDATE analyses SET selected = NOT coalesce(selected, false), score = coalesce(score, 50) + 7.37 WHERE id % 3 = 0;
                     UPDATE analyses SET relevance = 'block' WHERE id % 11 = 0""",
    "overrides": """INSERT INTO editorial_overrides (article_id, fields, visibility)
                    SELECT article_id, CASE n % 4 WHEN 0 THEN '{"title":"  Edited title  ","summary":"Edited summary","score":null}'::jsonb
                      WHEN 1 THEN '{"tags":["x","entity:openai","y"],"selected":true,"reason":"Because"}'::jsonb
                      WHEN 2 THEN '{"category":"paper","relevance":"pass","score":91.26}'::jsonb ELSE '{}'::jsonb END,
                      CASE WHEN n % 7 = 0 THEN 'withdrawn' WHEN n % 5 = 0 THEN 'summary-only' ELSE NULL END
                    FROM (SELECT article_id, row_number() OVER (ORDER BY article_id) n FROM publications) x WHERE n <= 24
                    ON CONFLICT DO NOTHING""",
    "fulltext": """UPDATE sources SET site_fulltext = true, syndicate_fulltext = (id::text <= 'm');
                   UPDATE articles SET body_status = 'ok', body_text = 'Body text for article ' || id WHERE id <= 'm'""",
    "isolated and tiers": """UPDATE sources SET participation_mode = 'isolated' WHERE id = (SELECT id FROM sources ORDER BY id LIMIT 1);
                             UPDATE sources SET tier = 'T2' WHERE tier = 'T1'""",
    "grouping pending and backfill": """UPDATE articles SET grouping_status = 'pending' WHERE id > 'x';
                                        UPDATE articles SET backfill = true, published_at = NULL WHERE id > 'f' AND id < 'k'""",
    "memberships": """DELETE FROM fact_articles WHERE (fact_id + length(article_id)) % 4 = 0""",
}
TABLES = {
    "publications": "SELECT * FROM publications ORDER BY article_id",
    "pool_search": "SELECT * FROM pool_search ORDER BY article_id",
    "selected_ledger": "SELECT seq, article_id, op, payload FROM selected_ledger ORDER BY seq",
    "selected_state": "SELECT article_id, in_set, payload_hash, last_seq FROM selected_state ORDER BY article_id",
}


def _admin(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/postgres"


def _clean(value, started: datetime):
    """What can be compared: the run's own clock reads (a release stamped now) are the same instant for this purpose."""
    if isinstance(value, datetime):
        return "NOW" if value >= started else value.isoformat()
    if isinstance(value, (Decimal, float)):
        return float(value)
    if isinstance(value, dict):
        return {k: _clean(v, started) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, started) for v in value]
    return value


def _dump(url: str, started: datetime) -> dict:
    out = {}
    with psycopg.connect(url, row_factory=dict_row) as conn:
        for name, query in TABLES.items():
            rows = conn.execute(query).fetchall()
            for r in rows:
                r.pop("updated_at", None)
            out[name] = [_clean(r, started) for r in rows]
    return out


@pytest.fixture(scope="module")
def copies(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    yield clone_template(admin, database_url, ["pub_node", "pub_py"])
    admin.close()


def _node(url: str) -> None:
    env = {**os.environ, **ENV, "DATABASE_URL": url, "AIHOT_DATA_DIR": str(Path(os.environ.get("TEMP", "/tmp")) / "pub-node-data")}
    subprocess.run(["node", "scripts/republish-all.ts"], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)


def _python(url: str) -> None:
    from newsroom.core.db import _FloatNumeric
    from newsroom.core.migrate import apply_dir
    from newsroom.engine.publication.publish import publish_article

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as c:
        apply_dir(c)  # the job queue's tables
        ids = [r["id"] for r in c.execute("SELECT id FROM articles ORDER BY id").fetchall()]
    with ConnectionPool(url, min_size=1, max_size=2, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for article_id in ids:
            publish_article(pool, article_id)


def _diff(node: list, py: list) -> str:
    if len(node) != len(py):
        return f"{len(node)} rows from TypeScript, {len(py)} from Python"
    for a, b in zip(node, py, strict=True):
        if a != b:
            return "; ".join(f"{k}: {a[k]!r} vs {b.get(k)!r}" for k in a if a[k] != b.get(k))
    return ""


def test_scenarios(copies):
    seen: dict[str, dict] = {}
    started = datetime.now(UTC)  # every release stamp from here on is "now" in both copies
    for name, sql in SCENARIOS.items():
        for url in copies.values():
            with psycopg.connect(url, autocommit=True) as c:
                c.execute(sql)
        _node(copies["pub_node"])
        _python(copies["pub_py"])
        node, py = _dump(copies["pub_node"], started), _dump(copies["pub_py"], started)
        for table in TABLES:
            assert py[table] == node[table], f"{name}: {table} differs: {_diff(node[table], py[table])}"
        assert node["publications"], name
        seen[name] = {
            "withdrawn": any(r["visibility"] == "withdrawn" for r in node["publications"]),
            "removals": any(r["op"] == "remove" for r in node["selected_ledger"]),
            "unselected": any(not r["selected"] for r in node["publications"]),
        }
    # The scenarios really changed things (a comparison of two unchanged copies would prove nothing), and the Python side queued the pushes.
    assert seen["overrides"]["withdrawn"] and seen["judgements"]["unselected"] and any(v["removals"] for v in seen.values()), seen
    with psycopg.connect(copies["pub_py"], row_factory=dict_row) as c:
        names = {r["name"] for r in c.execute("SELECT DISTINCT name FROM jobs").fetchall()}
    assert {"notify.selected", "media.prepare"} <= names, names
