"""SelectBench: selection-model comparison runs on the human gold set. Runs come from scripts/eval-selection.ts (imported automatically) or an uploaded
report; the admin compares models on the same cases and browses each case. Port of admin/selectbench.ts."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from newsroom.engine.audit import audit
from newsroom.engine.errors import BadRequest


def _blank(v) -> bool:
    return not isinstance(v, str) or not v.strip()


def import_selectbench_run(pool, report, label: str, actor: str) -> dict:
    """A report as scripts/eval-selection.ts writes it: { meta, models }."""
    if report is None:
        raise TypeError("Cannot read properties of null (reading 'models')")
    r = report if isinstance(report, dict) else {}
    models: Any = r.get("models") if r.get("models") is not None else {}
    names = [m for m in models if isinstance(models[m], dict) and models[m].get("summary")] if isinstance(models, dict) else []
    if not names:
        raise BadRequest("report has no model summaries")
    # Reject invalid evidence before writing a run; the same case may appear once per model.
    gold_by_case: dict[str, str] = {}
    for m in names:
        cases = models[m].get("cases")
        if cases is None:
            continue
        if not isinstance(cases, list):
            raise BadRequest(f"{m}: cases must be an array")
        ids: set[str] = set()
        for c in cases:
            if not isinstance(c, dict) or _blank(c.get("caseId")):
                raise BadRequest(f"{m}: caseId must be a non-empty string")
            if c["caseId"] in ids:
                raise BadRequest(f"{m}: duplicate caseId {c['caseId']}")
            ids.add(c["caseId"])
            if c.get("gold") not in ("select", "reject", "either"):
                raise BadRequest(f"{m}: {c['caseId']}: invalid gold decision")
            previous = gold_by_case.get(c["caseId"])
            if previous is not None and previous != c["gold"]:
                raise BadRequest(f"{m}: {c['caseId']}: conflicting gold decision across models")
            gold_by_case[c["caseId"]] = c["gold"]
    meta: Any = r.get("meta") if isinstance(r.get("meta"), dict) else {}
    run_id = f"sb-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(3)}"
    summary = {m: {**models[m]["summary"], "sweep": models[m].get("sweep") if models[m].get("sweep") is not None else []} for m in names}
    first_n = models[names[0]]["summary"].get("n")
    sample_size = int(float(meta["n"] if meta.get("n") is not None else first_n if first_n is not None else 0))
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO selectbench_runs (id, label, split, sample_size, seed, prompt_version, models, summary, imported_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run_id, label, meta.get("split"), sample_size, meta.get("seed"), meta.get("promptVersion"), names, Jsonb(summary), actor),
        )
        for m in names:
            rows = [
                (run_id, m, c["caseId"], c.get("title"), c.get("stratum"), c["gold"], c.get("decision"), c.get("score"), c.get("relevance"), c.get("category"),
                 c.get("reason"), c.get("receiptId"), c.get("error"))
                for c in models[m].get("cases") or []
            ]  # fmt: skip
            if rows:
                conn.cursor().executemany(
                    """INSERT INTO selectbench_results (run_id, model, case_id, title, stratum, gold, decision, score, relevance, category, reason, receipt_id, error)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                    rows,
                )
    with pool.connection() as conn:
        audit(conn, actor, "selectbench.import", f"selectbench:{run_id}", None, None, {"label": label, "models": names, "sampleSize": sample_size})
    return {"id": run_id}


def list_selectbench_runs(pool) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT r.id, r.label, r.split, r.sample_size, r.prompt_version, r.models,
                      (SELECT coalesce(jsonb_object_agg(key, value - 'sweep'), '{}'::jsonb) FROM jsonb_each(r.summary)) AS summary,
                      r.created_at, r.imported_by,
                      (SELECT count(*)::int FROM selectbench_results x WHERE x.run_id = r.id) AS cases
               FROM selectbench_runs r ORDER BY r.created_at DESC LIMIT 100"""
        ).fetchall()


def selectbench_run(pool, run_id: str, f: dict) -> dict | None:
    with pool.connection() as conn:
        run = conn.execute("SELECT * FROM selectbench_runs WHERE id = %s", (run_id,)).fetchone()
        if not run:
            return None
        rows = conn.execute(
            """SELECT case_id, min(title) AS title, min(stratum) AS stratum, min(gold) AS gold,
                      jsonb_object_agg(model, jsonb_build_object('decision', decision, 'score', score, 'relevance', relevance, 'category', category, 'reason', reason, 'error', error, 'receiptId', receipt_id)) AS by_model
               FROM selectbench_results WHERE run_id = %(id)s AND (%(stratum)s::text IS NULL OR stratum = %(stratum)s)
               GROUP BY case_id
               HAVING (%(outcome)s::text IS NULL OR bool_or(
                 model = %(model)s AND CASE %(outcome)s::text
                   WHEN 'fp' THEN decision = 'select' AND gold = 'reject'
                   WHEN 'fn' THEN decision = 'reject' AND gold = 'select'
                   WHEN 'tp' THEN decision = 'select' AND gold = 'select'
                   WHEN 'tn' THEN decision = 'reject' AND gold = 'reject'
                   WHEN 'either' THEN gold = 'either'
                   WHEN 'error' THEN decision IS NULL
                   ELSE true END))
                 AND (%(disagree)s::boolean IS FALSE OR count(DISTINCT decision) > 1)
               ORDER BY min(stratum), case_id LIMIT 400""",
            {"id": run_id, "stratum": f.get("stratum"), "outcome": f.get("outcome"), "model": f.get("model") or run["models"][0], "disagree": bool(f.get("disagree"))},
        ).fetchall()
        strata = conn.execute("SELECT stratum, count(DISTINCT case_id)::int AS n FROM selectbench_results WHERE run_id = %s GROUP BY 1 ORDER BY 2 DESC", (run_id,)).fetchall()
    return {"run": run, "rows": rows, "strata": strata}
