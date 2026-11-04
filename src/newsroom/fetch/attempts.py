"""What every fetch layer tried, and whether it worked (FET-FR-011): the figures that say which layer is worth its upkeep, and the record
of the daily self-test (FET-FR-010)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

LAYER_NAMES = {0: "原文擷取", 1: "L1 官方 API", 2: "L2 網頁檔案", 3: "L3 下載器", 4: "L4 瀏覽器錄影"}


@dataclass
class Attempt:
    layer: int
    url: str
    ok: bool
    reason: str | None = None


def record_attempts(conn, attempts: list[Attempt], kind: str = "download", at: datetime | None = None) -> None:
    at = at or datetime.now(timezone.utc)
    for a in attempts:
        conn.execute(
            "INSERT INTO fetch_attempts (layer, kind, url, ok, reason, attempted_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (a.layer, kind, a.url, a.ok, a.reason, at),
        )


@dataclass
class LayerStats:
    layer: int
    name: str
    attempts: int
    ok: int
    rate: float  # 0 to 1


def fetch_stats(conn, days: int = 30, now: datetime | None = None) -> list[LayerStats]:
    """The success rate of each layer over the last `days` days of real downloads (self-tests are not counted)."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT layer, count(*)::int AS attempts, count(*) FILTER (WHERE ok)::int AS ok FROM fetch_attempts
           WHERE kind = 'download' AND attempted_at > %s AND attempted_at <= %s GROUP BY layer ORDER BY layer""",
        (now - timedelta(days=days), now),
    ).fetchall()
    return [
        LayerStats(r["layer"], LAYER_NAMES.get(r["layer"], f"L{r['layer']}"), r["attempts"], r["ok"], r["ok"] / r["attempts"] if r["attempts"] else 0)
        for r in rows
    ]
