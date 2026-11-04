"""Image fetch, resize and cache for the signed image proxy: a (mode, url) is fetched once through the egress route and kept on disk. Port of
media/images.ts and media/upstream.ts, with Pillow where the TypeScript uses sharp.

Differences that matter: the encoders are different programs, so a resized picture has the same format and size as the TypeScript's but not
the same bytes; vector images (SVG) are passed through as they are, since Pillow cannot draw them; and the background conversions (animated
GIF to WebP, AVIF variants) are not done here, so a picture waiting for one is served as it is and marked pending."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from newsroom.engine import pack
from newsroom.engine.config import config
from newsroom.engine.lib.http_fetch import GuardedResponse, guarded_fetch
from newsroom.engine.media.renditions import AVIF_MODE_SUFFIX, IMAGE_WIDTHS
from newsroom.engine.time import beijing_date

ORIGINAL_TTL = 60.0
ORIGINAL_MAX_BYTES = 32 * 1024 * 1024
ORIGINAL_MAX_ENTRIES = 32
SVG_PASSTHROUGH_MAX_BYTES = 128 * 1024
ANIMATION_MAX_PIXELS = 200_000_000
PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


class ImageBudgetExceeded(Exception):
    """The upstream budget is spent: uncached images wait for the window to reset."""


class UpstreamStatus(Exception):
    """The image's server answered, but not with the image."""

    def __init__(self, status: int) -> None:
        super().__init__(f"upstream {status}")
        self.status = status


def failure_seconds(error: BaseException) -> int:
    """How long a failed image is kept failed, in seconds (here and at the CDN): a server that refuses it or no longer has it keeps doing so."""
    return 6 * 3600 if isinstance(error, UpstreamStatus) and error.status in (401, 403, 404, 410) else 60


@dataclass
class PreparedImage:
    body: bytes
    type: str
    pending_animation: bool = False  # a GIF awaiting background conversion must not hide its replacement behind a multi-day cache
    pending_avif: bool = False  # the rendition standing in for an AVIF variant not prepared yet, under the same rule


# ── the upstream budget ────────────────────────────────────────────────────────────────


def _budget(env: str, site: float | None, unit: float) -> float:
    value = os.environ.get(env)
    value = value if value is not None else site
    return float("inf") if value is None else float(value) * unit


class Budget:
    def __init__(self) -> None:
        deployment = (pack.site().get("DEPLOYMENT") or {}).get("imageUpstreamBudget") or {}
        self.minute_limit = _budget("IMGPROXY_UPSTREAM_MB_PER_MINUTE", deployment.get("mbPerMinute"), 1e6)
        self.day_limit = _budget("IMGPROXY_UPSTREAM_GB_PER_DAY", deployment.get("gbPerDay"), 1e9)
        self.minute = {"at": 0, "bytes": 0}
        self.day = {"key": "", "bytes": 0}
        self.tripped: str | None = None
        self.on_trip = None  # called with the settings value when a window is first exceeded

    def allowed(self, now: float | None = None) -> tuple[bool, str | None]:
        now = time.time() * 1000 if now is None else now
        m = int(now // 60_000)
        if self.minute["at"] != m:
            self.minute = {"at": m, "bytes": 0}
        d = beijing_date(now)
        if self.day["key"] != d:
            self.day = {"key": d, "bytes": 0}
        if self.minute["bytes"] >= self.minute_limit:
            return False, self._trip("minute", now)
        if self.day["bytes"] >= self.day_limit:
            return False, self._trip("day", now)
        self.tripped = None
        return True, None

    def _trip(self, window: str, now: float) -> str:
        reason = (
            f"圖片代理上游流量超過每分鐘 {self.minute_limit / 1e6:g} MB"
            if window == "minute"
            else f"圖片代理上游流量超過當日 {self.day_limit / 1e9:g} GB"
        )
        if self.tripped != window:
            self.tripped = window
            if self.on_trip:
                from datetime import UTC, datetime  # noqa: PLC0415

                self.on_trip({"window": window, "reason": reason, "at": datetime.fromtimestamp(now / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                              "dayBytes": self.day["bytes"]})  # fmt: skip
        return reason

    def record(self, size: int) -> None:
        self.allowed()
        self.minute["bytes"] += size
        self.day["bytes"] += size


_budget_state: Budget | None = None


def budget() -> Budget:
    global _budget_state
    if _budget_state is None:
        _budget_state = Budget()
    return _budget_state


# ── originals ──────────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_recent: dict[str, tuple[GuardedResponse, float]] = {}
_failures: dict[str, tuple[float, BaseException]] = {}
_inflight: dict[str, threading.Lock] = {}


def _prune(now: float) -> None:
    for url in [u for u, (_, until) in _recent.items() if until <= now]:
        del _recent[url]


def _remember(url: str, value: GuardedResponse) -> None:
    now = time.time()
    _prune(now)
    if len(value.body) > ORIGINAL_MAX_BYTES:
        return
    while len(_recent) >= ORIGINAL_MAX_ENTRIES or sum(len(v.body) for v, _ in _recent.values()) + len(value.body) > ORIGINAL_MAX_BYTES:
        del _recent[next(iter(_recent))]
    _recent[url] = (value, now + ORIGINAL_TTL)


def original(url: str) -> GuardedResponse:
    """The source picture: kept a minute (responsive candidates and a lightbox ask for the same source in turn), and a failure is kept as
    long as failure_seconds says. Callers that overlap share one download."""
    with _lock:
        _prune(time.time())
        if url in _recent:
            return _recent[url][0]
        failed = _failures.get(url)
        if failed and failed[0] > time.time():
            raise failed[1]
        _failures.pop(url, None)
        gate = _inflight.setdefault(url, threading.Lock())
    with gate:
        with _lock:
            if url in _recent:
                return _recent[url][0]
            failed = _failures.get(url)
            if failed and failed[0] > time.time():
                raise failed[1]
        try:
            res = _fetch_original(url)
        except ImageBudgetExceeded:
            raise  # a budget failure is rechecked against the current minute, never kept
        except BaseException as error:
            with _lock:
                _failures[url] = (time.time() + failure_seconds(error), error)
                if len(_failures) > 512:
                    del _failures[next(iter(_failures))]
            raise
        finally:
            with _lock:
                _inflight.pop(url, None)
        with _lock:
            _remember(url, res)
        return res


def _fetch_original(url: str) -> GuardedResponse:
    ok, reason = budget().allowed()
    if not ok:
        raise ImageBudgetExceeded(reason)
    res = guarded_fetch(url, timeout_ms=20_000, max_bytes=15 * 1024 * 1024, headers={"accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    budget().record(len(res.body))
    if res.status != 200:
        raise UpstreamStatus(res.status)
    return res


# ── icons ──────────────────────────────────────────────────────────────────────────────


def decode_ico(buf: bytes):
    """The largest picture in a Windows icon (favicon.ico): PNG entries as they are, 32- and 24-bit bitmap entries as raw RGBA
    ({"raw", "width", "height"}). Other depths are skipped."""
    if len(buf) < 6 or struct.unpack_from("<H", buf, 0)[0] != 0 or struct.unpack_from("<H", buf, 2)[0] != 1:
        return None
    count = struct.unpack_from("<H", buf, 4)[0]
    entries = []
    for i in range(count):
        at = 6 + i * 16
        if at + 16 > len(buf):
            continue
        size = buf[at] or 256
        nbytes, offset = struct.unpack_from("<II", buf, at + 8)
        if offset + nbytes <= len(buf):
            entries.append((size, nbytes, offset))
    entries.sort(key=lambda e: -e[0])
    for _, nbytes, offset in entries:
        data = buf[offset : offset + nbytes]
        if data[:8] == PNG_SIGNATURE:
            return bytes(data)
        if len(data) < 40:
            continue
        width = struct.unpack_from("<i", data, 4)[0]
        height = abs(struct.unpack_from("<i", data, 8)[0]) // 2
        bits = struct.unpack_from("<H", data, 14)[0]
        if bits not in (32, 24) or width <= 0 or height <= 0:
            continue
        stride = -(-(width * bits) // 32) * 4
        pixels = data[struct.unpack_from("<I", data, 0)[0] :]
        if len(pixels) < stride * height:
            continue
        raw = bytearray(width * height * 4)
        step = bits // 8
        for y in range(height):
            row = pixels[(height - 1 - y) * stride :]
            for x in range(width):
                p, o = x * step, (y * width + x) * 4
                raw[o], raw[o + 1], raw[o + 2] = row[p + 2], row[p + 1], row[p]
                raw[o + 3] = row[p + 3] if bits == 32 else 255
        return {"raw": bytes(raw), "width": width, "height": height}
    return None


# ── renditions ─────────────────────────────────────────────────────────────────────────


def _frames(img: Image.Image) -> int:
    return getattr(img, "n_frames", 1)


def _open(data, ico) -> Image.Image:
    if isinstance(ico, dict):
        return Image.frombytes("RGBA", (ico["width"], ico["height"]), ico["raw"])
    return Image.open(io.BytesIO(ico if isinstance(ico, bytes) else data))


def still_rendition(body: bytes, upstream_type: str, mode: str, vectors: bool):
    """A picture resized for its rendition and not yet encoded ({"image", "type", "ico", "avatar"}), or the bytes that stay as they are
    ({"kept": {"body", "type"}}: animations and vectors)."""
    ico = decode_ico(body)
    supplied = upstream_type.split(";")[0].strip().lower()
    binary = supplied in ("application/octet-stream", "binary/octet-stream", "")
    if not supplied.startswith("image/") and not ico and not binary:
        raise ValueError("upstream is not an image")
    png_icon = supplied.endswith("icon") and body[:8] == PNG_SIGNATURE
    typ = "image/png" if ico or png_icon else supplied
    if typ.endswith("icon") and not ico:
        raise ValueError("unreadable icon")
    avatar = mode == "avatar" or mode.startswith("avatar-")
    width = IMAGE_WIDTHS.get(mode, 1600)
    if typ == "image/svg+xml":
        return {"kept": {"body": body, "type": typ}}  # Pillow cannot draw vectors
    img = _open(body, ico)
    if binary and not ico:
        mime = Image.MIME.get(img.format or "")
        if not mime or not mime.startswith("image/"):
            raise ValueError("upstream is not an image")
        typ = mime
    # A large animation can be hundreds of frames: do not silently replace it with a still or decode all its frames on an HTTP request.
    if _frames(img) > 1 or typ == "image/gif":
        return {"kept": {"body": body, "type": typ}}
    img = ImageOps.exif_transpose(img) if not ico else img
    if avatar:
        img = ImageOps.fit(img, (width, width), Image.LANCZOS, centering=(0.5, 0.5))
    elif img.width > width:
        img = img.resize((width, max(1, round(img.height * width / img.width))), Image.LANCZOS)
    return {"image": img, "type": typ, "ico": bool(ico), "avatar": avatar}


def encode_image(body: bytes, upstream_type: str, mode: str, vectors: bool = True) -> dict:
    still = still_rendition(body, upstream_type, mode, vectors)
    if "kept" in still:
        return still["kept"]
    img, typ = still["image"], still["type"]
    out = io.BytesIO()
    # Screenshots and transparent PNGs benefit most from modern encoding; an already lossy JPEG keeps its established encoder and quality.
    if still["ico"] or typ == "image/png":
        img.convert("RGBA" if "A" in img.getbands() or img.mode in ("P", "LA", "RGBA") else "RGB").save(
            out, "WEBP", quality=88, alpha_quality=100, method=4
        )
        return {"body": out.getvalue(), "type": "image/webp"}
    if typ == "image/webp":
        img.convert("RGBA" if "A" in img.getbands() else "RGB").save(out, "WEBP", quality=82)
        return {"body": out.getvalue(), "type": typ}
    img.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
    return {"body": out.getvalue(), "type": "image/jpeg"}


def resize_image(body: bytes, upstream_type: str, mode: str) -> dict:
    """Deterministic output for a signed rendition: the request's Accept header never changes the bytes."""
    return encode_image(body, upstream_type, mode, True)


# ── the disk cache (the same layout as the TypeScript's, so both read each other's files) ───────


def cache_dir() -> Path:
    return config().data_dir / "imgcache"


def cache_file(url: str, mode: str) -> Path:
    key = hashlib.sha256(f"{mode}|{url}".encode()).hexdigest()
    return cache_dir() / key[:2] / key


def _save(file: Path, body: bytes, typ: str) -> None:
    tmp = file.with_name(f"{file.name}.{uuid.uuid4()}.tmp")
    tmp.write_bytes(body)
    Path(f"{tmp}.type").write_text(typ, encoding="utf-8")
    tmp.replace(file)
    Path(f"{tmp}.type").replace(Path(f"{file}.type"))


def _exists(path: str | Path) -> bool:
    return Path(path).exists()


def _cached(file: Path, body: bytes, typ: str, mode: str) -> PreparedImage:
    # The actual WebP bytes identify a completed conversion, including one done by another process.
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        typ = "image/webp"
    if typ == "image/svg+xml":
        return PreparedImage(body, typ)
    if typ != "image/gif":
        return PreparedImage(body, typ)
    if _exists(f"{file}.prepared"):
        return PreparedImage(body, typ)
    try:
        with Image.open(io.BytesIO(body)) as img:
            pixels = img.width * img.height * _frames(img)
    except Exception:
        pixels = 0
    # Unsupported large animations are kept as they are; shortening their cache would only add traffic.
    return PreparedImage(body, typ, pending_animation=0 < pixels <= ANIMATION_MAX_PIXELS)


def produce_image(url: str, mode: str) -> PreparedImage:
    """The cached rendition of an image for a mode, fetched and resized on first use."""
    file = cache_file(url, mode)
    try:
        body, meta = file.read_bytes(), Path(f"{file}.type").read_text(encoding="utf-8")
        return _cached(file, body, meta, mode)
    except OSError:
        pass  # not cached
    res = original(url)
    image = resize_image(res.body, res.headers.get("content-type") or "", mode)
    file.parent.mkdir(parents=True, exist_ok=True)
    _save(file, image["body"], image["type"])
    return _cached(file, image["body"], image["type"], mode)


def produce_avif(url: str, mode: str) -> PreparedImage:
    """A rendition's AVIF variant once background preparation made one. Until then the rendition stands in (marked pending, so caches keep it
    a minute): a browser that chose the AVIF source always gets a picture, never an error."""
    file = cache_file(url, f"{mode}{AVIF_MODE_SUFFIX}")
    try:
        return PreparedImage(file.read_bytes(), "image/avif")
    except OSError:
        pass
    image = produce_image(url, mode)
    if image.pending_animation or image.type not in ("image/jpeg", "image/webp"):
        return image
    if _exists(f"{file}.prepared"):
        return image
    return PreparedImage(image.body, image.type, image.pending_animation, True)


# ── background preparation (media.prepare): animations as WebP, AVIF variants ──────────────────────

AVIF_QUALITIES = [50, 60, 70]  # qualities tried for an AVIF variant, lowest first; above the last one it is rarely smaller than the rendition


def convert_animated(url: str, mode: str) -> int:
    """Replaces a cached animated rendition (GIF passed through on the request path) with an animated WebP at the rendition's width, keeping every frame, its
    timing and the loop count, when that is clearly smaller. Background work only: decoding all frames is too heavy for a request. Returns the bytes saved (0 when
    nothing changed)."""
    file = cache_file(url, mode)
    try:
        body, typ = file.read_bytes(), Path(f"{file}.type").read_text(encoding="utf-8")
    except OSError:
        return 0
    if typ != "image/gif":
        return 0
    if _exists(f"{file}.prepared"):
        return 0
    try:
        with Image.open(io.BytesIO(body)) as gif:
            frames_n = _frames(gif)
            if not gif.width or not gif.height or gif.width * gif.height * frames_n > ANIMATION_MAX_PIXELS:
                return 0
            width = IMAGE_WIDTHS.get(mode, 1600)
            loop = gif.info.get("loop", 0)
            frames, durations = [], []
            for i in range(frames_n):
                gif.seek(i)
                frame = gif.convert("RGBA")
                if frame.width > width:
                    frame = frame.resize((width, max(1, round(frame.height * width / frame.width))), Image.LANCZOS)
                frames.append(frame)
                durations.append(gif.info.get("duration", 100))
            out = io.BytesIO()
            frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=loop, quality=80, method=4)
            webp = out.getvalue()
    except Exception:
        webp = None
    if not webp or len(webp) > len(body) * 0.85:
        # Preparation finished, but this GIF cannot be converted or the original is smaller. Keep it normally cached instead of repeatedly queueing the same source.
        Path(f"{file}.prepared").write_text("original", encoding="utf-8")
        return 0
    _save(file, webp, "image/webp")
    return len(body) - len(webp)


def _opaque(img: Image.Image) -> bool:
    if "A" not in img.getbands() and img.mode not in ("P", "LA", "RGBA"):
        return True
    alpha = img.convert("RGBA").getchannel("A")
    return alpha.getextrema()[0] == 255


def _avif(img: Image.Image, rendition: bytes) -> bytes | None:
    from newsroom.engine.media.similarity import luma, ssim

    reference = luma(img)
    target = ssim(reference, luma(rendition))
    if target is None:
        return None
    for quality in AVIF_QUALITIES:
        out = io.BytesIO()
        img.convert("RGB").save(out, "AVIF", quality=quality, speed=6)
        avif = out.getvalue()
        # A higher quality only adds bytes.
        if len(avif) > len(rendition) * 0.9:
            return None
        if (ssim(reference, luma(avif)) or 0) >= target:
            return avif
    return None


def convert_avif(url: str, mode: str) -> int:
    """Prepares a rendition's AVIF variant (media.prepare; several encodes are too heavy for a request): from the original, at the lowest quality whose SSIM against
    the lossless resize reaches the rendition's own, kept only when at least a tenth smaller. Pictures with transparency keep their rendition. Returns the bytes
    saved; 0 when the rendition stays, which is recorded so the variant is not tried again."""
    file = cache_file(url, f"{mode}{AVIF_MODE_SUFFIX}")
    if _exists(file) or _exists(f"{file}.prepared"):
        return 0
    rendition = produce_image(url, mode)
    avif = None
    if not rendition.pending_animation and rendition.type in ("image/jpeg", "image/webp"):
        res = original(url)
        still = still_rendition(res.body, res.headers.get("content-type") or "", mode, True)
        if "kept" not in still and _opaque(still["image"]):
            avif = _avif(still["image"], rendition.body)
    file.parent.mkdir(parents=True, exist_ok=True)
    if not avif:
        Path(f"{file}.prepared").write_text("original", encoding="utf-8")
        return 0
    _save(file, avif, "image/avif")
    return len(rendition.body) - len(avif)
