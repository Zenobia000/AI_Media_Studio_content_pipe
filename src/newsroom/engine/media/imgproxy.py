"""Signed image proxy URLs. The address format and signing key stay stable, so proxy URLs already cached in full RSS and readers keep
working: /api/img-proxy?u=&mode=&exp=&sig= with sig = hex(HMAC-SHA256(IMG_PROXY_SIGN_SECRET, `${u}|${mode}|${exp}`)), sent as its first 16 hex
digits; a full-length signature from an older URL is still accepted. A mode with the AVIF suffix (card-avif) is that rendition's AVIF variant.
Port of media/imgproxy.ts (body rewriting follows with the item detail)."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
from urllib.parse import quote

from newsroom.engine.config import config, credential
from newsroom.engine.lib.image_url import is_non_article_image
from newsroom.engine.media.renditions import AVIF_MODE_SUFFIX, AVIF_MODES, IMAGE_WIDTHS, RESPONSIVE_MODES

# A URL keeps its expiry for a whole day, so the edge and browsers can reuse one copy of an image all day; it stays valid for two to three
# days. Avatars keep one address for 30 days.
WINDOW_SECONDS = 24 * 3600
AVATAR_WINDOW_SECONDS = 30 * 24 * 3600
LIFETIME_SECONDS = 48 * 3600
SIG_HEX = 16
LONGEST_CACHE_SECONDS = AVATAR_WINDOW_SECONDS


def _secret() -> str:
    s = credential("auth", "IMG_PROXY_SIGN_SECRET")
    if not s:
        raise RuntimeError("IMG_PROXY_SIGN_SECRET is not configured")
    return s


def signature(url: str, mode: str, exp) -> str:
    return hmac.new(_secret().encode(), f"{url}|{mode}|{exp}".encode(), hashlib.sha256).hexdigest()


def _component(s: str) -> str:
    """encodeURIComponent: everything but A-Z a-z 0-9 - _ . ! ~ * ' ( )."""
    return quote(s, safe="!~*'()")


def proxy_expiry(now_ms: float, lifetime_seconds: int, window_seconds: int) -> int:
    """Expiry rounded up to a window boundary at least `lifetime` (48 h) ahead, so URLs stay cacheable."""
    return math.ceil((now_ms / 1000 + lifetime_seconds) / window_seconds) * window_seconds


def proxied_image(
    url: str | None, mode: str, absolute: bool = False, now_ms: float | None = None, lifetime_seconds: int = LIFETIME_SECONDS
) -> str | None:
    if not url:
        return None
    if is_non_article_image(url):
        return None
    if url.startswith("data:"):
        return url
    if not re.match(r"^https?://", url, re.I):
        return None
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    exp = proxy_expiry(now_ms, lifetime_seconds, AVATAR_WINDOW_SECONDS if mode in RESPONSIVE_MODES["avatar"] else WINDOW_SECONDS)
    path = f"/api/img-proxy?u={_component(url)}&mode={mode}&exp={exp}&sig={signature(url, mode, exp)[:SIG_HEX]}"
    return f"{config().site_url}{path}" if absolute else path


def proxied_image_set(
    url: str | None, kind: str, absolute: bool = False, now_ms: float | None = None, lifetime_seconds: int = LIFETIME_SECONDS
) -> str | None:
    """Browser source candidates, each independently signed with the same expiry boundary."""
    if not url or not re.match(r"^https?://", url, re.I) or is_non_article_image(url):
        return None
    return ", ".join(f"{proxied_image(url, m, absolute, now_ms, lifetime_seconds)} {IMAGE_WIDTHS[m]}w" for m in RESPONSIVE_MODES[kind])


def avif_candidates(src_set: str) -> str:
    """A proxied srcset's candidates as their AVIF variants, for a <picture> source ahead of the img's own."""
    return re.sub(r"([?&]mode=[a-z0-9-]+)(?=&)", lambda m: m.group(1) + AVIF_MODE_SUFFIX, src_set)


def verify_proxy_request(u: str | None, mode: str | None, exp: str | None, sig: str | None, now_ms: float | None = None) -> dict:
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    if not u or mode == "" or not exp or not sig:
        return {"ok": False, "reason": "missing"}
    if not re.fullmatch(r"\d{9,11}", exp):
        return {"ok": False, "reason": "missing"}
    if int(exp) * 1000 < now_ms:
        return {"ok": False, "reason": "expired"}
    if not re.match(r"^https?://", u, re.I):
        return {"ok": False, "reason": "bad-url"}
    # Older article pages signed body images without a mode (as "default"); those keep loading, as full images, until their signature expires.
    avif = bool(mode and mode.endswith(AVIF_MODE_SUFFIX))
    rendition = mode[: -len(AVIF_MODE_SUFFIX)] if avif and mode else mode
    if avif and rendition not in AVIF_MODES:
        return {"ok": False, "reason": "bad-signature"}
    well_formed = bool(re.fullmatch(rf"(?:[0-9a-f]{{{SIG_HEX}}}|[0-9a-f]{{64}})", sig, re.I))
    given = bytes.fromhex(sig) if well_formed else b""
    expected = bytes.fromhex(signature(u, rendition or "default", exp))[: len(given)]
    if not given or not hmac.compare_digest(given, expected):
        return {"ok": False, "reason": "bad-signature"}
    return {"ok": True, "url": u, "mode": rendition or "full", "avif": avif}
