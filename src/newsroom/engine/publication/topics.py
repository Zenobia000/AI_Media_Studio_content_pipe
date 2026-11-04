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
