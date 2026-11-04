"""X Article links in a post's text (sources/x.ts)."""

from __future__ import annotations

import re

X_ARTICLE_LINK = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/i/article/\d+", re.I)


def links_x_article(text: str | None) -> bool:
    """The post links an X Article (long-form text posted on X): its body is fetched before judging."""
    return X_ARTICLE_LINK.search(text or "") is not None


def only_x_article_link(text: str | None) -> bool:
    """Nothing but the article's link: without the article there is nothing to judge (under 30 characters left)."""
    return links_x_article(text) and len(X_ARTICLE_LINK.sub("", text or "").strip()) < 30
