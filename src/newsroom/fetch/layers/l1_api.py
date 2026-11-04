"""L1 (FET-FR-005): the media a platform hands over itself, the steadiest way to a video. An X post: the rendition with the highest bitrate
among its mp4 files, from the post as the API gives it. A Threads post: the video its page names (og:video). A source that gets a video
here is not asked again by the layers below; a source that does not simply falls through to them."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from lxml import html as lxml_html

from newsroom.core.config import credential
from newsroom.core.http import Get, GetBinary, default_get, default_get_binary
from newsroom.core.workspace import workspace_dir
from newsroom.fetch.attempts import Attempt, record_attempts

MAX_BYTES = 500 * 1024 * 1024
X_STATUS = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/[^/]+/status/(\d+)", re.I)
THREADS_POST = re.compile(r"^https?://(?:www\.)?threads\.(?:net|com)/@[^/]+/post/", re.I)

LookupTweet = Callable[[str], dict | None]


@dataclass
class Layer1Result:
    topic_id: str
    downloaded: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # sources that gave nothing here, with why; they go on to the next layer


def best_mp4(media: list[dict]) -> str | None:
    """The mp4 with the highest bitrate among the renditions of the post's videos; None when it has none."""
    files = [
        v
        for m in media
        if m.get("type") != "photo"
        for v in (m.get("video_info") or {}).get("variants", [])
        if v.get("content_type") == "video/mp4" and isinstance(v.get("bitrate"), (int, float))
    ]
    files.sort(key=lambda v: v["bitrate"], reverse=True)
    return files[0]["url"] if files else None


def threads_video(html: str, base: str) -> str | None:
    doc = lxml_html.fromstring(html) if html.strip() else lxml_html.fromstring("<html></html>")
    for selector in (
        '//meta[@property="og:video"]/@content',
        '//meta[@property="og:video:secure_url"]/@content',
        '//meta[@name="twitter:player:stream"]/@content',
    ):
        for content in doc.xpath(selector)[:1]:
            if not content.strip():
                continue
            url = urljoin(base, content.strip())
            if urlsplit(url).scheme == "https":
                return url
    return None


def socialdata_lookup(tweet_id: str) -> dict | None:
    """One post by id from SocialData (paid; needs SOCIALDATA_API_KEY)."""
    key = credential("collectors", "SOCIALDATA_API_KEY")
    if not key:
        raise RuntimeError("SOCIALDATA_API_KEY is not configured")
    base = (credential("collectors", "SOCIALDATA_BASE_URL") or "https://api.socialdata.tools").rstrip("/")
    res = httpx.get(f"{base}/twitter/tweets/{tweet_id}", headers={"authorization": f"Bearer {key}", "accept": "application/json"}, timeout=20.0)
    return res.json() if res.status_code == 200 else None


def fetch_layer1(
    conn,
    topic_id: str,
    lookup: LookupTweet | None = None,
    get: Get | None = None,
    get_binary: GetBinary | None = None,
    max_bytes: int = MAX_BYTES,
    x_configured: bool | None = None,
) -> Layer1Result:
    lookup = lookup or socialdata_lookup
    get = get or default_get
    get_binary = get_binary or default_get_binary
    configured = bool(credential("collectors", "SOCIALDATA_API_KEY")) if x_configured is None else x_configured
    result = Layer1Result(topic_id)
    topic_dir = workspace_dir() / "topics" / topic_id
    raw_dir = topic_dir / "media" / "raw"
    sources = conn.execute(
        "SELECT source_id, url FROM topic_sources WHERE topic_id = %s AND url IS NOT NULL ORDER BY source_id", (topic_id,)
    ).fetchall()

    for source in sources:
        x = X_STATUS.match(source["url"])
        threads = bool(THREADS_POST.match(source["url"]))
        if not x and not threads:
            continue

        def skip(reason: str, failed: bool, _u=source["url"]) -> None:
            # Not a failure when nothing was tried: it does not count against the layer.
            result.skipped.append({"url": _u, "reason": reason, "failed": failed})

        try:
            if x:
                if not configured:
                    skip("沒有設定 X 的資料金鑰，略過 L1", False)
                    continue
                tweet = lookup(x.group(1))
                if not tweet:
                    skip("查不到這則貼文", True)
                    continue
                file = best_mp4((tweet.get("extended_entities") or tweet.get("entities") or {}).get("media", []))
                if not file:
                    skip("這則貼文沒有影片", False)
                    continue
            else:
                page = get(source["url"])
                if page.status != 200:
                    skip(f"頁面 HTTP {page.status}", True)
                    continue
                file = threads_video(page.text, source["url"])
                if not file:
                    skip("頁面沒有影片", False)
                    continue
            binary = get_binary(file, max_bytes=max_bytes)
            if binary.status != 200 or not binary.body:
                skip(f"影片檔 HTTP {binary.status}", True)
                continue
            sha = hashlib.sha256(binary.body).hexdigest()
            raw_dir.mkdir(parents=True, exist_ok=True)
            temp = raw_dir / f".l1-{sha[:8]}.tmp"
            temp.write_bytes(binary.body)
            dest = raw_dir / f"l1-{sha[:8]}.mp4"
            temp.replace(dest)
            rel = dest.relative_to(topic_dir).as_posix()
            method = "x-api" if x else "threads-og"
            conn.execute(
                """INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
                   VALUES (%s, %s, %s, 'video', 'official', %s, %s, 1, %s, %s)
                   ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL""",
                (topic_id, f"ast_{sha[:12]}", rel, source["source_id"], file, method, sha),
            )
            result.downloaded.append({"path": rel, "url": source["url"], "kind": "video", "method": method, "sourceId": source["source_id"]})
        except Exception as e:  # noqa: BLE001
            skip(str(e)[:120], True)
    record_attempts(conn, [Attempt(1, d["url"], True) for d in result.downloaded])
    record_attempts(conn, [Attempt(1, s["url"], False, s["reason"]) for s in result.skipped if s["failed"]])
    return result
