"""Shared provider health for model failover: settings retain the open circuit and the single recovery probe across workers (providers/circuit.ts)."""

from __future__ import annotations


def model_circuits(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'model.circuit.%'").fetchall()
    return {r["key"][len("model.circuit.") :]: r["value"] for r in rows}
