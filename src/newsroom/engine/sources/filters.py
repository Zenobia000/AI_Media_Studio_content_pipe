"""Source-list admission rules shared by collection and the administrator's preview. Port of sources/filters.ts (and `allowed` from web-list.ts)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from newsroom.engine.materials import FUTURE_TOLERANCE


def _over_https(url: str) -> str:
    return re.sub(r"^http://", "https://", url, flags=re.I)


def allowed(url: str, source: dict) -> bool:
    cfg = source["config"]
    allow = [_over_https(p) for p in cfg.get("allowUrlPrefixes") or []]
    deny = [_over_https(p) for p in cfg.get("denyUrlPrefixes") or []]
    target = _over_https(url)
    if any(target.startswith(p) for p in deny):
        return False
    return not allow or any(target.startswith(p) for p in allow)


def admit_listing(candidates: list[dict], source: dict) -> list[dict]:
    """What a source keeps from its listing: links inside its URL prefix rules (matched as listed), then moved by its URL rewrite, without its
    denied categories and noise. Publication admission happens after article-page enrichment."""
    kept = [c for c in candidates if allowed(c["url"], source)]
    return [c for c in (_rewrite_url(c, source) for c in kept) if not noise_filtered(c, source)]


def noise_filtered(c: dict, source: dict) -> bool:
    cfg = source["config"]
    f = cfg.get("ingestNoiseFilter")
    cats = c.get("categories") or []
    if any(d in cats for d in cfg.get("denyCategories") or []):
        return True
    if cfg.get("allowCategories") and not any(a in cats for a in cfg["allowCategories"]):
        return True
    if not f:
        return False

    # Case-insensitive: the exemption "agent" keeps "Agent" (words in the lists are lower case).
    def has(text: str, words: list[str] | None) -> bool:
        return any(k.lower() in text for k in words or [])

    title = c["title"].lower()
    hay = f"{title}\n{(c.get('excerpt') or '').lower()}"
    if has(hay, f.get("keepIfMatches")):
        return False
    return has(title, f.get("dropMarkersTitleOnly")) or has(hay, f.get("dropMarkers"))


def _rewrite_url(c: dict, source: dict) -> dict:
    rw = source["config"].get("itemUrlPrefixRewrite")
    if rw and rw.get("from") and rw.get("to") and c["url"].startswith(rw["from"]):
        return {**c, "url": rw["to"] + c["url"][len(rw["from"]) :]}
    return c


def filter_publication_window(candidates: list[dict], published_after: str | None, now: datetime | None = None) -> list[dict]:
    """A fixed publication boundary excludes history and dates that cannot prove an item is in range."""
    if not published_after:
        return candidates
    after = datetime.fromisoformat(published_after.replace("Z", "+00:00"))
    latest = (now or datetime.now(UTC)) + FUTURE_TOLERANCE
    return [c for c in candidates if c.get("published_at") and after < c["published_at"] <= latest]
