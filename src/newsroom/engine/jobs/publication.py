"""Publication jobs: re-deriving a whole source after an admin change (jobs/publication.ts)."""

from __future__ import annotations

from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from newsroom.engine.events.hot import compute_hot_ranking
from newsroom.engine.jobs.publication_keys import republish_key
from newsroom.engine.publication.publish import republish_source
from newsroom.engine.shutdown import SHUTDOWN


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _progress(pool, source_id: str, value: dict) -> None:
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_by) VALUES (%s, %s, 'worker')
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()""",
            (republish_key(source_id), Jsonb(value)),
        )


def run_republish(pool, source_id: str) -> dict:
    started = _iso(datetime.now(UTC))
    _progress(pool, source_id, {"status": "running", "done": 0, "total": None, "startedAt": started})
    result = republish_source(
        pool, source_id, lambda done, total: _progress(pool, source_id, {"status": "running", "done": done, "total": total, "startedAt": started}), SHUTDOWN
    )
    if result["reduced"] > 0:
        compute_hot_ranking(pool)  # the hot board may show one of the source's articles: re-rank now rather than within five minutes
    _progress(pool, source_id, {"status": "done", **result, "startedAt": started, "finishedAt": _iso(datetime.now(UTC))})
    return result
