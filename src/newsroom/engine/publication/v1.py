"""v1 items, read from the public read layer: port of publication/v1.ts (items; the selected sync follows) and v1Payload from publish.ts."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from newsroom.engine import pack
from newsroom.engine.http_policy import site_url
from newsroom.engine.lib.cache import shared_search
from newsroom.engine.lib.cursor import InvalidCursorError, decode_cursor, encode_cursor, query_binding
from newsroom.engine.lib.ids import stable_json
from newsroom.engine.publication.pool import public_match, search_terms, with_search_capacity
from newsroom.engine.publication.scope import browsed_condition, listed_condition, scope_params, seated_condition
from newsroom.engine.taxonomy import category_keys, to_public_api_category

API_ITEM_COLUMNS = """p.article_id AS id, p.title, p.original_title, p.summary, s.name AS source_name, p.url,
  p.published_at, p.discovered_at, p.category, p.score, (p.selected AND p.seat) AS selected, p.reason"""
API_ITEM_FROM = "FROM publications p JOIN sources s ON s.id = p.source_id"


def iso(d: datetime) -> str:
    """Date.prototype.toISOString: UTC, milliseconds, Z."""
    d = d.astimezone(UTC)
    return f"{d.strftime('%Y-%m-%dT%H:%M:%S')}.{d.microsecond // 1000:03d}Z"


def epoch_ms(d: datetime) -> int:
    return (d - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)


def js_round(x: float) -> int:
    """Math.round: halves go up."""
    return int(x + 0.5) if x >= 0 else -int(-x + 0.5)


def item_url(article_id: str) -> str:
    return site_url(f"/items/{article_id}")


def v1_payload(p: dict) -> dict:
    aihot = item_url(p["articleId"])
    return {
        "id": p["articleId"],
        "title": p["title"],
        "originalTitle": p["originalTitle"],
        "summary": p["summary"],
        "source": {"name": p["sourceName"]},
        "links": {"aihot": aihot, "original": p["url"]},
        "publishedAt": iso(p["publishedAt"]) if p["publishedAt"] else None,
        "discoveredAt": iso(p["discoveredAt"]),
        "category": to_public_api_category(p["category"]),
        "score": None if p["score"] is None else js_round(p["score"]),
        "selected": p["selected"],
        "reason": p["reason"] if p["selected"] else None,
        "attribution": {"name": pack.site()["SITE"]["name"], "url": aihot},
    }


def row_to_v1(row: dict) -> dict:
    return v1_payload({
        "articleId": row["id"], "title": row["title"], "originalTitle": row["original_title"], "summary": row["summary"], "sourceName": row["source_name"],
        "url": row["url"], "publishedAt": row["published_at"], "discoveredAt": row["discovered_at"], "category": row["category"],
        "score": None if row["score"] is None else float(row["score"]), "selected": row["selected"], "reason": row["reason"],
    })  # fmt: skip


def public_category_condition(category: str | None) -> tuple[str, dict]:
    """The public API's, RSS's and MCP's filter: every category the site publishes as this one (PUBLIC_CATEGORIES)."""
    if not category:
        return "", {}
    return "AND p.category = ANY(%(category_keys)s)", {"category_keys": [k for k in category_keys() if to_public_api_category(k) == category]}


@dataclass(frozen=True)
class V1ItemsQuery:
    mode: str  # selected | all
    window: str  # 24h | 7d
    by: str  # timeline | published
    category: str | None
    q: str | None
    limit: int
    cursor: str | None


_ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


def query_items(pool, query: V1ItemsQuery, now: datetime) -> dict:
    window = timedelta(days=1) if query.window == "24h" else timedelta(days=7)
    window_start = now - window
    binding = query_binding({"m": query.mode, "w": query.window, "b": query.by, "c": query.category, "q": query.q})
    # by=timeline is the site's own order: a selected item at its reading-group anchor, anything else at its timeline time.
    if query.by == "published":
        sort_col = "coalesce(p.published_at, p.discovered_at)"
    else:
        sort_col = "p.sort_at" if query.mode == "selected" else "p.timeline_at"
    after = None
    if query.cursor:
        c = decode_cursor("it3", query.cursor)
        a, i = c.get("a"), c.get("i")
        if c.get("c") != binding or not isinstance(a, int) or isinstance(a, bool) or not isinstance(i, str) or not _ID.match(i):
            raise InvalidCursorError("cursor does not belong to this query")
        if a < epoch_ms(window_start):  # the rolling window slid past the anchor: tell the client instead of returning an empty page
            raise InvalidCursorError("the rolling window moved past this cursor")
        after = (a, i)
    scope = seated_condition() if query.mode == "selected" else listed_condition() if query.q else browsed_condition()
    terms = search_terms(query.q) if query.q else []
    category_sql, category_params = public_category_condition(query.category)
    match_sql, match_params = public_match(terms)
    params = {**scope_params(now), **category_params, **match_params, "window_start": window_start, "limit": query.limit + 1}
    after_sql = ""
    if after:
        after_sql = f"AND ({sort_col}, p.article_id) < (%(after_at)s, %(after_id)s)"
        params |= {"after_at": datetime.fromtimestamp(after[0] / 1000, UTC), "after_id": after[1]}
    text = f"""SELECT {API_ITEM_COLUMNS}, {sort_col} AS sort_at {API_ITEM_FROM}
      WHERE {scope} {category_sql} {match_sql}
        AND {sort_col} >= %(window_start)s AND {sort_col} <= %(now)s {after_sql}
      ORDER BY {sort_col} DESC, p.article_id DESC LIMIT %(limit)s"""

    def run(conn):
        return conn.execute(text, params).fetchall()

    with pool.connection() as conn:
        rows = with_search_capacity(conn, run) if terms else run(conn)
    page = rows[: query.limit]
    has_more = len(rows) > query.limit
    last = page[-1] if page else None
    return {
        "schemaVersion": 1,
        "query": {
            "mode": query.mode,
            "category": query.category,
            "window": query.window,
            "q": query.q,
            "by": query.by,
            "ordering": "publishedAtDesc" if query.by == "published" else "timelineDesc",
        },  # fmt: skip
        "items": [row_to_v1(r) for r in page],
        "page": {
            "count": len(page),
            "hasMore": has_more,
            "nextCursor": encode_cursor("it3", {"a": epoch_ms(last["sort_at"]), "i": last["id"], "c": binding}) if has_more and last else None,
        },  # fmt: skip
    }


_searches: dict[int, Callable] = {}


def v1_items(pool, query: V1ItemsQuery, now: datetime | None = None) -> dict:
    """Search callers across HTTP and tools share one read (per connection pool)."""
    search = _searches.get(id(pool))
    if search is None:
        search = _searches[id(pool)] = shared_search(
            lambda q: stable_json([q.mode, q.window, q.by, q.category, q.q, q.limit, q.cursor]),
            lambda q, at: query_items(pool, q, at),
            lambda q: bool((q.q or "").strip()),
        )
    return search(query, now)
