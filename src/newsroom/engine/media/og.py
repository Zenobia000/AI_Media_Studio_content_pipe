"""Share images: 1200×630 cards and 1080×1440 article posters. Port of media/og.ts and media/poster.ts.

The TypeScript lays text out with satori and rasterises with sharp. This draws the same design with Pillow and the same fonts, so the
pictures have the same size, colours and texts, but line breaks and anti-aliasing differ slightly. What is identical is everything around the
picture: the cache key (ETag), the cards' texts and the files in the disk cache (named by that key, so a picture drawn by either program is
reused by the other)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import uuid
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from newsroom.engine import pack
from newsroom.engine.config import REPO_ROOT, config

OG_TEMPLATE_VERSION = "og-2026-09-29.1"
POSTER_TEMPLATE_VERSION = "poster-2026-09-29.1"
WIDTH, HEIGHT = 1200, 630
POSTER_WIDTH, POSTER_HEIGHT = 1080, 1440
FONT_DIR = REPO_ROOT / "assets" / "og-fonts"
ACCENTS = {"teal": "#2ce2e8", "hot": "#ff7a5f", "amber": "#e2b454"}


@cache
def fallback_fonts() -> list[str]:
    """Fonts a site adds beside Noto Sans SC: `<name>-400.ttf` and `<name>-700.ttf` in assets/og-fonts (none by default)."""
    import re  # noqa: PLC0415

    return sorted(f for f in os.listdir(FONT_DIR) if re.fullmatch(r".+-(400|700)\.ttf", f) and not f.startswith("noto-sans-sc-"))


def _js(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def og_etag(card: dict) -> str:
    """A card's cache key. `card` keeps the key order the TypeScript builds it in (an undefined field is simply not a key)."""
    return hashlib.sha256((OG_TEMPLATE_VERSION + ",".join(fallback_fonts()) + _js(card)).encode()).hexdigest()[:24]


def poster_etag(poster: dict) -> str:
    return hashlib.sha256((POSTER_TEMPLATE_VERSION + ",".join(fallback_fonts()) + _js(poster)).encode()).hexdigest()[:24]


def clamp(text: str, limit: int) -> str:
    chars = " ".join(text.split()).strip()
    return chars[: limit - 1] + "…" if len(chars) > limit else chars


@cache
def _font(weight: int, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"noto-sans-sc-{weight}.ttf"), size)


def _lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    """Greedy wrapping: Chinese breaks between any two characters, Latin words stay whole."""
    out: list[str] = []
    line = ""
    for token in _tokens(text):
        if line and draw.textlength(line + token, font=font) > width:
            out.append(line.rstrip())
            line = token.lstrip()
        else:
            line += token
    if line:
        out.append(line)
    return out


def _tokens(text: str) -> list[str]:
    import re  # noqa: PLC0415

    return re.findall(r"[A-Za-z0-9_'’.,:;!?%/@#&$+\-]+\s*|\s+|.", text)


def _paragraph(draw, xy, text, font, fill, width, line_height, max_lines=None) -> int:
    x, y = xy
    lines = _lines(draw, text, font, width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("…") + "…"
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _glow(img: Image.Image, center: tuple[float, float], radius: float, color: tuple[int, int, int], alpha: float) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse([center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius], fill=(*color, int(255 * alpha)))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius / 2.2)))


def _name_mark(draw, x, y, size, color, dot) -> None:
    d = size * 0.36
    draw.ellipse([x, y + (size - d) / 2, x + d, y + (size - d) / 2 + d], fill=dot)
    draw.text((x + d + size * 0.28, y), pack.site()["SITE"]["name"], font=_font(700, size), fill=color)


def render_card(card: dict) -> bytes:
    from urllib.parse import urlsplit  # noqa: PLC0415

    from newsroom.engine.config import config as cfg  # noqa: PLC0415

    accent = ACCENTS[card.get("accent") or "teal"]
    img = Image.new("RGBA", (WIDTH, HEIGHT), "#0a1012")
    _glow(img, (WIDTH * 0.88, HEIGHT * 0.08), 380, (44, 226, 232), 0.28)
    _glow(img, (0, HEIGHT), 400, (23, 107, 117), 0.35)
    draw = ImageDraw.Draw(img)
    _name_mark(draw, 72, 64, 34, "#e6eded", "#2ce2e8")
    host = urlsplit(cfg().site_url).netloc
    f24 = _font(400, 24)
    draw.text((WIDTH - 72 - draw.textlength(host, font=f24), 68), host, font=f24, fill="#82939a")
    draw.ellipse([72, 152, 82, 162], fill=accent)
    draw.text((96, 140), clamp(card["kicker"], 30), font=_font(700, 28), fill=accent)
    title = clamp(card["title"], 64)
    size = 50 if len(title) > 40 else 58 if len(title) > 24 else 66
    text_width = WIDTH - 144 - (170 + 40 if card.get("badge") else 0)
    y = _paragraph(draw, (72, 200), title, _font(700, size), "#ffffff", text_width, int(size * 1.25))
    if card.get("subtitle"):
        limit = 26 if len(title) > 40 else 50 if len(title) > 24 else 78
        _paragraph(draw, (72, y + 22), clamp(card["subtitle"], limit), _font(400, 28), "#b1bec0", text_width, 42)
    badge = card.get("badge")
    if badge:
        cx, cy = WIDTH - 72 - 85, 330
        draw.ellipse([cx - 85, cy - 85, cx + 85, cy + 85], outline=accent, width=6)
        value, label = _font(700, 58), _font(400, 22)
        draw.text((cx - draw.textlength(badge["value"], font=value) / 2, cy - 50), badge["value"], font=value, fill="#ffffff")
        draw.text((cx - draw.textlength(badge["label"], font=label) / 2, cy + 22), badge["label"], font=label, fill="#82939a")
    if card.get("meta"):
        draw.line([(72, HEIGHT - 110), (WIDTH - 72, HEIGHT - 110)], fill=(230, 237, 237, 31), width=1)
        draw.text((72, HEIGHT - 88), clamp(card["meta"], 70), font=f24, fill="#82939a")
    return _png(img.convert("RGB"))


def render_poster(p: dict) -> bytes:
    import segno  # noqa: PLC0415

    site = pack.site()["SITE"]
    ink, accent = "#0e191b", "#176b75"
    img = Image.new("RGBA", (POSTER_WIDTH, POSTER_HEIGHT), "#f5f6f5")
    _glow(img, (POSTER_WIDTH, 0), 560, (23, 107, 117), 0.16)
    _glow(img, (0, POSTER_HEIGHT), 480, (44, 226, 232), 0.10)
    draw = ImageDraw.Draw(img)
    _name_mark(draw, 88, 84, 44, ink, accent)
    f26 = _font(400, 26)
    draw.text((POSTER_WIDTH - 88 - draw.textlength(p["date"], font=f26), 92), p["date"], font=f26, fill="#66757a")
    draw.ellipse([88, 192, 100, 204], fill=accent)
    kicker = clamp(p["kicker"], 20)
    draw.text((116, 178), kicker, font=_font(700, 30), fill=accent)
    if p["score"] is not None:
        label = f"精選 · {round(p['score'])} 分"
        x = 116 + draw.textlength(kicker, font=_font(700, 30)) + 20
        draw.rounded_rectangle([x, 176, x + draw.textlength(label, font=f26) + 32, 220], radius=22, fill="#e3eef0")
        draw.text((x + 16, 182), label, font=f26, fill="#0f5a63")
    title = clamp(p["title"], 72)
    size = 58 if len(title) > 48 else 66 if len(title) > 30 else 76
    y = _paragraph(draw, (88, 250), title, _font(700, size), ink, POSTER_WIDTH - 176, int(size * 1.3))
    if p["summary"]:
        limit = 120 if len(title) > 48 else 150 if len(title) > 30 else 180
        y = _paragraph(draw, (88, y + 36), clamp(p["summary"], limit), _font(400, 34), "#3a484c", POSTER_WIDTH - 176, 58)
    draw.text((88, y + 36), clamp(f"來源：{p['source']}", 34), font=_font(400, 28), fill="#66757a")
    top = POSTER_HEIGHT - 72 - 40 - 272
    draw.rounded_rectangle([88, top, POSTER_WIDTH - 88, top + 272], radius=32, fill="#ffffff")
    qr = segno.make(p["url"], error="m", boost_error=False)
    matrix = [list(row) for row in qr.matrix]
    cell = 200 / len(matrix)
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                draw.rectangle([128 + c * cell, top + 36 + r * cell, 128 + (c + 1) * cell, top + 36 + (r + 1) * cell], fill=ink)
    draw.text((372, top + 50), "長按識別二維碼", font=_font(700, 36), fill=ink)
    draw.text((372, top + 112), "閱讀全文、中文譯文與原文連結", font=_font(400, 28), fill="#66757a")
    from urllib.parse import urlsplit  # noqa: PLC0415

    draw.text((372, top + 170), urlsplit(config().site_url).netloc, font=f26, fill=accent)
    foot = f"{site['name']} · {site['tagline']}"
    f24 = _font(400, 24)
    draw.text(((POSTER_WIDTH - draw.textlength(foot, font=f24)) / 2, POSTER_HEIGHT - 72 - 24), foot, font=f24, fill="#98a4a7")
    return _png(img.convert("RGB"))


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("P", palette=Image.Palette.ADAPTIVE, colors=256, dither=Image.Dither.FLOYDSTEINBERG).save(out, "PNG", optimize=True)
    return out.getvalue()


# ── the disk cache (<etag>.png, shared with the TypeScript) ──────────────────────────────

_lock = threading.Lock()


def _cached_png(name: str, draw) -> bytes:
    file = config().data_dir / "ogcache" / f"{name}.png"
    try:
        return file.read_bytes()
    except OSError:
        pass
    with _lock:  # concurrent requests share one render
        try:
            return file.read_bytes()
        except OSError:
            pass
        png = draw()
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(f"{file}.{os.getpid()}.{uuid.uuid4()}.tmp")
        tmp.write_bytes(png)
        tmp.replace(file)
        return png


def render_og(card: dict) -> bytes:
    return _cached_png(og_etag(card), lambda: render_card(card))


def render_poster_png(poster: dict) -> bytes:
    return _cached_png(f"poster-{poster_etag(poster)}", lambda: render_poster(poster))
