"""Public read layer, item level: port of publication/items.ts (the site's card shapes) and the small pieces of content/posts.ts, rules.ts and
sources/account.ts they use. Every exit reads items through these columns and views; which rows are public is decided by scope.py."""

from __future__ import annotations

import re
from datetime import datetime

from newsroom.engine import pack
from newsroom.engine.media.imgproxy import proxied_image, proxied_image_set
from newsroom.engine.publication.scope import foldable_selection_condition, scope_params, seated_condition
from newsroom.engine.publication.stories import public_source_name
from newsroom.engine.publication.v1 import iso, js_round
from newsroom.engine.traditional import plain_for_display

X_HANDLE = r"from:([A-Za-z0-9_]{1,15})"


def source_owns_post(config: str, handle: str) -> str:
    """In SQL: a post is the source's own only when its author is the account the source reads (handles ignore case)."""
    return f"lower((regexp_match({config}->>'query', '{X_HANDLE}', 'i'))[1]) = lower(nullif({handle}, ''))"


ITEM_COLUMNS = f"""
  p.article_id AS id, p.title, p.original_title, p.summary, p.reason, p.category, p.tags, p.score,
  p.selected, p.seat, p.channel, p.url, p.published_at, p.discovered_at, p.timeline_at, p.visibility,
  p.body_mode, p.indexable, p.fact_id, s.name AS source_name, s.participation_mode AS source_mode,
  a.x_post, CASE WHEN p.body_mode = 'full' THEN a.x_article END AS x_article, a.author, a.language,
  CASE WHEN s.kind = 'x_search' AND {source_owns_post("s.config", "a.x_post->>'handle'")} THEN s.icon_url END AS author_avatar_url,
  st.public_id::text AS story_public_id, st.title AS story_title,
  CASE WHEN p.channel = 'x' THEN tr.body_text END AS zh_text, qt.text_zh AS quoted_zh"""

# A translation of an older revision is left out: the original changed after it (the worker translates it again).
ITEM_FROM = """
  FROM publications p
  JOIN sources s ON s.id = p.source_id
  JOIN articles a ON a.id = p.article_id
  LEFT JOIN stories st ON st.id = p.story_id AND st.merged_into IS NULL
  LEFT JOIN translations tr ON tr.article_id = p.article_id AND tr.lang = 'zh' AND tr.revision >= a.revision
  LEFT JOIN quote_translations qt ON p.channel = 'x' AND qt.tweet_id = substring(a.x_post->'quoted'->>'url' from '/status/([0-9]+)')"""


def channel_condition(channel: str | None) -> str:
    if not channel or channel == "all":
        return ""
    if channel == "firstParty":
        return "AND p.source_id IN (SELECT id FROM sources WHERE tier = 'T1')"
    return f"AND p.channel = '{channel}'" if channel in ("news", "x") else "AND FALSE"


def category_condition(category: str | None) -> tuple[str, dict]:
    """The website's filter: one of its own categories."""
    return ("AND p.category = %(category)s", {"category": category}) if category else ("", {})


def tag_condition(tag: str | None) -> tuple[str, dict]:
    return ("AND p.tags @> %(tag)s::text[]", {"tag": [tag]}) if tag else ("", {})


def filter_sql(channel: str | None, category: str | None, tag: str | None) -> tuple[str, dict]:
    c, cp = category_condition(category)
    t, tp = tag_condition(tag)
    return f"{channel_condition(channel)} {c} {t}", {**cp, **tp}


def display_tags(tags: list[str]) -> list[str]:
    """Display tags exclude internal entity markers."""
    return [t for t in tags if not t.startswith("entity:")]


def is_empty_or_link_only(text: str) -> bool:
    return not re.sub(r"https?://\S+", "", text, flags=re.I).strip()


def with_x_article(x_post: dict | None, article: dict | None) -> dict | None:
    """The post and its already fetched long article are one text for writing and public reading."""
    if not x_post or not article or not article.get("text"):
        return x_post
    parts = [
        str(x_post.get("text") if x_post.get("text") is not None else "").strip(),
        f"【X 長文】{article['title']}" if article.get("title") else "【X 長文】",
        article["text"],
    ]
    return {**x_post, "text": "\n\n".join(p for p in parts if p)}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def media_view(m: dict, mode: str = "thumb", responsive: bool = False) -> dict | None:
    url = proxied_image(m.get("url"), mode)
    if not url:
        return None
    # `m.poster ?? m.url`
    src_set = (
        proxied_image_set(m["poster"] if m.get("poster") is not None else m.get("url"), "body" if mode == "full" else "card") if responsive else None
    )
    out: dict = {"kind": "video" if m.get("kind") == "video" else "image", "url": url}
    if responsive and mode != "full":
        out["fullUrl"] = proxied_image(m.get("url"), "full")
    if src_set:
        out["srcSet"] = src_set
    out |= {"width": _num(m.get("width")), "height": _num(m.get("height")), "alt": m.get("alt") if m.get("alt") is not None else None,
            "poster": proxied_image(m["poster"], "card" if mode == "card" else "thumb") if m.get("poster") else None}  # fmt: skip
    return out


def _s(v) -> str:
    return str(v) if v is not None else ""


def x_view(row: dict, compact: bool = False, responsive: bool | None = None) -> dict | None:
    responsive = compact if responsive is None else responsive
    x = with_x_article(row.get("x_post"), row.get("x_article"))
    if not x:
        return None
    quoted = None
    q = x.get("quoted")
    if isinstance(q, dict):
        q_text = _s(q.get("text"))
        quoted_zh = row.get("quoted_zh")
        quoted = {
            "authorName": _s(q.get("authorName")), "handle": _s(q.get("handle")), "text": q_text, "url": _s(q.get("url")),
            "translation": quoted_zh if (not is_empty_or_link_only(q_text) and quoted_zh and quoted_zh.strip() != q_text.strip()) else None,
        }  # fmt: skip
    entries = []
    for raw in x.get("media") or []:
        view = media_view(raw, "thumb" if compact or not responsive else "full", responsive)
        if view is not None:
            entries.append((raw, view))
    avatar = row.get("author_avatar_url") or x.get("avatarUrl")
    avatar_set = proxied_image_set(avatar, "avatar") if responsive else None
    text = _s(x.get("text"))
    zh = row.get("zh_text")
    own_article = bool((row.get("x_article") or {}).get("text"))
    out: dict = {
        "authorName": _s(x.get("authorName") or x.get("handle")),
        "handle": _s(x.get("handle")),
        "avatarUrl": proxied_image(avatar, "avatar"),
    }
    if avatar_set:
        out["avatarSrcSet"] = avatar_set
    out["text"] = text
    out["translation"] = zh if (not own_article and not is_empty_or_link_only(text) and zh and zh.strip() != text.strip()) else None
    out["quoted"] = quoted
    out["media"] = [media_view(raw, "card", responsive) if compact and len(entries) > 1 else view for raw, view in entries]
    return out


def shows_post(row: dict) -> bool:
    """Whether pages show an X post's own text and media. A site that counts them as full text shows them only where the source allows full text."""
    return row["channel"] == "x" and (not pack.site()["POLICY"].get("xPostIsFullText") or row["body_mode"] == "full")


def to_item_summary(row: dict) -> dict:
    """The shared public article; its X post is added as each answer shows it."""
    return {
        "id": row["id"], "title": plain_for_display(row["title"]), "originalTitle": row["original_title"], "summary": plain_for_display(row["summary"]),
        "reason": plain_for_display(row["reason"]) if row["selected"] else None, "source": {"name": public_source_name(row["source_name"])}, "links": {"original": row["url"]},
        "publishedAt": iso(row["published_at"]) if row["published_at"] else None, "discoveredAt": iso(row["discovered_at"]), "timelineAt": iso(row["timeline_at"]),
        "category": row["category"], "tags": display_tags(row["tags"]), "score": None if row["score"] is None else js_round(float(row["score"])),
        "selected": row["selected"], "channel": row["channel"],
        "story": {"publicId": row["story_public_id"], "title": plain_for_display(row["story_title"] or "")} if row["story_public_id"] else None,
    }  # fmt: skip


def to_feed_item_summary(row: dict) -> dict:
    """Project the shared public article into the exact fields a site card renders."""
    item = to_item_summary(row)
    x = x_view(row, True) if shows_post(row) else None
    xo = None
    if x:
        xo = {"authorName": x["authorName"], "handle": x["handle"], "avatarUrl": x["avatarUrl"]}
        if x.get("avatarSrcSet"):
            xo["avatarSrcSet"] = x["avatarSrcSet"]
        xo["media"] = x["media"]
        xo["quoted"] = ({"authorName": x["quoted"]["authorName"], "handle": x["quoted"]["handle"], "text": x["quoted"]["text"], "translation": x["quoted"]["translation"]}
                        if x["quoted"] else None)  # fmt: skip
    return {
        "id": item["id"], "title": item["title"], "summary": item["summary"] if item["summary"] is not None else ((x or {}).get("text") or None), "reason": item["reason"],
        "source": item["source"], "publishedAt": item["publishedAt"], "timelineAt": item["timelineAt"], "category": item["category"], "tags": item["tags"],
        "score": item["score"], "selected": item["selected"], "channel": item["channel"], "x": xo,
    }  # fmt: skip


def seat_holders(conn, rows: list[dict], now: datetime) -> dict[str, dict]:
    """For listed rows that are selected but yield their fact's seat: the report holding it, by row id. (The home timeline folds these into
    reading groups; flat lists say which report stands for them.)"""
    yielding = [r for r in rows if r["selected"] and not r["seat"] and r["fact_id"] is not None]
    if not yielding:
        return {}
    holders = conn.execute(
        f"SELECT p.fact_id, p.article_id AS id, p.title FROM publications p WHERE p.fact_id = ANY(%(facts)s) AND {seated_condition()} AND {foldable_selection_condition()}",
        {**scope_params(now), "facts": sorted({r["fact_id"] for r in yielding})},
    ).fetchall()
    by_fact = {h["fact_id"]: {"id": h["id"], "title": h["title"]} for h in holders}
    return {r["id"]: by_fact[r["fact_id"]] for r in yielding if r["fact_id"] in by_fact}
