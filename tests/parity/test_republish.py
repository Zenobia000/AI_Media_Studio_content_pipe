"""republishSource and its job: a source whose name, tier, licence or role changed is re-derived by the TypeScript engine in one copy of a database and by the Python
port's job in another; their public rows and ledgers must be the same, and the job must report its progress."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import TABLES, _admin, _diff, _dump

SCENARIOS = {
    "renamed": "UPDATE sources SET name = name || ' (renamed)' WHERE id IN (SELECT id FROM sources ORDER BY id LIMIT 2)",
    "tier": "UPDATE sources SET tier = 'T1' WHERE id IN (SELECT id FROM sources ORDER BY id OFFSET 2 LIMIT 2)",
    "licence": "UPDATE sources SET site_fulltext = NOT site_fulltext, syndicate_fulltext = NOT syndicate_fulltext WHERE id IN (SELECT id FROM sources ORDER BY id LIMIT 3)",
    "isolated": "UPDATE sources SET participation_mode = 'isolated' WHERE id IN (SELECT id FROM sources ORDER BY id OFFSET 1 LIMIT 1)",
    "hot": "UPDATE sources SET participation_mode = 'hot_signal' WHERE id IN (SELECT id FROM sources ORDER BY id OFFSET 3 LIMIT 1)",
}


@pytest.fixture(scope="module")
def copies(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["rep_node", "rep_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["rep_py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    return urls


def test_scenarios(copies):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.jobs.publication import run_republish

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    started = datetime.now(UTC)
    changed_any = False
    with ConnectionPool(copies["rep_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True) as pool:
        for name, sql in SCENARIOS.items():
            for url in copies.values():
                with psycopg.connect(url, autocommit=True) as c:
                    c.execute(sql)
            with psycopg.connect(copies["rep_node"]) as c:
                sources = [r[0] for r in c.execute("SELECT DISTINCT source_id FROM publications ORDER BY 1").fetchall()]
            env = {**os.environ, **ENV, "DATABASE_URL": copies["rep_node"], "AIHOT_DATA_DIR": str(Path(os.environ.get("TEMP", "/tmp")) / "rep-node-data")}
            out = subprocess.run(["node", "scripts/republish-source-run.ts", *sources], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
            node_results = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("{")))
            py_results = {s: run_republish(pool, s) for s in sources}
            assert py_results == node_results, (name, node_results, py_results)
            changed_any = changed_any or any(r["changed"] for r in node_results.values())
            node, py = _dump(copies["rep_node"], started), _dump(copies["rep_py"], started)
            for table in TABLES:
                assert py[table] == node[table], f"{name}: {table} differs: {_diff(node[table], py[table])}"
    assert changed_any
    with psycopg.connect(copies["rep_py"], row_factory=dict_row) as c:
        progress = c.execute("SELECT key, value FROM settings WHERE key LIKE 'republish.source:%'").fetchall()
    assert progress and all(r["value"]["status"] == "done" and r["value"]["total"] >= 1 for r in progress)
