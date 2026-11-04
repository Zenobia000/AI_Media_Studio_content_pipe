"""External collection reports (POST /api/ingest/items). Same identity rules and timeline rule as every other entrance: old or future-dated items and explicit
backfill never count as today's news and are never pushed. Unknown sources are created isolated, awaiting an operator. Port of ingest/items.ts."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from newsroom.core.urls import normalize_url
from newsroom.engine.materials import Material, upsert_material
from newsroom.engine.sources.collect import queue_processing
from newsroom.engine.sources.dates import read_publication_date

MAX_ITEMS = 50


class IngestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _trim(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def ingest_items(pool, body: dict) -> dict:
    source_id = _trim(body.get("sourceId"))
    items = body["items"] if isinstance(body.get("items"), list) else []
    if not source_id or not items:
        raise IngestError(400, "sourceId and items[] required")
    if len(items) > MAX_ITEMS:
        raise IngestError(413, f"items[] exceeds max {MAX_ITEMS} per request")
    name = _trim(body.get("sourceName")) or source_id[:120]
    with pool.connection() as conn:
        source = conn.execute(
            """INSERT INTO sources (id, name, kind, config, tier, participation_mode, interval_minutes, enabled, health, tags)
               VALUES (%s, %s, 'external', '{}'::jsonb, 'T2', 'isolated', 1440, true, 'ok', %s)
               ON CONFLICT (id) DO UPDATE SET last_ok_at = now() WHERE sources.enabled
               RETURNING id, participation_mode, enabled""",
            (source_id[:120], name[:200], ["ingest:auto-created"]),
        ).fetchone()
    if not source:
        raise IngestError(409, "source paused")
    seen: set[str] = set()
    created = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        title, raw_url = _trim(it.get("title")), _trim(it.get("url"))
        if not title or not raw_url:
            continue
        url = normalize_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        publication = read_publication_date(it["publishedAt"] if isinstance(it.get("publishedAt"), str) else None, "+00:00")
        raw = it.get("raw")
        flags = (raw.get("_aihot") if isinstance(raw, dict) else None) or {}
        res = upsert_material(
            pool,
            Material(
                source_id=source["id"],
                # Normalize only for deduplication. Ownership needs the reported origin (including www and scheme), and upsert_material already applies the common
                # normalized identity rule.
                url=raw_url, title=title, author=it["author"][:200] if isinstance(it.get("author"), str) else None,
                published_at=publication.published_at, publication_time=publication.publication_time, raw=raw if raw is not None else None, via="ingest",
                backfill="reported-backfill" if flags.get("backfill") else "reported-baseline" if flags.get("baseline") else None,
            ),
        )  # fmt: skip
        if res.created:
            created += 1
        if res.created or res.revised or res.processing_needed:
            queue_processing(pool, res.article_id)
    with pool.connection() as conn:
        conn.execute("UPDATE sources SET last_fetch_at = now(), last_ok_at = now() WHERE id = %s", (source["id"],))
        conn.execute(
            "INSERT INTO ingest_events (client, kind, status, summary) VALUES ('ingest-items', 'items', 'ok', %s)",
            (Jsonb({"sourceId": source["id"], "received": len(items), "created": created}),),
        )
    return {"ok": True, "created": created}
