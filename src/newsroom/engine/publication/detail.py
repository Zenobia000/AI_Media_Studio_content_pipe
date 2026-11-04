"""Item detail: port of the detail half of publication/detail.ts (the Markdown export follows), behind the same visibility and licence rules."""

from __future__ import annotations

import re
from datetime import datetime

from newsroom.engine.content import proxy_body_images, text_to_html
from newsroom.engine.lib.text import is_chinese_text, strip_tag_markup
from newsroom.engine.publication.items import (
    ITEM_COLUMNS,
    ITEM_FROM,
    seat_holders,
    shows_post,
    to_item_summary,
    x_view,
)
from newsroom.engine.publication.scope import evidence_condition, listed_condition, scope_params
from newsroom.engine.publication.topics import topic_links, topic_membership, topic_membership_params
from newsroom.engine.site import has_item_page


def with_outline(html: str) -> tuple[str, list[dict]]:
    """Adds stable ids to h2–h4 and returns the outline."""
    outline: list[dict] = []
    n = 0

    def heading(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        level, inner = m.group(1), m.group(2)
        sec = f"sec-{n}"
        text = strip_tag_markup(inner, "").strip()
        if text:
            outline.append({"id": sec, "text": text[:80], "level": int(level)})
        return f'<h{level} id="{sec}">{inner}</h{level}>'

    return re.sub(r'<h([2-4])(?: id="sec-\d+")?>([\s\S]*?)</h\1>', heading, html, flags=re.I), outline


def load_row(conn, item_id: str) -> dict | None:
    return conn.execute(
        f"""SELECT {ITEM_COLUMNS}, a.body_html, a.body_text, a.body_status, tr.body_html AS tr_html, tr.complete AS tr_complete,
              {topic_membership()} AS topics
            {ITEM_FROM}
            WHERE p.article_id = %(id)s""",
        {**topic_membership_params(), "id": item_id},
    ).fetchone()


def is_chinese_body(a: dict) -> bool:
    """Every exit uses the same language decision as writing and translation."""
    return is_chinese_text(a.get("body_text") or "", a.get("language"))


def export_translation(a: dict) -> str | None:
    """The complete Chinese translation an export (Markdown, full RSS) carries; a page also shows a partial one."""
    return a["tr_html"] if not is_chinese_body(a) and a.get("tr_html") and a.get("tr_complete") else None


def reading_body(language: str, zh: dict | None, original: str | None, complete: bool, page) -> dict:
    """The body a page reads in `language` when it has it, else in the other: Chinese is the article's own text or a translation (shown even
    before it is complete), the original only the article's own. `page` turns the HTML shown into what is sent, with its outline."""
    body_language = "original" if language == "original" and original else ("zh" if zh and zh["html"] else "original")
    shown = zh["html"] if body_language == "zh" else original
    html, outline = page(shown) if shown else (shown, [])
    return {
        "body": {"zh": html if body_language == "zh" else None, "original": html if body_language == "original" else None,
                 "zhKind": zh["kind"] if zh else None, "complete": complete},
        "outline": outline,
        "hasTranslation": bool(zh and zh["kind"] == "translation" and zh["html"] and original),
        "bodyLanguage": body_language,
    }  # fmt: skip


def markdown_available(row: dict) -> bool:
    """Same predicate for the export button and the export route: a public page with something to export (a summary, the post where pages
    show it, or a full-text body)."""
    if row["visibility"] != "public" or not has_item_page(row["visibility"], row["source_mode"]):
        return False
    post = row.get("x_post") or {}
    has_post = shows_post(row) and bool(post.get("text") or post.get("media") or (post.get("quoted") or {}).get("text"))
    return bool(row["summary"]) or has_post or (row["body_mode"] == "full" and bool(row.get("body_html")))


def load_item_detail(pool, item_id: str, language: str = "zh", now: datetime | None = None) -> dict | None:
    """Public detail in one language, Chinese unless the original is asked for: items the lists leave out (low relevance, merged duplicates, no
    Chinese summary yet) keep a noindex page; withdrawn and hot_signal items are a 404. Returns the item and its row."""
    now = now or datetime.now().astimezone()
    with pool.connection() as conn:
        row = load_row(conn, item_id)
        if not row or not has_item_page(row["visibility"], row["source_mode"]):
            return None
        summary = to_item_summary(row)
        if row["visibility"] == "summary-only":
            return {"item": {**summary, "selected": False, "score": None, "category": None, "story": None, "reason": None, "tags": [], "x": None,
                             "readingMode": "summary-only", "author": None, "body": None, "outline": [], "relatedStories": [], "topics": [],
                             "indexable": False, "markdownAvailable": False, "group": None, "hasTranslation": False, "bodyLanguage": "original"},
                    "row": row}  # fmt: skip
        related = conn.execute(
            """SELECT DISTINCT st.public_id::text AS "publicId", st.title
               FROM fact_articles fa JOIN facts f ON f.id = fa.fact_id JOIN stories st ON st.id = f.story_id
               WHERE fa.article_id = %s AND fa.role <> 'mention' AND st.merged_into IS NULL
               ORDER BY "publicId" LIMIT 6""",
            (item_id,),
        ).fetchall()
        x = None
        reading = {"body": None, "outline": [], "hasTranslation": False, "bodyLanguage": "original"}
        x_post = row.get("x_post") or {}
        if shows_post(row):
            post = x_view(row, False, True)
            text = (post or {}).get("text") if post and post.get("text") is not None else str(_first(x_post.get("text"), row.get("body_text"), ""))
            # A post's text is sent as written: its headings make the outline, without anchors.
            if (post and post.get("translation")) or text.strip():
                reading = reading_body(
                    language,
                    {"html": text_to_html(post["translation"]), "kind": "translation"} if post and post.get("translation") else None,
                    text_to_html(text) if text.strip() else None,
                    True,
                    lambda html: (html, with_outline(html)[1]),
                )
            if post:
                x = {k: v for k, v in post.items() if k not in ("text", "translation")}
        elif row["body_mode"] == "full" and row.get("body_html"):
            chinese = is_chinese_body(row)
            zh = (
                {"html": row["body_html"], "kind": "original"}
                if chinese
                else ({"html": row["tr_html"], "kind": "translation"} if row.get("tr_html") else None)
            )
            reading = reading_body(language, zh, None if chinese else row["body_html"], True if chinese else bool(row.get("tr_complete")),
                                   lambda html: with_outline(proxy_body_images(html)))  # fmt: skip
        group = None
        if row["fact_id"]:
            g = conn.execute(
                f"""SELECT f.public_id::text AS public_id, count(p.article_id) AS reports, count(DISTINCT p.source_id) AS sources
                    FROM facts f JOIN fact_articles fa ON fa.fact_id = f.id JOIN publications p ON p.article_id = fa.article_id
                    WHERE f.id = %(fact)s AND {evidence_condition()} AND {listed_condition()}
                    GROUP BY f.public_id""",
                {**scope_params(now), "fact": row["fact_id"]},
            ).fetchone()
            if g:
                group = {"factId": g["public_id"], "reportCount": int(g["reports"]), "additionalSourceCount": max(0, int(g["sources"]) - 1)}
        # The reason goes with the seat; a report yielding it points to the one holding it.
        same_event = seat_holders(conn, [row], now).get(row["id"])
    item = {**summary, "x": x}
    if same_event:
        item["reason"] = None
        item["sameEvent"] = same_event
    item.update({
        "readingMode": "full", "author": row["author"], "body": reading["body"], "outline": reading["outline"], "relatedStories": related,
        "topics": topic_links(row["topics"]), "indexable": row["indexable"], "markdownAvailable": markdown_available(row),
        "group": group, "hasTranslation": reading["hasTranslation"], "bodyLanguage": reading["bodyLanguage"],
    })  # fmt: skip
    return {"item": item, "row": row}


def _first(*values):
    """The first value that is not null (JavaScript's `??` chain)."""
    return next((v for v in values if v is not None), None)


def export_markdown(pool, item_id: str) -> dict | None:
    """The page as a Markdown file: what the export button and route both offer, under the same visibility and licence rules."""
    from newsroom.engine import pack  # noqa: PLC0415
    from newsroom.engine.http_policy import site_url  # noqa: PLC0415
    from newsroom.engine.markdown import body_to_markdown  # noqa: PLC0415
    from newsroom.engine.publication.stories import public_source_name  # noqa: PLC0415
    from newsroom.engine.publication.v1 import iso, item_url  # noqa: PLC0415

    with pool.connection() as conn:
        row = load_row(conn, item_id)
    if not row or not markdown_available(row):
        return None
    site = pack.site()
    lines = [f"# {row['title']}", ""]
    if row["original_title"]:
        lines += [f"> 原標題：{row['original_title']}", ""]
    lines.append(f"- 來源：{public_source_name(row['source_name'])}")
    # Without a reliable date from the original, the time it was collected says so.
    lines.append(f"- 發布時間：{iso(row['published_at'])}" if row["published_at"] else f"- 收錄時間：{iso(row['discovered_at'])}")
    lines.append(f"- {site['SITE']['name']}：{item_url(row['id'])}")
    lines += [f"- 原文：{row['url']}", ""]
    if row["summary"]:
        lines += ["## 摘要", "", row["summary"], ""]
    if row["selected"] and row["seat"] and row["reason"]:
        lines += [f"## {site['ITEM_COPY']['reasonLabel']}", "", row["reason"], ""]
    if shows_post(row) and row.get("x_post"):
        post = x_view(row)
        if post and post["text"]:
            lines += ["## 正文", "", post["text"], ""]
        if post and post["translation"]:
            lines += ["## 中文譯文", "", post["translation"], ""]
        for media in (post or {}).get("media") or []:
            url = site_url(media["url"]) if media["url"].startswith("/") else media["url"]
            lines += [f"![{media.get('alt') if media.get('alt') is not None else ''}]({url})" if media["kind"] == "image" else f"[影片]({url})", ""]
        q = (post or {}).get("quoted")
        if q and q["text"]:
            lines += [
                f"## 引用 @{q['handle'] or ''}",
                "",
                *[f"> {line}" for line in str(q["text"]).split("\n")],
                "",
                *([q["url"], ""] if q["url"] else []),
            ]
        if q and q["text"] and q["translation"]:
            lines += ["### 引用中文譯文", "", *[f"> {line}" for line in q["translation"].split("\n")], ""]
    elif row["body_mode"] == "full" and row.get("body_html"):
        translation = export_translation(row)
        if translation:
            lines += ["## 正文 · 中文譯文", "", body_to_markdown(translation, row["url"]), ""]
        lines += ["## 正文" if is_chinese_body(row) else "## 正文 · 原文", "", body_to_markdown(row["body_html"], row["url"]), ""]
    return {"filename": f"{site['SITE']['mcpPrefix']}-{row['id']}.md", "body": re.sub(r"\n{3,}", "\n\n", "\n".join(lines))}
