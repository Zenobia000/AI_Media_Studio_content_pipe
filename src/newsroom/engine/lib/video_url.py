"""Video watch/share/embed addresses name players, whose controls and recommendations are not articles. Port of lib/video-url.ts."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit


def is_video_page_url(text: str) -> bool:
    try:
        u = urlsplit(text)
        host = (u.hostname or "").lower()
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not host:
        return False
    if re.fullmatch(r"(?:www\.|m\.)?youtube\.com", host):
        watch = bool(re.fullmatch(r"/watch/?", u.path)) and bool(parse_qs(u.query).get("v", [""])[0])
        return watch or bool(re.fullmatch(r"/(?:shorts|embed|v)/[^/]+/?", u.path))
    if host == "youtu.be":
        return bool(re.fullmatch(r"/[^/]+/?", u.path))
    if host in ("vimeo.com", "www.vimeo.com"):
        return bool(re.fullmatch(r"/\d+(?:/[a-zA-Z0-9]+)?/?", u.path))
    return host == "player.vimeo.com" and bool(re.fullmatch(r"/video/\d+/?", u.path))
