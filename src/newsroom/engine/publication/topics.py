"""Topics: stable slugs in three groups (companies, directions, content forms), defined by the industry pack (industry/topics.json, now
engine/pack/topics.json). Port of publication/topics.ts, the definitions first (the pages and counts follow)."""

from __future__ import annotations

import re
from functools import cache

from newsroom.engine import pack
from newsroom.engine.vocabulary import entities, entity_names


def _escape(s: str) -> str:
    """JavaScript's regular-expression escape set (a PostgreSQL pattern reads the result)."""
    return re.sub(r"[.*+?^${}()|\[\]\\]", lambda m: "\\" + m.group(0), s)


def _name_parts(name: str) -> list[str]:
    return [p.strip() for p in name.split("/") if p.strip()]


def _title_pattern(name: str, entity_id: str) -> str:
    """Any name of the company: the topic's name and its parts, the subject's names. Latin names match as whole words."""
    e = entities().get(entity_id)
    names = dict.fromkeys([name, *_name_parts(name), *(entity_names(e) if e else [])])
    return f"(?<![A-Za-z])({'|'.join(_escape(n) for n in names)})(?![A-Za-z])"


@cache
def topic_groups() -> list[dict]:
    return pack.load("topics")["groups"]


@cache
def topics() -> list[dict]:
    out = []
    for t in pack.load("topics")["topics"]:
        entity_id = t.get("entityId")
        out.append({
            "slug": t["slug"], "name": t["name"], "group": t["group"], "definition": t["definition"], "entityId": entity_id,
            "tags": [f"entity:{entity_id}"] if entity_id else t.get("tags") or [],
            "pattern": _title_pattern(t["name"], entity_id) if entity_id else None,
            "aliases": [t["slug"], t["name"], *_name_parts(t["name"]), *(t.get("aliases") or [])] if entity_id else [],
        })  # fmt: skip
    return out


def find_topic(slug: str) -> dict | None:
    return next((t for t in topics() if t["slug"] == slug), None)


def topic_links(slugs: list[str]) -> list[dict]:
    return [{"slug": t["slug"], "name": t["name"]} for t in (find_topic(s) for s in slugs) if t]


def topic_browse_links() -> list[dict]:
    """Stable browse links in the same order as the topic directory, without its counts or modules."""
    return [{"slug": t["slug"], "name": t["name"], "group": t["group"]} for t in topics()]


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def query_entity_tag(q: str) -> str | None:
    """The company a whole query names: a company topic's slug, full name, each part of "OpenAI / ChatGPT" and its other names. Then the search
    also takes the articles about that company (their subject tag). Only the whole query counts."""
    want = _normalize_alias(q)
    if not want:
        return None
    topic = next((t for t in topics() if t["entityId"] and any(_normalize_alias(a) == want for a in t["aliases"])), None)
    return f"entity:{topic['entityId']}" if topic else None


# ── membership ──────────────────────────────────────────────────────────────────────────

STORY_TOPICS = 6


def _position(slug: str) -> int:
    for i, t in enumerate(topics()):
        if t["slug"] == slug:
            return i
    return 2**53 - 1


def topic_match(tags: str, pattern: str) -> str:
    """One membership rule for topic discovery and a single topic's page and count (`%` is doubled: the text carries parameters)."""
    return (
        f"p.tags && {tags} AND ({pattern} IS NULL OR p.title ~* {pattern} OR coalesce(p.original_title, '') ~* {pattern} "
        "OR (SELECT count(*) FROM unnest(p.tags) AS e(tag) WHERE e.tag LIKE 'entity:%%') = 1)"
    )


def topic_membership(param: str = "topic_rows") -> str:
    """The topics report `p` belongs to, in topic order: its tags meet the topic's and, for a company, the title names it or it is the report's
    only subject company. The topics travel as one JSON parameter (`topic_rows`, from `topic_membership_params`)."""
    return f"""ARRAY(
    SELECT t.slug FROM jsonb_to_recordset(%({param})s::jsonb) AS t(slug text, tags text[], pattern text, position int)
    WHERE {topic_match("t.tags", "t.pattern")}
    ORDER BY t.position)"""


def topic_membership_params() -> dict:
    import json  # noqa: PLC0415

    return {
        "topic_rows": json.dumps(
            [{"slug": t["slug"], "tags": t["tags"], "pattern": t["pattern"], "position": _position(t["slug"])} for t in topics()]
        )
    }


def topics_of_story(conn, story_id: int, now) -> list[dict]:
    """The topics of a story's reports, most reports first."""
    from newsroom.engine.publication.scope import evidence_condition, scope_params, story_report_condition  # noqa: PLC0415

    rows = conn.execute(
        f"""SELECT DISTINCT ON (p.article_id) {topic_membership()} AS topics
            FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
            JOIN sources s ON s.id = p.source_id
            WHERE f.story_id = %(story)s AND {evidence_condition()} AND {story_report_condition()} ORDER BY p.article_id""",
        {**scope_params(now), **topic_membership_params(), "story": story_id},
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        for slug in r["topics"]:
            counts[slug] = counts.get(slug, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], _position(kv[0])))
    return topic_links([slug for slug, _ in ordered[:STORY_TOPICS]])
