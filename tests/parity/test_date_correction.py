"""The publication-date correction (admin/content.ts: previewPublicationDateCorrection, correctPublicationDate): the same steps run by the TypeScript engine on one
copy of a database and by the Python port on another must give the same plan (with the same approval hash), the same result or refusal, and leave the same rows -
a verified old date replaced by an older one, refused dates, a live date, a retracted live date that archives the article, an editor's grouping that blocks it,
a wrong hash, an unfinished article, an unchanged date, and a retried request."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from tests.parity.conftest import AIHOT, ENV, clone_template
from tests.parity.test_publish import TABLES, _admin, _clean, _diff

ARTICLES = (
    "SELECT id, publication_time, published_at, published_at_claim, timeline_at, backfill, backfill_reason, revision, processing_state, grouping_status, grouped_at "
    "FROM articles ORDER BY id"
)
AUDIT = (
    "SELECT actor, action, subject, reason, before, after, request_id FROM audit_log WHERE action = 'content.correct-publication-date' ORDER BY id"
)
FACTS = "SELECT fact_id, article_id, role, manual FROM fact_articles ORDER BY article_id, fact_id"


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


@pytest.fixture(scope="module")
def copies(database_url, settings_env):
    admin = psycopg.connect(_admin(database_url), autocommit=True, row_factory=dict_row)
    urls = clone_template(admin, database_url, ["dc_node", "dc_py"])
    admin.close()
    migration = Path(__file__).parents[2] / "src/newsroom/core/migrations/0123_job_queue.sql"
    with psycopg.connect(urls["dc_py"], autocommit=True) as c:
        c.execute(migration.read_text(encoding="utf-8"))
    return urls


def test_corrections(copies):
    from newsroom.core.db import _FloatNumeric
    from newsroom.engine.admin.content import correct_publication_date, preview_publication_date_correction
    from newsroom.engine.audit import Conflict

    with psycopg.connect(copies["dc_node"], row_factory=dict_row) as c:
        ids = [r["id"] for r in c.execute("SELECT a.id FROM articles a JOIN publications p ON p.article_id = a.id ORDER BY a.id LIMIT 14").fetchall()]
    assert len(ids) == 14
    base = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
    old, older = "2020-02-15T00:00:00.000Z", "2020-10-29T00:00:00.000Z"
    live = f"'{base.isoformat()}'"
    settled = "processing_state = 'analyzed', grouping_status = 'complete'"
    history = f"published_at = '{old}', published_at_claim = '{old}', timeline_at = '{old}', discovered_at = '{old}', backfill = false, backfill_reason = NULL"
    news = f"published_at = {live}, published_at_claim = {live}, timeline_at = {live}, discovered_at = {live}, backfill = false, backfill_reason = NULL, {settled}"
    past = f"published_at = '{old}', published_at_claim = '{old}', timeline_at = '{old}', discovered_at = '2020-03-20T00:00:00.000Z', backfill = true, backfill_reason = 'stale'"
    # (name, setup SQL for the article, new date, tamper)
    cases = [
        ("history_older", history, older, None),
        ("history_into_window", history, iso(datetime.now(UTC) - timedelta(days=2)), None),
        ("future", news, iso(datetime.now(UTC) + timedelta(days=2)), None),
        ("malformed", news, "2020-10-29", None),
        ("impossible_day", news, "2020-02-30T00:00:00.000Z", None),
        ("retract_historical", past, None, None),
        ("recent_over_historical", past, iso(base), None),
        ("live_earlier", news, iso(base - timedelta(minutes=30)), None),
        ("live_retract_archives", news, None, None),
        ("live_retract_manual_group", news, None, None),
        ("live_unfinished", f"published_at = {live}, published_at_claim = {live}, timeline_at = {live}, discovered_at = {live}, backfill = false, backfill_reason = NULL, processing_state = 'new'", iso(base - timedelta(minutes=30)), None),
        ("wrong_hash", news, iso(base - timedelta(minutes=30)), "hash"),
        ("unchanged", news, iso(base), None),
        ("retried_request", news, iso(base - timedelta(minutes=10)), None),
    ]  # fmt: skip
    steps = []
    for (name, sql, date, tamper), article in zip(cases, ids, strict=True):
        for url in (copies["dc_node"], copies["dc_py"]):
            with psycopg.connect(url, autocommit=True) as c:
                c.execute(f"UPDATE articles SET {sql} WHERE id = %s", (article,))
                if name == "live_retract_manual_group":
                    c.execute("INSERT INTO grouping_overrides (article_id, reason, actor) VALUES (%s, 'standalone', 'parity')", (article,))
        steps.append({"name": name, "id": article, "publishedAt": date, "requestId": f"parity-{name}", "tamper": tamper})
    # the setup moved articles' dates: both copies publish again, so that what is stored matches what is derived, as in a database that has been running
    from newsroom.engine.jobs.publication import run_republish

    with psycopg.connect(copies["dc_node"]) as c:
        sources = [r[0] for r in c.execute("SELECT DISTINCT source_id FROM publications ORDER BY 1").fetchall()]
    env = {**os.environ, **ENV, "DATABASE_URL": copies["dc_node"], "AIHOT_DATA_DIR": str(Path(os.environ.get("TEMP", "/tmp")) / "dc-node-data")}
    subprocess.run(["node", "scripts/republish-source-run.ts", *sources], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300)
    with ConnectionPool(copies["dc_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, open=True) as pool:
        for source in sources:
            run_republish(pool, source)
    # the same request again, then the same request id asking for another date
    again = next(s for s in steps if s["name"] == "retried_request")
    steps.append({**again, "name": "retried_again"})
    steps.append({**again, "name": "retried_other_date", "publishedAt": iso(base - timedelta(minutes=20))})
    started = datetime.now(UTC)

    env = {**os.environ, **ENV, "DATABASE_URL": copies["dc_node"], "AIHOT_DATA_DIR": str(Path(os.environ.get("TEMP", "/tmp")) / "dc-node-data")}
    out = subprocess.run(
        ["node", "scripts/date-correction-run.ts", json.dumps(steps)], cwd=AIHOT, env=env, check=True, capture_output=True, timeout=300
    )
    node = json.loads(next(line for line in reversed(out.stdout.decode().splitlines()) if line.startswith("[")))

    def configure(conn):
        conn.adapters.register_loader("numeric", _FloatNumeric)

    py = []
    with ConnectionPool(
        copies["dc_py"], min_size=1, max_size=3, kwargs={"row_factory": dict_row, "autocommit": True}, configure=configure, open=True
    ) as pool:
        for s in steps:
            entry: dict = {"name": s["name"]}
            plan = None
            try:
                plan = preview_publication_date_correction(pool, s["id"], s["publishedAt"])
                entry["plan"] = plan
            except Exception as error:
                entry["plan"] = {"error": str(error), "kind": type(error).__name__}
            if plan:
                try:
                    entry["result"] = correct_publication_date(
                        pool,
                        s["id"],
                        {
                            "version": plan["version"],
                            "hash": "0" * 64 if s["tamper"] == "hash" else plan["hash"],
                            "publishedAt": s["publishedAt"],
                            "requestId": s["requestId"],
                            "reason": "parity check",
                        },
                        "parity",
                    )
                except Conflict as error:  # fmt: skip
                    entry["result"] = {"error": str(error), "kind": "Conflict"}
                except Exception as error:
                    entry["result"] = {"error": str(error), "kind": type(error).__name__}
            py.append(entry)

    # a Node Error and a Python ValueError are the same refusal: compare the message, and the class only where it matters (Conflict)
    def shape(entries):
        shaped = []
        for e in entries:
            e = json.loads(json.dumps(e))
            for key in ("plan", "result"):
                if isinstance(e.get(key), dict) and "error" in e[key]:
                    e[key]["kind"] = "Conflict" if e[key]["kind"] == "Conflict" else "Error"
            if e["name"] in ("retried_again", "retried_other_date") and isinstance(e.get("plan"), dict):
                e["plan"].pop("hash", None)  # the plan's hash holds updated_at, which each copy set to its own now() when the first request applied
            shaped.append(e)
        return shaped

    node, py = shape(node), shape(py)
    for a, b in zip(node, py, strict=True):
        assert a == b, f"{a['name']}\nTS: {a}\nPY: {b}"
    # the cases did what they are for
    by = {e["name"]: e for e in node}
    assert by["history_older"]["result"]["status"] == "corrected" and by["history_older"]["plan"]["mode"] == "history"
    for refused in ("history_into_window", "future", "malformed", "impossible_day", "retract_historical", "recent_over_historical"):
        assert "error" in by[refused]["plan"], refused
    assert by["live_earlier"]["result"]["status"] == "corrected" and by["live_earlier"]["plan"]["mode"] == "live"
    assert by["live_retract_archives"]["plan"]["archive"] is True and by["live_retract_archives"]["result"]["status"] == "corrected"
    assert by["live_retract_manual_group"]["result"]["kind"] == "Conflict"
    assert by["live_unfinished"]["result"]["kind"] == "Conflict"
    assert by["wrong_hash"]["result"]["kind"] == "Conflict"
    assert by["unchanged"]["result"]["status"] == "unchanged"
    assert by["retried_again"]["result"] == by["retried_request"]["result"] and by["retried_other_date"]["result"]["kind"] == "Conflict"

    def dump(url):
        with psycopg.connect(url, row_factory=dict_row) as c:
            tables = {"articles": ARTICLES, "audit": AUDIT, "facts": FACTS, **TABLES}
            result = {}
            for name, query in tables.items():
                rows = c.execute(query).fetchall()
                for r in rows:
                    r.pop("updated_at", None)
                result[name] = [_clean(r, started) for r in rows]
            return result

    a, b = dump(copies["dc_node"]), dump(copies["dc_py"])
    for table in a:
        assert b[table] == a[table], f"{table} differs: {_diff(a[table], b[table])}"
    assert len(a["audit"]) == sum(1 for e in node if e.get("result", {}).get("status") == "corrected" and e["name"] != "retried_again")
