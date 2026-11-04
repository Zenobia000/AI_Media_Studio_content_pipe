"""L2 (FET-FR-003): the media a page gives directly: <video> and <source>, og:video, twitter:player:stream, GIFs, the main picture, and the
media a repository's README links. Nothing is rendered or recorded, only downloaded: the robots.txt of the site is read first, a host is
asked at most once a second, a file over 500 MB is skipped and left as a gap, and a picture with a side under 400 px (a logo, an icon) is
dropped."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from newsroom.core.http import Binary, Get, GetBinary, default_get, default_get_binary
from newsroom.core.urls import site_identity_for_url
from newsroom.core.workspace import workspace_dir
from newsroom.fetch.assets import probe
from newsroom.fetch.attempts import Attempt, record_attempts

MAX_BYTES = 500 * 1024 * 1024
MIN_SIDE = 400
MAX_PER_PAGE = 10  # a page can name dozens of clips; the first ten (videos before GIFs before pictures) are enough to choose from
ROBOTS_NAMES = ("mediahotbot", "*")
VIDEO_EXT = re.compile(r"\.(mp4|webm|mov|m4v)(\?|#|$)", re.I)
GIF_EXT = re.compile(r"\.gif(\?|#|$)", re.I)
IMAGE_EXT = re.compile(r"\.(png|jpe?g|webp|avif)(\?|#|$)", re.I)
GITHUB_ASSET = re.compile(r"^https://github\.com/user-attachments/assets/[0-9a-f-]{36}$", re.I)  # no extension on a README video
EXTENSION_BY_TYPE = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "image/gif": ".gif",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


@dataclass
class MediaCandidate:
    url: str
    kind: str  # video | gif | image
    via: str  # video-tag, source-tag, og:video, twitter:player, gif-img, og:image, link, readme


def _absolute(href: str | None, base: str) -> str | None:
    if not href:
        return None
    try:
        url = urljoin(base, href.strip())
    except ValueError:
        return None
    return url if urlsplit(url).scheme in ("http", "https") else None


def classify(url: str) -> str | None:
    if VIDEO_EXT.search(url) or GITHUB_ASSET.match(url):
        return "video"
    if GIF_EXT.search(url):
        return "gif"
    if IMAGE_EXT.search(url):
        return "image"
    return None


def find_media(html: str, page_url: str) -> list[MediaCandidate]:
    """The media a web page names."""
    doc = lxml_html.fromstring(html) if html.strip() else lxml_html.fromstring("<html></html>")
    out: dict[str, MediaCandidate] = {}

    def add(href: str | None, via: str, kind: str | None = None) -> None:
        url = _absolute(href, page_url)
        k = (kind or classify(url)) if url else None
        if url and k and url not in out:
            out[url] = MediaCandidate(url, k, via)

    for v in doc.xpath("//video[@src]"):
        add(v.get("src"), "video-tag", "video")
    for s in doc.xpath("//source[@src]"):
        if re.match(r"^video/", s.get("type") or "", re.I) or VIDEO_EXT.search(s.get("src") or ""):
            add(s.get("src"), "source-tag", "video")
    for name in ("og:video", "og:video:url", "og:video:secure_url"):
        for c in doc.xpath(f'//meta[@property="{name}"]/@content')[:1]:
            add(c, "og:video", "video")
    for c in doc.xpath('//meta[@name="twitter:player:stream" or @property="twitter:player:stream"]/@content')[:1]:
        add(c, "twitter:player", "video")
    for img in doc.xpath("//img"):
        src = img.get("src") or img.get("data-src")
        if src and GIF_EXT.search(src):
            add(src, "gif-img", "gif")
    for a in doc.xpath("//a[@href]"):
        href = a.get("href")
        if href and (VIDEO_EXT.search(href) or GIF_EXT.search(href)):
            add(href, "link")
    for c in doc.xpath('//meta[@property="og:image" or @property="og:image:secure_url"]/@content')[:1]:
        add(c, "og:image", "image")
    return list(out.values())


_README_URL = re.compile(r"""(?:\]\(|src=["']|href=["']|^|\s)(https?://[^\s)"'<>]+|(?:\.{0,2}/)?[\w./%-]+\.(?:mp4|webm|mov|gif))""", re.I | re.M)


def find_media_in_markdown(markdown: str, base_url: str) -> list[MediaCandidate]:
    """The media a README (Markdown) links or embeds."""
    out: dict[str, MediaCandidate] = {}
    for m in _README_URL.finditer(markdown):
        url = _absolute(m.group(1), base_url)
        kind = classify(url) if url else None
        if url and kind and kind != "image" and url not in out:
            out[url] = MediaCandidate(url, kind, "readme")
    return list(out.values())


def robots_allows(robots: str, pathname: str) -> bool:
    """Whether robots.txt lets the crawler fetch a path: the longest matching rule wins, Allow over Disallow on a tie."""
    applies = False
    best: tuple[int, bool] | None = None
    group: list[str] = []
    in_rules = False
    for raw in re.split(r"\r?\n", robots):
        line = re.sub(r"#.*", "", raw).strip()
        if ":" not in line:
            continue
        field_, _, value = line.partition(":")
        key, value = field_.strip().lower(), value.strip()
        if not key:
            continue
        if key == "user-agent":
            if in_rules:
                group = []
            in_rules = False
            group.append(value.lower())
            applies = any(g in ROBOTS_NAMES for g in group)
        elif key in ("disallow", "allow") and applies:
            in_rules = True
            if not value:
                continue
            body = re.escape(value).replace(r"\*", ".*")
            pattern = "^" + (body[:-2] + "$" if body.endswith(r"\$") else body)
            if re.match(pattern, pathname) and (best is None or len(value) > best[0] or (len(value) == best[0] and key == "allow")):
                best = (len(value), key == "allow")
    return best[1] if best else True


@dataclass
class Layer2Result:
    topic_id: str
    downloaded: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # `failed` is False for a rule (robots.txt, a small picture, the cap), True for a fault


def _candidates_of(url: str, get: Get, wait: Callable[[str], None]) -> list[MediaCandidate]:
    site = site_identity_for_url(url)
    wait(urlsplit(url).netloc)
    if site and site.startswith("github:"):
        owner, repo = [p for p in urlsplit(url).path.split("/") if p][:2]
        readme = get(f"https://api.github.com/repos/{owner}/{repo}/readme", {"accept": "application/vnd.github.raw+json"})
        if readme.status != 200:
            raise RuntimeError(f"README HTTP {readme.status}")
        return find_media_in_markdown(readme.text, f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/")
    page = get(url, {"accept-language": "zh-TW,zh;q=0.9,en;q=0.8"})
    if page.status != 200 or "html" not in page.type:
        raise RuntimeError(f"HTTP {page.status}")
    return find_media(page.text, page.url)


def fetch_layer2(
    conn,
    topic_id: str,
    get: Get | None = None,
    get_binary: GetBinary | None = None,
    delay: float = 1.0,
    max_bytes: int = MAX_BYTES,
    max_per_page: int = MAX_PER_PAGE,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Layer2Result:
    get = get or default_get
    get_binary = get_binary or default_get_binary
    result = Layer2Result(topic_id)
    topic_dir = workspace_dir() / "topics" / topic_id
    raw_dir = topic_dir / "media" / "raw"
    sources = conn.execute(
        "SELECT source_id, url FROM topic_sources WHERE topic_id = %s AND url IS NOT NULL ORDER BY source_id", (topic_id,)
    ).fetchall()
    gaps: dict[str, tuple[str, str, str]] = {}
    robots: dict[str, str] = {}
    last_at: dict[str, float] = {}

    def wait(host: str) -> None:
        gap = last_at.get(host, -1e9) + delay - clock()
        if gap > 0:
            sleep(gap)
        last_at[host] = clock()

    def allowed(url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in robots:
            wait(parts.netloc)
            try:
                page = get(f"{origin}/robots.txt")
                robots[origin] = page.text if page.status == 200 else ""
            except Exception:  # noqa: BLE001
                robots[origin] = ""
        return robots_allows(robots[origin], parts.path or "/")

    order = {"video": 0, "gif": 1, "image": 2}
    for source in sources:
        try:
            if not allowed(source["url"]):
                result.skipped.append({"url": source["url"], "reason": "robots.txt 不允許讀取這個頁面", "failed": False})
                continue
            candidates = _candidates_of(source["url"], get, wait)
        except Exception as e:  # noqa: BLE001
            result.skipped.append({"url": source["url"], "reason": f"頁面讀不到：{str(e)[:100]}", "failed": True})
            continue
        ranked = sorted(candidates, key=lambda c: order[c.kind])
        if len(ranked) > max_per_page:
            result.skipped.append(
                {"url": source["url"], "reason": f"頁面列出 {len(ranked)} 個媒體，只取前 {max_per_page} 個（影片優先）", "failed": False}
            )
        for media in ranked[:max_per_page]:

            def skip(reason: str, failed: bool = True, _m=media) -> None:
                result.skipped.append({"url": _m.url, "reason": reason, "failed": failed})

            try:
                if not allowed(media.url):
                    skip("robots.txt 不允許下載這個檔案", False)
                    continue
                wait(urlsplit(media.url).netloc)
                # The size a server states is a shortcut, not a gate: some refuse HEAD (a signed address allows only GET).
                try:
                    head: Binary | None = get_binary(media.url, head=True, max_bytes=max_bytes)
                except Exception:  # noqa: BLE001
                    head = None
                limit = f"{round(max_bytes / 1024 / 1024)} MB"

                def too_big(_m=media, limit=limit) -> None:
                    gaps[_m.url] = ("media", f"{_m.url} 超過 {limit}，沒有下載", "手動下載需要的片段，或錄一段較短的畫面，放進 media/raw/")
                    skip(f"超過 {limit}", True, _m)

                if head and head.status < 400 and head.length is not None and head.length > max_bytes:
                    too_big()
                    continue
                wait(urlsplit(media.url).netloc)
                try:
                    file = get_binary(media.url, max_bytes=max_bytes)
                except Exception as e:  # noqa: BLE001
                    if re.search(r"too large", str(e), re.I):
                        too_big()
                    else:
                        skip(str(e)[:120])
                    continue
                if file.status != 200 or not file.body:
                    skip(f"HTTP {file.status}")
                    continue
                ctype = file.type.split(";")[0].strip()
                kind = (
                    "video"
                    if ctype.startswith("video/")
                    else "gif"
                    if ctype == "image/gif"
                    else "image"
                    if ctype.startswith("image/")
                    else media.kind
                )
                ext = EXTENSION_BY_TYPE.get(ctype) or Path(urlsplit(media.url).path).suffix
                sha = hashlib.sha256(file.body).hexdigest()
                raw_dir.mkdir(parents=True, exist_ok=True)
                temp = raw_dir / f".l2-{sha[:8]}.tmp"
                temp.write_bytes(file.body)
                if kind != "video":
                    try:
                        size = probe(temp)
                    except Exception:  # noqa: BLE001
                        size = None
                    if not size or (size["width"] or 0) < MIN_SIDE or (size["height"] or 0) < MIN_SIDE:
                        temp.unlink(missing_ok=True)
                        skip(f"圖片太小（{(size or {}).get('width', '?')}×{(size or {}).get('height', '?')}，需要兩邊都至少 {MIN_SIDE} px）", False)
                        continue
                dest = raw_dir / f"l2-{sha[:8]}{ext or ('.mp4' if kind == 'video' else '.bin')}"
                temp.replace(dest)
                rel = dest.relative_to(topic_dir).as_posix()
                method = "html-image" if media.via in ("og:image", "gif-img") else "readme-media" if media.via == "readme" else "html-video"
                conn.execute(
                    """INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
                       VALUES (%s, %s, %s, %s, 'official', %s, %s, 2, %s, %s)
                       ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL""",
                    (topic_id, f"ast_{sha[:12]}", rel, kind, source["source_id"], media.url, method, sha),
                )
                result.downloaded.append({"path": rel, "url": media.url, "kind": kind, "method": method, "sourceId": source["source_id"]})
            except Exception as e:  # noqa: BLE001
                skip(str(e)[:120])

    conn.execute("DELETE FROM topic_fetch_gaps WHERE topic_id = %s AND layer = 2", (topic_id,))
    for need, reason, suggestion in gaps.values():
        conn.execute(
            "INSERT INTO topic_fetch_gaps (topic_id, layer, need, reason, suggestion) VALUES (%s, 2, %s, %s, %s) ON CONFLICT DO NOTHING",
            (topic_id, need, reason, suggestion),
        )
    record_attempts(conn, [Attempt(2, u, True) for u in dict.fromkeys(d["url"] for d in result.downloaded)])
    record_attempts(conn, [Attempt(2, s["url"], False, s["reason"]) for s in result.skipped if s.get("failed")])
    return result
