"""Article page reading. Port of content/extract.ts, so far the sign-in tests the listings need."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# Sign-in paths read from the final address, never the text; a segment only counts when it ends at a delimiter (a real page path or an extension).
AUTH_PATH = re.compile(
    r"(?:^|/)(?:login|log-in|log_in|signin|sign-in|sign_in|register|signup|sign-up|sign_up|authorize|oauth2?|sso|auth|wp-login(?:\.php)?)(?:/|$|\.(?:php|aspx?|html?|jsp))",
    re.I,
)


def _path(url: str) -> str:
    return urlsplit(url).path or "/"


def is_sign_in_url(url: str) -> bool:
    """Whether an address is a sign-in form's own path (a listing's "Log in" link is no post either)."""
    try:
        return AUTH_PATH.search(_path(url)) is not None
    except ValueError:
        return False


def is_sign_in_redirect(asked: str, landed: str) -> bool:
    """A redirect that ended on a sign-in form: read from the final address of the fetch, never from the text."""
    try:
        return AUTH_PATH.search(_path(asked)) is None and AUTH_PATH.search(_path(landed)) is not None
    except ValueError:
        return False


def page_fetchable(url: str, source_kind: str) -> bool:
    """Ordinary article pages; player pages have no article body, and social posts arrive separately."""
    from newsroom.engine.lib.video_url import is_video_page_url

    if source_kind in ("x_search", "mp_account") or is_video_page_url(url):
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.hostname) and re.search(r"(^|\.)(x\.com|twitter\.com|mp\.weixin\.qq\.com)$", u.hostname, re.I) is None
