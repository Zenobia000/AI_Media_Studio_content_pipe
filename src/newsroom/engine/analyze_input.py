"""What the judging steps read about an article: loaded once per analysis and rendered per step. Port of editorial/input.ts."""

from __future__ import annotations

import base64
import re

from newsroom.engine.lib.text import collapse_whitespace, truncate
from newsroom.engine.media.images import SVG_PASSTHROUGH_MAX_BYTES, produce_image
from newsroom.engine.posts import with_x_article
from newsroom.engine.sources.dates import describe_publication_time
from newsroom.engine.writing import MAX_BODY_CHARS

KIND_LABEL = {"rss": "RSS", "web_list": "網頁", "json_list": "網頁介面", "x_search": "X 帖子", "mp_account": "微信公眾號", "external": "外部上報"}


def load_analyze_input(pool, article_id: str) -> dict | None:
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT a.id, a.revision, a.title, a.url, a.author, a.published_at, a.publication_time, a.discovered_at, a.body_text, a.excerpt, a.body_status, a.x_post, a.x_article, a.media,
                      s.name AS source_name, s.kind AS source_kind, s.tier, s.selection_class, s.first_party, s.tags AS source_tags, s.owner_entity_id, s.config,
                      tr.body_text AS translation_zh
               FROM articles a JOIN sources s ON s.id = a.source_id
               LEFT JOIN translations tr ON tr.article_id = a.id AND tr.lang = 'zh' AND tr.revision >= a.revision
               WHERE a.id = %s""",
            (article_id,),
        ).fetchone()
    if not row:
        return None
    cfg = row["config"] or {}
    return {
        "id": row["id"], "revision": row["revision"], "title": row["title"], "url": row["url"], "author": row["author"], "publishedAt": row["published_at"],
        "publicationTime": row["publication_time"], "discoveredAt": row["discovered_at"], "bodyText": row["body_text"], "excerpt": row["excerpt"],
        "bodyStatus": row["body_status"], "xPost": with_x_article(row["x_post"], row["x_article"]), "media": row["media"],
        "source": {
            "name": row["source_name"], "kind": row["source_kind"], "tier": row["tier"], "selectionClass": row["selection_class"], "firstParty": row["tier"] == "T1",
            "tags": row["source_tags"], "ownerEntityId": row["owner_entity_id"],
            "fetchesBody": cfg.get("fetchPublicContent") is True or bool(cfg.get("detail")) or row["source_kind"] == "web_list",
        },
        "translationZh": row["translation_zh"],
    }  # fmt: skip


def build_material(a: dict) -> str:
    """The material as the structure step reads it (source facts, text, link)."""

    def body(text: str) -> str:
        # Conditions often occur at the end of an announcement. Use the existing body budget instead of only its lead; beyond it, state the missing tail so it is
        # not mistaken for complete evidence.
        return text if len(text) <= MAX_BODY_CHARS else f"{text[:MAX_BODY_CHARS]}\n【原文超過 {MAX_BODY_CHARS} 字元，後文未提供】"

    src = a["source"]
    x = a.get("xPost")
    lines = ["<source>", f"名稱：{src['name']}", f"型別：{KIND_LABEL.get(src['kind'], src['kind'])}；分級：{src['tier']}；一手來源：{'是' if src['firstParty'] else '否'}", "</source>", "<material>"]
    if a.get("publishedAt"):
        lines.append(f"發布時間：{describe_publication_time(a['publishedAt'], a.get('publicationTime'))}")
    if a.get("author"):
        lines.append(f"作者：{a['author']}")
    if x:
        lines.append(f"作者：{x.get('authorName') if x.get('authorName') is not None else ''} (@{x.get('handle') if x.get('handle') is not None else ''})")
        lines.append(f"帖子：\n{body(str(x.get('text') if x.get('text') is not None else a['title']))}")
        quoted = x.get("quoted") or {}
        if quoted.get("text"):
            lines.append(f"引用的帖子（@{quoted.get('handle') if quoted.get('handle') is not None else ''}）：\n{body(str(quoted['text']))}")
        if a.get("translationZh"):
            lines.append(f"帖子中文譯文：\n{truncate(a['translationZh'], 4000)}")
    else:
        lines.append(f"標題：{collapse_whitespace(a['title'])}")
        original = a.get("bodyText") if a.get("bodyText") is not None else a.get("excerpt") if a.get("excerpt") is not None else ""
        lines.append(f"正文：\n{body(original)}" if original else "正文：（無）")
        if a.get("translationZh") and not a.get("bodyText"):
            lines.append(f"正文中文譯文：\n{truncate(a['translationZh'], 5000)}")
    lines.append(f"原文連結：{a['url']}")
    lines.append("</material>")
    return "\n".join(lines)


def first_image_part(a: dict) -> dict | None:
    """The article's first image, inlined: models often cannot reach the original hosts, so they get the site's cached thumbnail. None when it cannot be
    fetched. A post without its own image shows the quoted post's. (A vector too large to pass through would be rasterised in the original; here it is
    left out.)"""
    x = a.get("xPost") or {}
    candidates = [*(x.get("media") or []), *((x.get("quoted") or {}).get("media") or []), *(a.get("media") or [])]
    image = next((m for m in candidates if m.get("kind") == "image" and m.get("url")), None)
    if not image:
        return None
    try:
        prepared = produce_image(str(image["url"]), "thumb")
        if prepared.type == "image/svg+xml" and len(prepared.body) > SVG_PASSTHROUGH_MAX_BYTES:
            return None
        if not re.fullmatch(r"image/(jpeg|png|webp)", prepared.type):
            return None
        return {"type": "image_url", "image_url": {"url": f"data:{prepared.type};base64,{base64.b64encode(prepared.body).decode()}"}}
    except Exception:
        return None
