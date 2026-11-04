"""Tag normalization and company identity over the industry pack's vocabulary. Port of editorial/vocabulary.ts."""

from __future__ import annotations

import re
import unicodedata

from newsroom.engine.pack import load


def _t() -> dict:
    return load("taxonomy")


def category_tags() -> list[str]:
    return _t()["CATEGORY_TAGS"]


def topic_tags() -> list[str]:
    return _t()["TOPIC_TAGS"]


def entity_tags() -> list[str]:
    return _t()["ENTITY_TAGS"]


def entities() -> dict:
    return _t()["ENTITIES"]


def item_types() -> list[str]:
    return _t()["ITEM_TYPES"]


def _allowed_tags() -> set[str]:
    return {*category_tags(), *topic_tags(), *entity_tags()}


_WS = " \t\n\r\v\f                 　﻿"


def normalize_tags(v) -> list[str]:
    """Known tags only, synonyms mapped, duplicates dropped, at most six; the category tag goes first, and a list without one gets the pack's last category
    tag (其他)."""
    raw = v if isinstance(v, list) else re.split(r"[,，]", v) if isinstance(v, str) else []
    synonyms = _t()["TAG_SYNONYMS"]
    allowed = _allowed_tags()
    tags: list[str] = []
    for x in raw:
        t = re.sub(r"^#", "", ("" if x is None else str(x)).strip(_WS), count=1)
        tag = synonyms.get(t) or synonyms.get(t.lower()) or t
        if tag and tag in allowed and tag not in tags:
            tags.append(tag)
    cats = category_tags()
    category = next((t for t in tags if t in cats), cats[-1])
    return [category, *[t for t in tags if t != category]][:6]


def category_guide() -> str:
    """The category boundaries the structure step reads, one line per category."""
    return "\n".join(f"- {c['key']}（{c['label']}）：{c['guide']}" for c in _t()["CATEGORIES"])


def is_release(category: str | None, tags: list[str]) -> bool:
    release = _t().get("RELEASE")
    return release is not None and category == release["category"] and release["tag"] in tags


def entity_names(e: dict) -> list[str]:
    return [e["name"], *e.get("aliases", []), *(e.get("orgNames") or []), *(e.get("otherNames") or [])]


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).strip().lower())


def entity_identity(value: str | None) -> str | None:
    """Exact entity/alias only: mentions embedded in a sentence are not ownership."""
    if not value or not value.strip():
        return None
    normalized = _key(value)
    matches = [eid for eid, e in entities().items() if any(_key(a) == normalized for a in [eid, *entity_names(e)])]
    return None if len(matches) > 1 else matches[0] if matches else None
