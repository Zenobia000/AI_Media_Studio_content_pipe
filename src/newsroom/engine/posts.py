"""Textless and link-only posts keep their own text and media instead of model-written copy (content/posts.ts)."""

from __future__ import annotations

import re

from newsroom.engine.lib.text import collapse_whitespace

_URL = re.compile(r"https?://\S+", re.I)


def is_empty_or_link_only(text: str) -> bool:
    return not _URL.sub("", text).strip()


def with_x_article(x_post: dict | None, article: dict | None) -> dict | None:
    """The post and its already fetched long article are one text for writing and public reading."""
    if not x_post or not (article or {}).get("text"):
        return x_post
    assert article is not None  # the check above returned for a missing article
    post_text = x_post.get("text")
    parts = [str(post_text if post_text is not None else "").strip(), f"【X 長文】{article['title']}" if article.get("title") else "【X 長文】", article["text"]]
    return {**x_post, "text": "\n\n".join(p for p in parts if p)}


def original_post_copy(post: dict | None, url: str, article: dict | None = None) -> dict | None:
    """A fetched long article supplies real material; otherwise there is nothing here to translate."""
    if not post or ((article or {}).get("text") or "").strip():
        return None
    value = post.get("text")
    text = str(value if value is not None else "")
    if not is_empty_or_link_only(text):
        return None
    return {"title": collapse_whitespace(text) or url, "summary": text if text.strip() else None}
