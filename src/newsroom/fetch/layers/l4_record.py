"""L4 (FET-FR-006): when no other layer gave a source's page a video and the topic is a reel, the page is opened in a browser, scrolled and
recorded for a few seconds (1080p, 30 fps MP4). One script for every page; it records what any visitor sees, so robots.txt is read first
like everywhere else, and a page that does not load or records black is a gap, never an empty video."""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from newsroom.core.http import Get, default_get
from newsroom.core.workspace import workspace_dir
from newsroom.fetch.attempts import Attempt, record_attempts
from newsroom.fetch.ffmpeg import ffmpeg
from newsroom.fetch.layers.l2_html import robots_allows

RECORD_SECONDS = 20
LOAD_TIMEOUT_MS = 30_000
MAX_BLACK_SHARE = 0.5  # a recording whose picture is black for this share of its length is thrown away

# Records `url` for `seconds` into a video file at `out` (any container the next step can read); raises when the page does not load.
Recorder = Callable[[str, str, int], None]


def browser_record(url: str, out: str, seconds: int) -> None:
    # playwright is only needed where recording is wanted, so it is not loaded for anything else.
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    work = tempfile.mkdtemp(prefix="l4-")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    record_video_dir=work,
                    record_video_size={"width": 1920, "height": 1080},
                    reduced_motion="no-preference",
                )
                page = context.new_page()
                page.goto(url, wait_until="load", timeout=LOAD_TIMEOUT_MS)
                page.wait_for_timeout(1500)
                # Scroll evenly down the page over the recording.
                steps = max(1, (seconds * 1000 - 1500) // 500)
                for i in range(steps):
                    page.evaluate(
                        f'window.scrollTo({{ top: ((document.documentElement.scrollHeight - window.innerHeight) * {i + 1}) / {steps}, behavior: "smooth" }})'
                    )
                    page.wait_for_timeout(500)
                video = page.video
                context.close()
                shutil.copyfile(video.path(), out)
            finally:
                browser.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def to_mp4(src: str, dest: str) -> float:
    """Converts a recording to the pipeline's shape (1080p, 30 fps, H.264, yuv420p, no sound) and says how much of it is black (0 to 1)."""
    ffmpeg(
        [
            "-i",
            src,
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-an",
            "-movflags",
            "+faststart",
            dest,
        ]
    )
    report = ffmpeg(["-i", dest, "-vf", "blackdetect=d=0.2:pix_th=0.1", "-an", "-f", "null", "-"])
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", report)
    length = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    black = sum(float(x) for x in re.findall(r"black_duration:(\d+(?:\.\d+)?)", report))
    return min(1.0, black / length) if length > 0 else 1.0


@dataclass
class Layer4Result:
    topic_id: str
    recorded: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # sources not recorded, with why: already has a video, a rule, or a failure


def fetch_layer4(
    conn,
    topic_id: str,
    record: Recorder | None = None,
    get: Get | None = None,
    seconds: int = RECORD_SECONDS,
    have_video: set[str] | None = None,
    formats: list[str] | None = None,
) -> Layer4Result:
    record = record or browser_record
    get = get or default_get
    result = Layer4Result(topic_id)
    topic_dir = workspace_dir() / "topics" / topic_id
    if formats is None:
        contents = topic_dir / "contents"
        formats = [n.name.split("-")[0] for n in contents.iterdir()] if contents.exists() else []
    if "reel" not in formats:
        return result
    raw_dir = topic_dir / "media" / "raw"
    sources = conn.execute(
        "SELECT source_id, url FROM topic_sources WHERE topic_id = %s AND url IS NOT NULL ORDER BY source_id", (topic_id,)
    ).fetchall()
    robots: dict[str, str] = {}
    conn.execute("DELETE FROM topic_fetch_gaps WHERE topic_id = %s AND layer = 4", (topic_id,))
    for source in sources:

        def skip(reason: str, failed: bool, _s=source) -> None:
            result.skipped.append({"sourceId": _s["source_id"], "url": _s["url"], "reason": reason, "failed": failed})
            if failed:
                conn.execute(
                    "INSERT INTO topic_fetch_gaps (topic_id, layer, need, reason, suggestion) VALUES (%s, 4, 'visual', %s, %s) ON CONFLICT DO NOTHING",
                    (topic_id, f"{_s['url']}：{reason}", "自己錄 30 秒操作畫面放進 media/raw/"),
                )

        if have_video and source["source_id"] in have_video:
            result.skipped.append({"sourceId": source["source_id"], "url": source["url"], "reason": "已有影片，不錄影", "failed": False})
            continue
        if not re.match(r"^https?:", source["url"], re.I):
            continue
        parts = urlsplit(source["url"])
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in robots:
            try:
                page = get(f"{origin}/robots.txt")
                robots[origin] = page.text if page.status == 200 else ""
            except Exception:  # noqa: BLE001
                robots[origin] = ""
        if not robots_allows(robots[origin], parts.path or "/"):
            skip("robots.txt 不允許讀取這個頁面", False)
            continue
        work = Path(tempfile.mkdtemp(prefix="l4-out-"))
        try:
            raw, mp4 = work / "raw.webm", work / "out.mp4"
            try:
                record(source["url"], str(raw), seconds)
            except Exception as e:  # noqa: BLE001
                skip(f"頁面沒有載入（{str(e).splitlines()[0][:100] if str(e) else type(e).__name__}）", True)
                continue
            black = to_mp4(str(raw), str(mp4))
            if black > MAX_BLACK_SHARE:
                skip(f"錄到的畫面大半是黑的（{round(black * 100)}%）", True)
                continue
            sha = hashlib.sha256(mp4.read_bytes()).hexdigest()
            raw_dir.mkdir(parents=True, exist_ok=True)
            dest = raw_dir / f"l4-{sha[:8]}.mp4"
            shutil.copyfile(mp4, dest)
            rel = dest.relative_to(topic_dir).as_posix()
            conn.execute(
                """INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
                   VALUES (%s, %s, %s, 'video', 'third_party', %s, %s, 4, 'playwright', %s)
                   ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL""",
                (topic_id, f"ast_{sha[:12]}", rel, source["source_id"], source["url"], sha),
            )
            result.recorded.append({"path": rel, "url": source["url"], "sourceId": source["source_id"]})
        except Exception as e:  # noqa: BLE001
            skip(str(e)[:120], True)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    record_attempts(conn, [Attempt(4, r["url"], True) for r in result.recorded])
    record_attempts(conn, [Attempt(4, s["url"], False, s["reason"]) for s in result.skipped if s["failed"]])
    return result
