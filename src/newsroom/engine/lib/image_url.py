"""Reject non-content images at ingestion and public reads: extracted bodies can mislabel tracking endpoints and video pages as
illustrations. Port of lib/image-url.ts."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from newsroom.engine.lib.video_url import is_video_page_url

TRACKING_HOSTS = {
    "ids4.ad.gt", "ids.ad.gt", "secure.adnxs.com", "sync.1rx.io", "ssum-sec.casalemedia.com", "sync.smartadserver.com", "token.rubiconproject.com",
    "image2.pubmatic.com", "sync.go.sonobi.com", "onetag-sys.com",
}  # fmt: skip


def _number(s: str) -> float:
    try:
        return float(s.strip() or 0)
    except ValueError:
        return float("nan")


def is_non_article_image(src: str, width: str | None = None, height: str | None = None) -> bool:
    if re.match(r"^data:", src, re.I) and not re.match(r"^data:image/[a-z0-9.+-]+[;,]", src, re.I):
        return True
    if width is not None and height is not None and _number(width) <= 1 and _number(height) <= 1:
        return True
    try:
        if (urlsplit(src).hostname or "") in TRACKING_HOSTS:
            return True
    except ValueError:
        return False
    return is_video_page_url(src)
