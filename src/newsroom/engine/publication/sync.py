"""The selected sync (snapshot + changes), read from the sync ledger: port of the second half of publication/v1.ts. The ledger itself is written
by publishing (publication/publish.ts)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from newsroom.engine.lib.cursor import InvalidCursorError, decode_cursor, encode_cursor
from newsroom.engine.lib.ids import new_short_id
from newsroom.engine.publication.scope import listed_condition, scope_params
from newsroom.engine.publication.v1 import iso

SYNC_PREFIX = "ax1"  # any other watermark answers 409
_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


class SnapshotRequiredError(Exception):
    pass


def _is_watermark(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


_epoch: list[str] = []


def ledger_epoch(conn) -> str:
    """Ledger epoch: changes whenever the ledger is rebuilt (e.g. after an import), invalidating old watermarks."""
    if _epoch:
        return _epoch[0]
    row = conn.execute("SELECT value FROM settings WHERE key = 'selected_ledger_epoch'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('selected_ledger_epoch', %s::jsonb) ON CONFLICT (key) DO NOTHING",
            (f'{{"epoch":"{new_short_id(6)}"}}',),
        )
        row = conn.execute("SELECT value FROM settings WHERE key = 'selected_ledger_epoch'").fetchone()
    _epoch[:] = [row["value"]["epoch"]]
    return _epoch[0]


def ledger_watermark(conn) -> int:
    """The ledger's latest sequence: a snapshot and a page of changes read up to it."""
    return int(conn.execute("SELECT coalesce(max(seq), 0) AS w FROM selected_ledger").fetchone()["w"])


def minimal_of(item: dict) -> dict:
    return {
        "id": item["id"], "title": item["title"], "source": item["source"], "publishedAt": item["publishedAt"], "discoveredAt": item["discoveredAt"],
        "category": item["category"], "score": item["score"], "selected": item["selected"], "links": {"aihot": item["links"]["aihot"]},
    }  # fmt: skip


def _ledger_payload(minimal: bool, payload: str = "payload") -> str:
    """Drop unused wide fields before crossing the database connection; minimal_of remains the output whitelist."""
    if minimal:
        return f"({payload} - ARRAY['originalTitle', 'summary', 'reason', 'attribution']::text[]) #- '{{links,original}}'"
    return payload


def selected_snapshot(conn, fields: str | None, limit: int, page: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    epoch = ledger_epoch(conn)
    use_fields = fields or "default"
    after_id = ""
    if page is not None:
        # A page token that is broken, empty or from another snapshot (a rebuilt ledger) cannot continue this snapshot: the client starts over.
        try:
            p = decode_cursor(SYNC_PREFIX, page)
        except InvalidCursorError as e:
            raise SnapshotRequiredError("unreadable snapshot page token") from e
        if (p.get("k") != "page" or p.get("e") != epoch or p.get("f") not in ("default", "minimal") or not _is_watermark(p.get("w"))
                or not isinstance(p.get("a"), str) or not _ID.match(p["a"]) or not isinstance(p.get("t"), str) or not _parses(p["t"])):  # fmt: skip
            raise SnapshotRequiredError("page token does not match this snapshot")
        # Tokens are opaque, not signed: a supplied watermark must not be ahead of the ledger.
        if p["w"] > ledger_watermark(conn):
            raise SnapshotRequiredError("page watermark is ahead of this ledger")
        # A readable token asked for with another projection is the request's mistake, not a lost snapshot.
        if fields is not None and p["f"] != fields:
            raise InvalidCursorError("page token belongs to another projection")
        use_fields, w, after_id, as_of = p["f"], p["w"], p["a"], p["t"]
    else:
        w = ledger_watermark(conn)
        as_of = iso(now)
    # The set as of the watermark, less anything taken out of the selected set since.
    rows = conn.execute(
        f"""SELECT latest.article_id, {_ledger_payload(use_fields == "minimal", "latest.payload")} AS payload FROM (
              SELECT DISTINCT ON (article_id) article_id, op, payload FROM selected_ledger
              WHERE seq <= %(w)s AND article_id > %(after)s ORDER BY article_id, seq DESC
            ) latest
            JOIN selected_state st ON st.article_id = latest.article_id AND st.in_set
            WHERE latest.op = 'upsert' ORDER BY latest.article_id LIMIT %(limit)s""",
        {"w": w, "after": after_id, "limit": limit + 1},
    ).fetchall()
    page_rows = rows[:limit]
    has_more = len(rows) > limit
    last = page_rows[-1] if page_rows else None
    return {
        "schemaVersion": 1,
        "asOf": as_of,
        "fields": use_fields,
        "cursor": encode_cursor(SYNC_PREFIX, {"k": "sync", "e": epoch, "w": w, "f": use_fields}),
        "count": len(page_rows),
        "hasMore": has_more,
        "nextPage": encode_cursor(SYNC_PREFIX, {"k": "page", "e": epoch, "w": w, "f": use_fields, "a": last["article_id"], "t": as_of}) if has_more and last else None,
        "items": [minimal_of(r["payload"]) if use_fields == "minimal" else r["payload"] for r in page_rows],
    }  # fmt: skip


def _parses(text: str) -> bool:
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def selected_changes(conn, cursor: str, limit: int, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    epoch = ledger_epoch(conn)
    try:
        c = decode_cursor(SYNC_PREFIX, cursor)
    except InvalidCursorError as e:
        raise SnapshotRequiredError("unknown watermark") from e
    if c.get("k") != "sync" or c.get("e") != epoch or not _is_watermark(c.get("w")) or c.get("f") not in ("default", "minimal"):
        raise SnapshotRequiredError("watermark from another ledger epoch or format")
    w = ledger_watermark(conn)
    if c["w"] > w:
        raise SnapshotRequiredError("watermark is ahead of this ledger")
    # An offline client's old watermark must not redistribute content withdrawn since the upsert. Keep every sequence (as a removal) so even a
    # one-entry page advances without exposing it.
    rows = conn.execute(
        f"""SELECT l.seq, l.article_id, CASE WHEN p.article_id IS NULL THEN 'remove' ELSE l.op END AS op, l.changed_at,
              CASE WHEN p.article_id IS NOT NULL THEN {_ledger_payload(c["f"] == "minimal", "l.payload")} END AS payload
            FROM selected_ledger l LEFT JOIN publications p ON p.article_id = l.article_id AND {listed_condition()}
            WHERE l.seq > %(after)s AND l.seq <= %(w)s ORDER BY l.seq LIMIT %(limit)s""",
        {**scope_params(now), "after": c["w"], "w": w, "limit": limit + 1},
    ).fetchall()
    page = rows[:limit]
    has_more = len(rows) > limit
    next_w = page[-1]["seq"] if page else max(c["w"], 0)
    changes = []
    for r in page:
        if r["op"] == "remove":
            changes.append({"op": "remove", "changedAt": iso(r["changed_at"]), "id": r["article_id"]})
        else:
            changes.append(
                {"op": "upsert", "changedAt": iso(r["changed_at"]), "item": minimal_of(r["payload"]) if c["f"] == "minimal" else r["payload"]}
            )
    return {
        "schemaVersion": 1, "fields": c["f"], "cursor": encode_cursor(SYNC_PREFIX, {"k": "sync", "e": epoch, "w": next_w, "f": c["f"]}),
        "count": len(page), "hasMore": has_more, "changes": changes,
    }  # fmt: skip
