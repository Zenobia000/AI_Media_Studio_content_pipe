"""The topic directory and a topic's page: port of the pages half of publication/topics.ts. The selected set by topic is read in one pass and
kept a minute (counts may lag by that much; the rows of a page are checked again when read). The open edition installs no module parts, so a
page carries no module output and a company's mark is its monogram."""

from __future__ import annotations

import math
from datetime import datetime

import regex

from newsroom.engine.lib.cache import cached
from newsroom.engine.publication.items import ITEM_COLUMNS, ITEM_FROM, to_feed_item_summary
from newsroom.engine.publication.scope import (
    listed_condition,
    own_fact_evidence_condition,
    scope_params,
    seated_condition,
    selected_condition,
)
from newsroom.engine.publication.topics import (
    find_topic,
    topic_groups,
    topic_match,
    topic_membership,
    topic_membership_params,
    topics,
)
from newsroom.engine.publication.v1 import iso

DAY_MS = 86400_000
RECENT_DAYS = 30
TOPIC_PAGE_SIZE = 20
LATEST_CANDIDATES = 3


def in_topic(t: dict) -> tuple[str, dict]:
    """Report `p` is in topic `t`; the tag overlap comes first, for the tags index."""
    return topic_match("%(t_tags)s::text[]", "%(t_pattern)s::text"), {"t_tags": t["tags"], "t_pattern": t["pattern"]}


def read_seats(conn, now: datetime, ids: list[str] | None = None, subset: list[dict] | None = None) -> list[dict]:
    """The full index or a bounded set of its candidates, with their current content and membership."""
    extra = "AND p.article_id = ANY(%(ids)s::text[])" if ids else ""
    params = {**scope_params(now), **topic_membership_params(subset)}
    if ids:
        params["ids"] = list(dict.fromkeys(ids))
    return conn.execute(
        f"""WITH seats AS (
              SELECT p.article_id AS id, p.revision, p.timeline_at AS at, p.visible_after AS released, p.title, p.score, (s.tier = 'T1') AS first_party,
                p.category, p.original_title, p.published_at, p.tags, p.fact_id,
                f.subject AS fact_subject, f.action AS fact_action, f.occurred_at AS fact_occurred_at, a.output->>'scope' AS scope,
                st.public_id::text AS story, {topic_membership()} AS topics
              FROM publications p JOIN sources s ON s.id = p.source_id
              LEFT JOIN facts f ON f.id = p.fact_id
              LEFT JOIN analyses a ON a.id = p.analysis_id
              LEFT JOIN stories st ON st.id = p.story_id AND st.merged_into IS NULL
              WHERE {seated_condition()} {extra}
            ), dates AS (
              SELECT p.fact_id, min(coalesce(p.published_at, p.timeline_at)) AS fact_published_at
              FROM publications p WHERE p.fact_id IN (SELECT fact_id FROM seats WHERE fact_id IS NOT NULL)
                AND {selected_condition()} AND {own_fact_evidence_condition()}
              GROUP BY p.fact_id
            )
            SELECT seats.*, dates.fact_published_at FROM seats LEFT JOIN dates ON dates.fact_id = seats.fact_id
            ORDER BY seats.at DESC, seats.id DESC""",
        params,
    ).fetchall()


_indexes: dict[int, object] = {}


def _read_index(pool, now: datetime) -> dict:
    with pool.connection() as conn:
        members = [s for s in read_seats(conn, now) if s["topics"]]
    by_slug: dict[str, list[dict]] = {t["slug"]: [] for t in topics()}
    for seat in members:
        for slug in seat["topics"]:
            if slug in by_slug:
                by_slug[slug].append(seat)
    return {"at": now, "bySlug": by_slug}


def topic_index(pool, now: datetime | None = None) -> dict:
    """A given `now` reads afresh (tests, a clock other than this minute's)."""
    if now:
        return _read_index(pool, now)
    entry = _indexes.get(id(pool))
    if entry is None:
        entry = _indexes[id(pool)] = cached(lambda _=None: _read_index(pool, datetime.now().astimezone()), 60_000, 60_000)
    return entry.get()  # type: ignore[attr-defined]


def company_brands() -> dict[str, dict]:
    """A company's initial, drawn as its mark."""
    return {
        t["slug"]: {"src": None, "monogram": regex.sub(r"[^\p{L}\p{N}]", "", t["name"])[:1].upper(), "raster": False}
        for t in topics()
        if t["group"] == "company"
    }


def _recent_count(seats: list[dict], now: datetime) -> int:
    floor = now.timestamp() * 1000 - RECENT_DAYS * DAY_MS
    return sum(1 for s in seats if s["at"].timestamp() * 1000 > floor)


def is_indexable(total: int, recent: int) -> bool:
    """Thin topics keep their page but stay out of the sitemap and search engines."""
    return total >= 50 or (total >= 20 and recent > 0)


def summarize(t: dict, seats: list[dict], now: datetime, brands: dict[str, dict], live: dict[str, dict]) -> dict:
    recent = _recent_count(seats, now)
    latest = next((s for s in (live.get(x["id"]) for x in seats[:LATEST_CANDIDATES]) if s and t["slug"] in s["topics"]), None)
    return {
        "slug": t["slug"], "name": t["name"], "group": t["group"], "definition": t["definition"], "brand": brands.get(t["slug"]),
        "total": len(seats), "recent": recent, "indexable": is_indexable(len(seats), recent),
        "latest": {"title": latest["title"], "at": iso(latest["at"])} if latest else None,
    }  # fmt: skip


def list_topic_summaries(pool) -> dict:
    index = topic_index(pool)
    brands = company_brands()
    ids = [s["id"] for t in topics() for s in index["bySlug"][t["slug"]][:LATEST_CANDIDATES]]
    with pool.connection() as conn:
        live = {s["id"]: s for s in read_seats(conn, datetime.now().astimezone(), ids)} if ids else {}
    return {"groups": topic_groups(), "topics": [summarize(t, index["bySlug"][t["slug"]], index["at"], brands, live) for t in topics()]}


def topic_page_counts(pool, now: datetime | None = None) -> list[dict]:
    """For discovery: every topic's pages and whether they are indexed."""
    index = topic_index(pool, now)
    out = []
    for t in topics():
        seats = index["bySlug"][t["slug"]]
        out.append({
            "slug": t["slug"], "pages": max(1, math.ceil(len(seats) / TOPIC_PAGE_SIZE)),
            "indexable": is_indexable(len(seats), _recent_count(seats, index["at"])),
        })  # fmt: skip
    return out


_pool_counts: dict[tuple[int, str], object] = {}


def pool_total(pool, t: dict, now: datetime | None) -> int:
    """Every listed report of the topic, selected or not."""

    def read(at: datetime) -> int:
        clause, extra = in_topic(t)
        with pool.connection() as conn:
            return conn.execute(
                f"SELECT count(*)::int AS n FROM publications p WHERE {listed_condition()} AND {clause}", {**scope_params(at), **extra}
            ).fetchone()["n"]

    if now:
        return read(now)
    key = (id(pool), t["slug"])
    entry = _pool_counts.get(key)
    if entry is None:
        entry = _pool_counts[key] = cached(lambda _=None: read(datetime.now().astimezone()), 60_000, 60_000)
    return entry.get()  # type: ignore[attr-defined]


def load_topic_page(pool, slug: str, page: float, now: datetime | None = None) -> dict | None:
    topic = find_topic(slug)
    if not topic or page != page or not float(page).is_integer() or page < 1:
        return None
    page = int(page)
    index = topic_index(pool, now)
    seats = index["bySlug"][slug]
    page_count = max(1, math.ceil(len(seats) / TOPIC_PAGE_SIZE))
    if page > page_count:
        return None
    ids = [s["id"] for s in seats[(page - 1) * TOPIC_PAGE_SIZE : page * TOPIC_PAGE_SIZE]]
    at = now or datetime.now().astimezone()
    recheck = [s["id"] for s in seats[:LATEST_CANDIDATES]]
    clause, extra = in_topic(topic)
    with pool.connection() as conn:
        rows = (
            conn.execute(
                f"""SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE p.article_id = ANY(%(ids)s::text[]) AND {seated_condition()} AND {clause}
                    ORDER BY p.timeline_at DESC, p.article_id DESC""",
                {**scope_params(at), **extra, "ids": ids},
            ).fetchall()
            if ids
            else []
        )
        live = {s["id"]: s for s in read_seats(conn, at, recheck, [topic])} if recheck else {}
    group_name = next((g["name"] for g in topic_groups() if g["key"] == topic["group"]), "")
    summary = summarize(topic, seats, index["at"], company_brands(), live)
    return {
        "topic": {**summary, "groupName": group_name, "poolTotal": pool_total(pool, topic, now)},
        "modules": {},
        "items": [to_feed_item_summary(r) for r in rows],
        "page": page,
        "pageCount": page_count,
        "pageSize": TOPIC_PAGE_SIZE,
    }
