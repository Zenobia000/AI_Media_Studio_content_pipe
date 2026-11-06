"""Admin "模型與評測": the model each capability uses and where that choice comes from, the prompt versions in use, quality / latency / cost of the last days per
model, the switch history and the SelectBench runs that compare models on the same batch. A switch is audited and applies to new work only. Port of
admin/models.ts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from newsroom.engine import models as M
from newsroom.engine.audit import audit
from newsroom.engine.circuit import model_circuits
from newsroom.engine.errors import BadRequest


def _num(v) -> float:
    return 0.0 if v is None else float(v)


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ms) % 1000:03d}Z"


USAGE_SQL = """
SELECT r.purpose, a.model, r.request->>'promptVersion' AS prompt_version, count(*)::int AS calls,
       count(*) FILTER (WHERE a.status = 'received')::int AS ok,
       count(*) FILTER (WHERE a.status = 'failed')::int AS failed,
       count(*) FILTER (WHERE a.status = 'unknown')::int AS unknown,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY a.latency_ms) AS p50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY a.latency_ms) AS p95,
       sum((a.usage->>'prompt_tokens')::bigint) AS tokens_in, sum((a.usage->>'completion_tokens')::bigint) AS tokens_out,
       sum((a.usage->>'prompt_tokens')::bigint) FILTER (WHERE a.cost IS NULL) AS tokens_in_uncosted,
       sum((a.usage->>'completion_tokens')::bigint) FILTER (WHERE a.cost IS NULL) AS tokens_out_uncosted,
       sum(coalesce((a.usage->'prompt_tokens_details'->>'cached_tokens')::bigint, (a.usage->>'prompt_cache_hit_tokens')::bigint, 0)) FILTER (WHERE a.cost IS NULL) AS tokens_cached,
       sum(a.cost) FILTER (WHERE a.cost_basis = 'actual') AS actual_cost, max(a.currency) AS currency
FROM receipt_attempts a JOIN receipts r ON r.id = a.receipt_id
WHERE a.started_at >= %s AND a.origin = 'live' AND a.model IS NOT NULL
GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC"""


def models_overview(pool, days: int = 7) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    with pool.connection() as conn:
        sources = M.model_sources(conn)
        circuits = model_circuits(conn)
        usage = conn.execute(USAGE_SQL, (since,)).fetchall()
        prices = conn.execute("SELECT service, model, currency, input_per_mtok, cached_per_mtok, output_per_mtok FROM service_prices").fetchall()
        history = conn.execute(
            "SELECT created_at AS at, actor, subject, reason, before, after FROM audit_log WHERE action = 'models.switch' ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
        benches = conn.execute(
            """SELECT id, label, sample_size, prompt_version, models,
                      (SELECT coalesce(jsonb_object_agg(key, value - 'sweep'), '{}'::jsonb) FROM jsonb_each(r.summary)) AS summary, created_at
               FROM selectbench_runs r ORDER BY created_at DESC LIMIT 8"""
        ).fetchall()
    registry = M.models()

    def service_of(model: str):
        return next((m["service"] for m in registry.values() if m["model"] == model or m["key"] == model), None)

    def priced(u: dict):
        service = service_of(u["model"]) if u["model"] else None
        p = next((x for x in prices if x["service"] == service and x["model"] == u["model"]), None) or next((x for x in prices if x["service"] == service and x["model"] == ""), None)
        if not p or (not p["input_per_mtok"] and not p["output_per_mtok"]):
            return None
        cached = _num(u["tokens_cached"])
        fresh = _num(u["tokens_in_uncosted"]) - cached
        input_price, output_price = _num(p["input_per_mtok"]), _num(p["output_per_mtok"])
        cached_price = float(p["cached_per_mtok"]) if p["cached_per_mtok"] is not None else float(p["input_per_mtok"] or 0)
        # Cached prompt tokens at the cache price, the rest at the input price; calls that carry their own cost are not estimated again.
        amount = (fresh / 1e6) * input_price + (cached / 1e6) * cached_price + (_num(u["tokens_out_uncosted"]) / 1e6) * output_price
        return {"amount": amount, "currency": p["currency"]}

    steps = []
    for key, c in M.capabilities().items():
        current = sources[key]
        backup = M.fallback_model_for(key, current["model"])
        primary_circuit = circuits.get(registry[current["model"]]["service"])
        backup_circuit = circuits.get(registry[backup]["service"]) if backup else None
        fallback = None
        if backup:
            open_until = (primary_circuit or {}).get("openUntil")
            fallback = {
                "model": backup, "primaryPaused": bool(open_until), "backupPaused": bool((backup_circuit or {}).get("openUntil")),
                "nextProbeAt": _iso(max(open_until, (primary_circuit or {}).get("probeUntil") or 0)) if open_until else None,
            }  # fmt: skip
        steps.append({
            "key": key, "label": c["label"], "env": c["env"], "defaultModel": c["default"], "vision": bool(c["vision"]), "current": current, "fallback": fallback,
            "usage": [
                {
                    "purpose": u["purpose"], "model": u["model"], "promptVersion": u["prompt_version"], "calls": u["calls"], "ok": u["ok"], "failed": u["failed"],
                    "unknown": u["unknown"], "p50": u["p50"], "p95": u["p95"], "tokensIn": _num(u["tokens_in"]), "tokensOut": _num(u["tokens_out"]),
                    "actualCost": None if u["actual_cost"] is None else float(u["actual_cost"]), "currency": u["currency"], "estimate": priced(u),
                }
                for u in usage if u["purpose"] in c["purposes"]
            ],
        })  # fmt: skip
    choices = [{"key": m["key"], "service": m["service"], "vision": bool(m.get("vision"))} for m in registry.values()]
    return {"days": days, "capabilities": steps, "choices": choices, "history": history, "benches": benches}


def switch_model(pool, capability: str, model: str | None, reason: str, actor: str) -> dict:
    """Switches a capability to another registered model (or back to the environment/default when null)."""
    c = M.capabilities().get(capability)
    if not c:
        raise BadRequest("unknown capability")
    if not reason.strip():
        raise BadRequest("a reason is required")
    registry = M.models()
    if model is not None:
        spec = registry.get(model)
        if not spec:
            raise BadRequest("unknown model")
        if not M.capability_accepts_model(c, spec):
            raise BadRequest("this capability needs a vision model")
    with pool.connection() as conn:
        before = M.model_sources(conn).get(capability)
        if model is None:
            conn.execute("DELETE FROM settings WHERE key = %s", (f"models.{capability}",))
        else:
            conn.execute(
                """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
                (f"models.{capability}", Jsonb({"model": model}), actor),
            )
        M.invalidate_model_cache()
        after = M.model_sources(conn).get(capability)
        audit(conn, actor, "models.switch", f"capability:{capability}", reason, before, after)
    return {"capability": capability, "before": before, "after": after}


__all__ = ["models_overview", "switch_model", "Decimal"]
