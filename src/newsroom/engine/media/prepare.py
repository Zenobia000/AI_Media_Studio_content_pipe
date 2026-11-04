"""Images of a newly selected item, prepared as it is selected: every rendition its card and its page ask the image proxy for is fetched and resized once (a first
reader otherwise waits seconds for the source site), animations are re-encoded, AVIF variants are made while the original is at hand, and the share image is rendered
before the content push makes chat apps fetch it. Port of media/prepare.ts."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import unquote

from newsroom.engine.media.images import convert_animated, convert_avif, produce_image
from newsroom.engine.media.og import render_og
from newsroom.engine.media.renditions import AVIF_MODE_SUFFIX, AVIF_MODES
from newsroom.engine.publication.detail import load_item_detail
from newsroom.engine.publication.items import to_feed_item_summary
from newsroom.engine.publication.og import load_item_og_card

log = logging.getLogger("newsroom.media")
_PROXIED = re.compile(r'/api/img-proxy\?u=([^&"\s\\]+)&mode=([a-z0-9-]+)')


def proxied_renditions(answers: list) -> list[dict]:
    """The (source, mode) pairs the proxy URLs in these public answers point at. AVIF variants are made from their rendition."""
    text = json.dumps(answers, ensure_ascii=False, separators=(",", ":"), default=str).replace("&amp;", "&").replace("\u0026", "&")
    seen: dict[str, dict] = {}
    for m in _PROXIED.finditer(text):
        if m.group(2).endswith(AVIF_MODE_SUFFIX):
            continue
        url = unquote(m.group(1))
        seen[f"{m.group(2)}|{url}"] = {"url": url, "mode": m.group(2)}
    return list(seen.values())


def prepare_article_media(pool, article_id: str) -> dict:
    zh, original = load_item_detail(pool, article_id), load_item_detail(pool, article_id, "original")
    if not zh or not original:
        return {"renditions": 0, "failed": 0, "animatedSaved": 0, "avifSaved": 0}
    failed = animated_saved = avif_saved = 0
    renditions = proxied_renditions([to_feed_item_summary(zh["row"]), zh["item"], original["item"]])
    for r in renditions:
        try:
            image = produce_image(r["url"], r["mode"])
            if image.type == "image/gif":
                animated_saved += convert_animated(r["url"], r["mode"])
            if r["mode"] in AVIF_MODES:
                avif_saved += convert_avif(r["url"], r["mode"])
        except Exception:
            failed += 1  # the request path tries again (and answers 502) if the source is still unavailable
    return {"renditions": len(renditions), "failed": failed, "animatedSaved": animated_saved, "avifSaved": avif_saved}


def warm_share_image(pool, article_id: str) -> bool:
    """Renders the current public article card into the shared disk cache before chat apps unfurl the pushed link. No serving process is required. Best effort."""
    try:
        card = load_item_og_card(pool, article_id)
        if not card:
            return False
        render_og(card)
        return True
    except Exception as error:
        log.warning("share image preparation failed for %s: %s", article_id, error)
        return False
