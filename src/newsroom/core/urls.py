"""URL identity, the same rules as AIHOT's packages/backend/src/lib/url.ts (a mismatch would store one page twice: once through the
site's collectors and once through ours). tests/unit/test_urls.py compares the results with ones produced by the TypeScript version."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

_TRACKING = re.compile(
    r"^(utm_[a-z]+|spm|from|ref|ref_src|ref_url|source|share_source|share_token|fbclid|gclid|igshid|mc_cid|mc_eid|_hsenc|_hsmi|scene|chksm"
    r"|sessionid|srcid|clicktime|enterid|mkt_tok)$",
    re.I,
)
# The WHATWG path percent-encode set: anything outside printable ASCII (taken as UTF-8), space, and " < > ` { }. Existing %XX stay.
_PATH_ENCODE = re.compile(r"[^\x21-\x7e]|[\"<>`{}]")


def _parse(text: str):
    try:
        u = urlsplit(text.strip())
        _ = u.port  # raises ValueError for a bad port
    except ValueError:
        return None
    return u if u.scheme in ("http", "https") and u.hostname else None


def _form_encode(pairs: list[tuple[str, str]]) -> str:
    """URLSearchParams.toString(): application/x-www-form-urlencoded (space as +, only * - . _ and alphanumerics left as they are)."""

    def enc(s: str) -> str:
        return quote(s, safe="*-._", encoding="utf-8").replace("%20", "+")

    return "&".join(enc(k) + "=" + enc(v) for k, v in pairs)


def _encode_path(path: str) -> str:
    return _PATH_ENCODE.sub(lambda m: quote(m.group(0), safe=""), path)


def normalize_url(text: str) -> str | None:
    """Canonical form used for identity: lower-case host, no fragment, no tracking params, no trailing slash."""
    u = _parse(text)
    if u is None:
        return None
    host = re.sub(r"^www\.", "", (u.hostname or "").lower())
    if ":" in host:
        host = f"[{host}]"
    port = u.port
    netloc = host if port in (None, 443, 80) else f"{host}:{port}"
    pairs = parse_qsl(u.query, keep_blank_values=True)
    if host == "mp.weixin.qq.com":
        kept: list[tuple[str, str]] = []
        for k in ("__biz", "mid", "idx", "sn"):
            first = next((v for kk, v in pairs if kk == k), None)
            if first:
                kept.append((k, first))
    else:
        kept = sorted(((k, v) for k, v in pairs if not _TRACKING.match(k)), key=lambda kv: kv[0])  # stable, by key only
    path = _encode_path(u.path) or "/"
    s = urlunsplit(("https", netloc, path, _form_encode(kept), ""))
    if s.endswith("/") and path != "/":
        s = s[:-1]
    return s


def tweet_id_from_url(url: str) -> str | None:
    """Tweet id from an x.com / twitter.com status URL."""
    u = _parse(url)
    if u is None or not re.fullmatch(r"(?:(?:www|mobile|m)\.)?(?:x|twitter)\.com", (u.hostname or "").lower()):
        return None
    m = re.match(r"^/[^/]+/status(?:es)?/(\d+)(?:/|$)", u.path, re.I)
    return m.group(1) if m else None


def site_identity_for_url(text: str) -> str | None:
    """Sites with one address per thing: an arXiv paper whatever its version or form, a YouTube video whatever the share form, a
    GitHub repository itself. None for anything else."""
    u = _parse(text)
    if u is None:
        return None
    host = re.sub(r"^(?:www|m)\.", "", (u.hostname or "").lower())
    if host == "arxiv.org":
        m = re.match(r"^/(?:abs|pdf|html)/(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?/?$", u.path)
        return f"arxiv:{m.group(1)}" if m else None
    if host == "youtu.be":
        m = re.match(r"^/([\w-]{11})/?$", u.path)
        return f"youtube:{m.group(1)}" if m else None
    if host == "youtube.com":
        if u.path == "/watch":
            vid = next((v for k, v in parse_qsl(u.query, keep_blank_values=True) if k == "v"), None)
        else:
            m = re.match(r"^/(?:shorts|embed|live)/([\w-]{11})/?$", u.path)
            vid = m.group(1) if m else None
        return f"youtube:{vid}" if vid and re.fullmatch(r"[\w-]{11}", vid) else None
    if host == "github.com":
        m = re.match(r"^/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", u.path)
        return f"github:{m.group(1).lower()}/{m.group(2).lower()}" if m else None
    return None


def identity_key_for_url(url: str, keep_fragment: bool = False) -> str | None:
    """Identity of material at an address. `keep_fragment` is for sources whose entries are sections of one page."""
    tweet = tweet_id_from_url(url)
    if tweet:
        return f"x:{tweet}"
    site = site_identity_for_url(url)
    if site:
        return site
    normalized = normalize_url(url)
    if not normalized:
        return None
    fragment = urlsplit(url.strip()).fragment if keep_fragment else ""
    return f"url:{normalized}" + (f"#{fragment}" if fragment else "")
