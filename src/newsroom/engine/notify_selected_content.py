"""The current selected card and its eligibility, shared by initial delivery, mirrors and recovery. Port of notify/selected-content.ts."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from newsroom.engine import pack
from newsroom.engine import taxonomy as T
from newsroom.engine.publication.stories import item_url, public_source_name
from newsroom.engine.time import beijing_day

MAX_AGE_SECONDS = 12 * 3600
PUSH_TIERS = ("T1", "T1_5")  # content groups get first-party (T1) and near-first-party (T1_5) sources only


def _published(at: datetime) -> str:
    """The original's publication date, so a launch introduced again weeks later does not read as today's."""
    return f"{beijing_day(at)}發布"


def _literal(text: str) -> str:
    """Model-written words go into card markdown: its markup characters are shown as written."""
    return re.sub(r"[&<>*_~`\[\]]", lambda m: f"&#{ord(m.group(0))};", text)


def _link(label: str, url: str, kind: str) -> dict:
    return {
        "tag": "column", "width": "auto",
        "elements": [{"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind, "behaviors": [{"type": "open_url", "default_url": url}]}],
    }  # fmt: skip


def card(r: dict) -> dict:
    """Card JSON 2.0: a header title has one fixed size, so the title is the first body line, a size above the summary."""
    category = next((c for c in T.categories() if c["key"] == r["category"]), None)
    tag = f" <text_tag color='{category['tagColor']}'>{category['label']}</text_tag>" if category else ""
    site = pack.site()
    return {
        "schema": "2.0",
        "config": {"style": {"text_size": {"title": {"default": "heading-3", "pc": "heading-3", "mobile": "heading-4"}}}},
        "body": {"elements": [
            {"tag": "markdown", "text_size": "title", "content": f"**{_literal(r['title'])}**{tag}"},
            {"tag": "markdown", "text_size": "notation", "content": f"<font color='grey'>{_literal(public_source_name(r['source_name']))} · {_published(r['published_at'])}</font>"},
            {"tag": "markdown", "content": _literal(r["summary"] or "")},
            *([{"tag": "markdown", "text_size": "notation", "content": f"<font color='grey'>**{site['ITEM_COPY']['reasonLabel']}**　{_literal(r['reason'])}</font>"}] if r["reason"] else []),
            {"tag": "column_set", "horizontal_spacing": "8px", "columns": [_link(f"在 {site['SITE']['name']} 閱讀", item_url(r["article_id"]), "primary"), _link("原文", r["url"], "default")]},
        ]},
    }  # fmt: skip


def selected_content(pool, article_id: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with pool.connection() as conn:
        r = conn.execute(
            """SELECT p.article_id, p.selected, p.visibility, p.title, p.summary, p.reason, p.category, s.name AS source_name, s.tier AS source_tier, p.url,
                      p.timeline_at, p.published_at, p.discovered_at, p.backfill, p.fact_id, coalesce((o.fields->>'silent')::boolean, false) AS silent
               FROM publications p JOIN sources s ON s.id = p.source_id LEFT JOIN editorial_overrides o ON o.article_id = p.article_id
               WHERE p.article_id = %s""",
            (article_id,),
        ).fetchone()
    if not r or not r["selected"] or r["visibility"] != "public":
        return {"status": "skipped", "reason": "not public selected"}
    if r["silent"]:
        return {"status": "skipped", "reason": "silenced"}
    if not r["published_at"]:
        return {"status": "skipped", "reason": "unknown publication time"}
    if r["source_tier"] not in PUSH_TIERS:
        return {"status": "skipped", "reason": "not a first-party source"}
    if r["backfill"] or (now - r["timeline_at"]).total_seconds() > MAX_AGE_SECONDS:
        return {"status": "skipped", "reason": "not live"}
    return {"status": "ready", "article": r, "card": card(r)}
