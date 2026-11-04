"""Shared provider health for model failover. The existing receipt claim serializes admission; settings retain the open circuit and the single recovery probe
across workers and restarts (providers/circuit.ts)."""

from __future__ import annotations

import time
import uuid

from psycopg.types.json import Jsonb

from newsroom.engine.receipts import ProviderRejectedError

FAILURE_WINDOW_MS = 5 * 60_000
OPEN_MS = 5 * 60_000
FAILURE_LIMIT = 10


def key_of(service: str) -> str:
    return f"model.circuit.{service}"


def closed() -> dict:
    return {"failures": 0, "firstFailureAt": 0, "openUntil": 0, "probe": None, "probeUntil": 0}


class CircuitOpenError(ProviderRejectedError):
    def __init__(self, service: str) -> None:
        super().__init__(f"{service} temporarily paused after repeated transport failures", 503, True)


def now_ms() -> int:
    return int(time.time() * 1000)


def enter_model_circuit(conn, service: str, timeout_ms: int) -> dict:
    """Called inside paid_request's service lock, before recording a paid attempt: {"probe": id or None}."""
    key = key_of(service)
    conn.execute("INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, 'model.circuit') ON CONFLICT DO NOTHING", (key, Jsonb(closed())))
    state = conn.execute("SELECT value FROM settings WHERE key = %s FOR UPDATE", (key,)).fetchone()["value"]
    if not state.get("openUntil"):
        return {"probe": None}
    now = now_ms()
    if state["openUntil"] > now or state["probeUntil"] > now:
        raise CircuitOpenError(service)
    state["probe"] = str(uuid.uuid4())
    state["probeUntil"] = now + timeout_ms
    conn.execute("UPDATE settings SET value = %s, updated_at = now() WHERE key = %s", (Jsonb(state), key))
    return {"probe": state["probe"]}


def record_model_health(pool, service: str, permit: dict, unavailable: bool) -> None:
    """Only actual HTTP attempts report health. Cached answers and local refusals cannot restore it."""
    key = key_of(service)
    with pool.connection() as conn, conn.transaction():
        state = conn.execute("SELECT value FROM settings WHERE key = %s FOR UPDATE", (key,)).fetchone()["value"]
        # A call admitted before opening, or a probe whose lease was replaced, cannot close the circuit.
        if state.get("openUntil") and (not permit["probe"] or state.get("probe") != permit["probe"]):
            return
        if not state.get("openUntil") and permit["probe"]:
            return
        if not unavailable:
            if state.get("failures") or state.get("openUntil"):
                conn.execute("UPDATE settings SET value = %s, updated_at = now() WHERE key = %s", (Jsonb(closed()), key))
            return
        now = now_ms()
        if not state.get("firstFailureAt") or now - state["firstFailureAt"] > FAILURE_WINDOW_MS:
            state["firstFailureAt"] = now
            state["failures"] = 0
        state["failures"] += 1
        if permit["probe"] or state["failures"] >= FAILURE_LIMIT:
            state["openUntil"] = now + OPEN_MS
        state["probe"] = None
        state["probeUntil"] = 0
        conn.execute("UPDATE settings SET value = %s, updated_at = now() WHERE key = %s", (Jsonb(state), key))


def model_circuits(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'model.circuit.%'").fetchall()
    return {r["key"][len("model.circuit.") :]: r["value"] for r in rows}
