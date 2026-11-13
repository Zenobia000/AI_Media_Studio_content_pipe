"""L3 (FET-FR-004): the downloaders for what a page does not hand over directly. yt-dlp for YouTube, X posts, Vimeo and the videos a page
embeds, gallery-dl for picture sets. Video is taken at most 1080p and H.264. A source whose page L2 already gave a video is not asked again.
Cookies of the person's own account may be named in YTDLP_COOKIES_FILE (the file never goes into version control); nothing here works
around a login or a bot check, it says what is needed."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from lxml import html as lxml_html

from newsroom.core.http import Get, default_get
from newsroom.core.workspace import workspace_dir
from newsroom.fetch.attempts import Attempt, record_attempts
from newsroom.fetch.layers.l2_html import MAX_BYTES

# yt-dlp's format choice: up to 1080p, H.264 video with AAC audio when there is one, else the best single file up to 1080p.
VIDEO_FORMAT = "bv*[height<=1080][vcodec~='^(avc|h264)']+ba[acodec~='^(mp4a|aac)']/b[height<=1080][ext=mp4]/b[height<=1080]"
YTDLP_HOSTS = re.compile(r"(^|\.)(youtube\.com|youtu\.be|vimeo\.com|x\.com|twitter\.com)$", re.I)
GALLERY_HOSTS = re.compile(r"(^|\.)(imgur\.com|flickr\.com|pinterest\.com|artstation\.com)$", re.I)
VIDEO_FILE = re.compile(r"\.(mp4|webm|mkv|mov|m4v)$", re.I)
IMAGE_FILE = re.compile(r"\.(png|jpe?g|gif|webp)$", re.I)


@dataclass
class ExecResult:
    code: int | str  # "ENOENT" when the program is missing
    out: str
    err: str


Exec = Callable[[str, list[str]], ExecResult]


def default_exec(command: str, args: list[str]) -> ExecResult:
    try:
        res = subprocess.run([command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15 * 60)
        return ExecResult(res.returncode, res.stdout, res.stderr)
    except FileNotFoundError:
        return ExecResult("ENOENT", "", f"{command}: not found")
    except subprocess.TimeoutExpired:
        return ExecResult(1, "", "timeout")


def tool(exec_: Exec, bin_: str, module: str) -> list[str] | None:
    """A downloader may be on PATH, or only reachable as a Python module."""
    for base in ([bin_], ["python", "-m", module], ["py", "-m", module]):
        if exec_(base[0], [*base[1:], "--version"]).code == 0:
            return base
    return None


def embedded_videos(html: str, base: str) -> list[str]:
    """The addresses a page embeds as players: a YouTube or Vimeo iframe becomes the page of that video."""
    doc = lxml_html.fromstring(html) if html.strip() else lxml_html.fromstring("<html></html>")
    out: dict[str, None] = {}
    for frame in doc.xpath("//iframe[@src or @data-src]"):
        try:
            src = urlsplit(urljoin(base, frame.get("src") or frame.get("data-src") or ""))
        except ValueError:
            continue
        host = src.hostname or ""
        yt = re.match(r"^/embed/([\w-]{11})", src.path)
        vm = re.match(r"^/video/(\d+)", src.path)
        if re.search(r"(^|\.)(youtube\.com|youtube-nocookie\.com)$", host) and yt:
            out[f"https://www.youtube.com/watch?v={yt.group(1)}"] = None
        elif host == "player.vimeo.com" and vm:
            out[f"https://vimeo.com/{vm.group(1)}"] = None
    return list(out)


@dataclass
class Failure:
    code: str  # JS_RUNTIME | LOGIN | OUTDATED | MISSING_TOOL | TOO_LARGE | FAILED
    message: str


def explain(err: str, downloader: str) -> Failure:
    """What a downloader's complaint means for the person, and what to do about it."""
    update = "python -m pip install -U yt-dlp" if downloader == "yt-dlp" else "python -m pip install -U gallery-dl"
    if re.search(r"javascript runtime|no supported js|deno", err, re.I):
        return Failure(
            "JS_RUNTIME",
            "yt-dlp 讀 YouTube 需要 JavaScript 執行環境。請安裝 Deno（Windows：`winget install DenoLand.Deno`），或確認 Node.js 在 PATH 裡，再重試",
        )
    if re.search(r"sign in|log ?in|cookies|age-restricted|private|members-only|confirm you.re not a bot", err, re.I):
        return Failure(
            "LOGIN",
            "這個影片要登入才能看。若要用自己的帳號，匯出 cookies 檔並用環境變數 YTDLP_COOKIES_FILE 指定路徑；或改請人自己錄影放進 media/raw/",
        )
    if re.search(r"please update|out of date|outdated|unable to extract|nsig extraction|http error 403", err, re.I):
        return Failure("OUTDATED", f"{downloader} 可能太舊了，網站改版後常會失敗。更新：`{update}`")
    if re.search(r"larger than max-filesize|file is larger", err, re.I):
        return Failure("TOO_LARGE", "檔案超過 500 MB，沒有下載")
    errors = [ln for ln in err.split("\n") if re.search(r"error", ln, re.I)]
    return Failure("FAILED", errors[-1][:200] if errors else err[-200:])


@dataclass
class Layer3Result:
    topic_id: str
    downloaded: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # sources left alone because an earlier layer already gave them a video


def fetch_layer3(
    conn,
    topic_id: str,
    exec_: Exec | None = None,
    get: Get | None = None,
    have_video: set[str] | None = None,
    cookies_file: str | None | bool = True,
    kinds: frozenset[str] | None = None,
) -> Layer3Result:
    """`kinds` limits what is downloaded to "video" (yt-dlp) and/or "image" (gallery-dl); None takes both."""
    exec_ = exec_ or default_exec
    get = get or default_get
    cookies = os.environ.get("YTDLP_COOKIES_FILE") if cookies_file is True else cookies_file
    result = Layer3Result(topic_id)
    topic_dir = workspace_dir() / "topics" / topic_id
    raw_dir = topic_dir / "media" / "raw"
    sources = conn.execute(
        "SELECT source_id, url FROM topic_sources WHERE topic_id = %s AND url IS NOT NULL ORDER BY source_id", (topic_id,)
    ).fetchall()
    cache: dict[str, list[str] | None] = {}

    def register(file: Path, kind: str, method: str, source_id: str, url: str) -> None:
        body = file.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        dest = raw_dir / f"l3-{sha[:8]}{file.suffix.lower()}"
        if file != dest:
            try:
                file.replace(dest)
            except OSError:
                file.unlink(missing_ok=True)
        rel = dest.relative_to(topic_dir).as_posix()
        conn.execute(
            """INSERT INTO topic_assets (topic_id, asset_id, path, type, origin, source_id, source_url, fetch_layer, fetch_method, sha256)
               VALUES (%s, %s, %s, %s, 'third_party', %s, %s, 3, %s, %s)
               ON CONFLICT (topic_id, asset_id) DO UPDATE SET path = EXCLUDED.path, deleted_at = NULL""",
            (topic_id, f"ast_{sha[:12]}", rel, kind, source_id, url, method, sha),
        )
        result.downloaded.append({"path": rel, "url": url, "kind": kind, "method": method, "sourceId": source_id})

    def video(url: str, source_id: str) -> None:
        if "yt-dlp" not in cache:
            cache["yt-dlp"] = tool(exec_, "yt-dlp", "yt_dlp")
        ytdlp = cache["yt-dlp"]
        if not ytdlp:
            result.failed.append({"url": url, "code": "MISSING_TOOL", "message": "找不到 yt-dlp。安裝：`python -m pip install yt-dlp`"})
            return
        raw_dir.mkdir(parents=True, exist_ok=True)
        args = [
            *ytdlp[1:],
            "-f",
            VIDEO_FORMAT,
            "--merge-output-format",
            "mp4",
            "--no-playlist",
            "--max-filesize",
            f"{round(MAX_BYTES / 1024 / 1024)}M",
            "--restrict-filenames",
            "--no-progress",
            "--no-warnings",
            "-o",
            str(raw_dir / ".l3-%(id)s.%(ext)s"),
            "--print",
            "after_move:filepath",
            *(["--cookies", cookies] if cookies else []),
            url,
        ]
        r = exec_(ytdlp[0], args)
        files = [ln.strip() for ln in r.out.splitlines() if VIDEO_FILE.search(ln.strip())]
        if r.code != 0 or not files:
            f = explain(f"{r.err}\n{r.out}", "yt-dlp")
            result.failed.append({"url": url, "code": f.code, "message": f.message})
            return
        register(Path(files[-1]), "video", "yt-dlp", source_id, url)

    def images(url: str, source_id: str) -> None:
        if "gallery-dl" not in cache:
            cache["gallery-dl"] = tool(exec_, "gallery-dl", "gallery_dl")
        gallery = cache["gallery-dl"]
        if not gallery:
            result.failed.append({"url": url, "code": "MISSING_TOOL", "message": "找不到 gallery-dl。安裝：`python -m pip install gallery-dl`"})
            return
        raw_dir.mkdir(parents=True, exist_ok=True)
        tmp = raw_dir / f".l3-gallery-{hashlib.sha1(url.encode()).hexdigest()[:8]}"
        r = exec_(
            gallery[0],
            [
                *gallery[1:],
                "-D",
                str(tmp),
                "--range",
                "1-10",
                "--filesize-max",
                f"{round(MAX_BYTES / 1024 / 1024)}M",
                *(["--cookies", cookies] if cookies else []),
                url,
            ],
        )
        got = sorted(n.name for n in tmp.iterdir() if IMAGE_FILE.search(n.name)) if tmp.exists() else []
        if r.code != 0 and not got:
            f = explain(f"{r.err}\n{r.out}", "gallery-dl")
            result.failed.append({"url": url, "code": f.code, "message": f.message})
        for name in got:
            register(tmp / name, "image", "gallery-dl", source_id, url)
        shutil.rmtree(tmp, ignore_errors=True)

    for source in sources:
        if have_video and source["source_id"] in have_video:
            result.skipped.append(source["source_id"])
            continue
        host = urlsplit(source["url"]).hostname or ""
        wanted: list[tuple[str, str]] = []
        if YTDLP_HOSTS.search(host):
            wanted.append(("video", source["url"]))
        if GALLERY_HOSTS.search(host) or re.search(r"(^|\.)(x|twitter)\.com$", host):
            wanted.append(("images", source["url"]))
        if not wanted:
            try:
                page = get(source["url"])
            except Exception:  # noqa: BLE001
                page = None
            if page and page.status == 200 and "html" in page.type:
                wanted += [("video", e) for e in embedded_videos(page.text, page.url)]

        def got_video(_s=source) -> bool:
            return any(d["sourceId"] == _s["source_id"] and d["kind"] == "video" for d in result.downloaded)

        # A post with a video is taken as that video; its pictures are looked for only when there is none.
        for kind, url in wanted:
            if kind == "video":
                if kinds is None or "video" in kinds:
                    video(url, source["source_id"])
            elif not got_video() and (kinds is None or "image" in kinds):
                images(url, source["source_id"])
    record_attempts(conn, [Attempt(3, u, True) for u in dict.fromkeys(d["url"] for d in result.downloaded)])
    record_attempts(conn, [Attempt(3, f["url"], False, f["message"]) for f in result.failed])
    return result
