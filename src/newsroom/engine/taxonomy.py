"""Public vocabularies shared by the website, the API and the worker: port of packages/contracts/src/taxonomy.ts. The categories belong to the
industry pack; their keys are external identities (URLs, API, RSS) and never change. How the public API, RSS and MCP differ from the
website is the site's (PUBLIC_CATEGORIES)."""

from __future__ import annotations

from newsroom.engine import pack

CHANNEL_KEYS = ["all", "news", "x", "firstParty"]
CHANNEL_LABELS = {"all": "全部", "news": "資訊", "x": "X", "firstParty": "一手"}
ARTICLE_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,80}$"  # also the local-data import validation pattern


def categories() -> list[dict]:
    return pack.load("taxonomy")["CATEGORIES"]


def category_keys() -> list[str]:
    return [c["key"] for c in categories()]


def category_labels() -> dict[str, str]:
    return {c["key"]: c["label"] for c in categories()}


def _merge() -> dict[str, str]:
    return pack.site()["PUBLIC_CATEGORIES"]["merge"]


def public_api_category_keys() -> list[str]:
    """The categories the public API, RSS and MCP know: a merged category is published as the one it joins."""
    return [c["key"] for c in categories() if c["key"] not in _merge()]


def to_public_api_category(category: str | None) -> str | None:
    c = next((x for x in categories() if x["key"] == category), None)
    if not c:
        return None
    return _merge().get(c["key"], c["key"])


def feed_category_label(key: str) -> str:
    """A public category's name in feeds: the site's, else the pack's feed label, else its website label."""
    c = next(x for x in categories() if x["key"] == key)
    return pack.site()["PUBLIC_CATEGORIES"]["feedLabels"].get(key) or c.get("feedLabel") or c["label"]


def is_category_key(value) -> bool:
    return isinstance(value, str) and value in category_keys()


def is_channel_key(value) -> bool:
    return isinstance(value, str) and value in CHANNEL_KEYS
