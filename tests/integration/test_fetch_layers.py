"""Ported from layer1–4, assets, preprocess, scenes tests: the fetch layers with stand-ins for the network and the downloaders, and real
ffmpeg for the media (skipped without it)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from newsroom.collect.inbox_writer import write_inbox
from newsroom.collect.manual import add_source
from newsroom.collect.topics import open_topic
from newsroom.core.http import Binary, Page
from newsroom.fetch.assets import Asset, gaps_for, inventory, type_of
from newsroom.fetch.attempts import fetch_stats
from newsroom.fetch.layers.l1_api import best_mp4, fetch_layer1, threads_video
from newsroom.fetch.layers.l2_html import fetch_layer2, find_media, find_media_in_markdown, robots_allows
from newsroom.fetch.layers.l3_dl import VIDEO_FORMAT, ExecResult, embedded_videos, explain, fetch_layer3
from newsroom.fetch.layers.l4_record import fetch_layer4, to_mp4
from newsroom.fetch.preprocess import measure_lufs, preprocess
from newsroom.fetch.scenes import detect_scenes, ranges_from_cuts

pytestmark = pytest.mark.usefixtures("conn")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


def offline(url, headers=None):
    raise OSError("offline")


def no_page(url, headers=None):
    return Page(404, url, "text/plain", "")


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, bytes]:
    """Small real files made with ffmpeg."""
    if not HAS_FFMPEG:
        return {}
    d = tmp_path_factory.mktemp("fixtures")
    out: dict[str, bytes] = {}

    def make(name: str, *args: str) -> None:
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(d / name)], check=True)
        out[name] = (d / name).read_bytes()

    make("v1.mp4", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25", "-pix_fmt", "yuv420p")
    make("v2.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=25", "-pix_fmt", "yuv420p")
    make("anim.gif", "-f", "lavfi", "-i", "testsrc=duration=1:size=500x400:rate=5")
    make("hero.png", "-f", "lavfi", "-i", "color=c=blue:size=800x600", "-frames:v", "1")
    make("logo.png", "-f", "lavfi", "-i", "color=c=red:size=100x100", "-frames:v", "1")
    return out


@pytest.fixture()
def workspace(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSROOM_HOME", str(tmp_path))
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    return tmp_path


def topic_at(conn, url: str, formats=("reel",), title: str | None = None):
    added = add_source(conn, url, title=title or f"補料 {url[-12:]}", get=offline)
    write_inbox(conn)
    return open_topic(conn, [f"cand_{added.article_id}"], formats=list(formats))


def assets_of(conn, topic_id):
    return conn.execute("SELECT origin, fetch_layer, fetch_method FROM topic_assets WHERE topic_id = %s ORDER BY asset_id", (topic_id,)).fetchall()


# ── L1 ──────────────────────────────────────────────────────────────────────────────────


def variant(bitrate=None, content_type="video/mp4", url=""):
    v = {"content_type": content_type, "url": url or f"https://video.example/{bitrate}.mp4"}
    if bitrate is not None:
        v["bitrate"] = bitrate
    return v


def test_best_mp4_is_the_highest_bitrate_never_the_playlist_or_a_photo():
    video = {
        "type": "video",
        "video_info": {"variants": [variant(256000), variant(2176000), variant(832000), variant(content_type="application/x-mpegURL")]},
    }
    assert best_mp4([video]) == "https://video.example/2176000.mp4"
    assert best_mp4([{"type": "video", "video_info": {"variants": [variant(content_type="application/x-mpegURL")]}}]) is None
    assert best_mp4([{"type": "photo"}]) is None and best_mp4([]) is None


def test_threads_page_names_its_video_with_og_video():
    base = "https://www.threads.net/@a/post/x"
    assert threads_video('<meta property="og:video" content="https://cdn.example/v.mp4">', base) == "https://cdn.example/v.mp4"
    assert threads_video('<meta property="og:video" content="http://cdn.example/v.mp4">', base) is None
    assert threads_video("<html></html>", base) is None


def test_x_video_post_gives_its_best_mp4_through_l1_and_l3_is_not_asked(conn, workspace, tag):
    t = topic_at(conn, f"https://x.com/acme_{tag}/status/19000{tag.replace('x', '')}001")
    body = b"not really a video, but bytes"
    got: list[str] = []

    def get_binary(url, head=False, max_bytes=0):
        got.append(url)
        return Binary(200, url, "video/mp4", len(body), body)

    tweet = {"extended_entities": {"media": [{"type": "video", "video_info": {"variants": [variant(320000), variant(2176000)]}}]}}
    result = fetch_layer1(conn, t.topic_id, lookup=lambda _id: tweet, get_binary=get_binary, x_configured=True)
    assert got == ["https://video.example/2176000.mp4"]
    assert [(d["kind"], d["method"]) for d in result.downloaded] == [("video", "x-api")]
    assert [(r["origin"], r["fetch_layer"], r["fetch_method"]) for r in assets_of(conn, t.topic_id)] == [("official", 1, "x-api")]
    assert (workspace / "topics" / t.topic_id / result.downloaded[0]["path"]).read_bytes() == body

    calls: list = []

    def exec_(command, args):
        calls.append(command)
        return ExecResult(1, "", "should not run")

    l3 = fetch_layer3(conn, t.topic_id, exec_=exec_, get=no_page, cookies_file=None, have_video={d["sourceId"] for d in result.downloaded})
    assert calls == [] and l3.downloaded == []


def test_when_l1_gets_nothing_the_source_is_left_for_the_next_layer(conn, workspace, tag):
    t = topic_at(conn, f"https://x.com/acme_{tag}/status/19000{tag.replace('x', '')}002")

    def never(_id):
        raise AssertionError("must not be called")

    off = fetch_layer1(conn, t.topic_id, lookup=never, x_configured=False)
    assert off.downloaded == [] and [s["failed"] for s in off.skipped] == [False]
    photo = fetch_layer1(conn, t.topic_id, lookup=lambda _id: {"extended_entities": {"media": [{"type": "photo"}]}}, x_configured=True)
    assert [s["failed"] for s in photo.skipped] == [False]
    gone = fetch_layer1(conn, t.topic_id, lookup=lambda _id: None, x_configured=True)
    assert [s["failed"] for s in gone.skipped] == [True]
    tweet = {"extended_entities": {"media": [{"type": "video", "video_info": {"variants": [variant(1)]}}]}}
    refused = fetch_layer1(conn, t.topic_id, lookup=lambda _id: tweet, get_binary=lambda url, **k: Binary(403, url, "", None, b""), x_configured=True)
    assert refused.downloaded == [] and "HTTP 403" in refused.skipped[0]["reason"]
    assert assets_of(conn, t.topic_id) == []


def test_a_threads_post_is_read_for_og_video_and_other_sources_are_not_touched(conn, workspace, tag):
    t = topic_at(conn, f"https://www.threads.net/@acme{tag}/post/AbC123")
    got: list[str] = []

    def get(url, headers=None):
        return Page(200, url, "text/html", '<meta property="og:video" content="https://cdn.example/t.mp4">')

    def get_binary(url, head=False, max_bytes=0):
        got.append(url)
        return Binary(200, url, "video/mp4", 3, b"abc")

    fetch_layer1(conn, t.topic_id, get=get, get_binary=get_binary, lookup=lambda _id: pytest.fail("X lookup must not run"))
    assert got == ["https://cdn.example/t.mp4"]
    assert [(r["fetch_layer"], r["fetch_method"]) for r in assets_of(conn, t.topic_id)] == [(1, "threads-og")]
    plain = topic_at(conn, f"https://news.example.com/plain-{tag}")
    none = fetch_layer1(conn, plain.topic_id, lookup=lambda _id: pytest.fail("no"))
    assert (none.downloaded, none.skipped) == ([], [])


# ── L2 ──────────────────────────────────────────────────────────────────────────────────

PAGE = """<html><head>
<meta property="og:video" content="/media/og.mp4"><meta property="og:image" content="https://cdn.example/hero.png">
<meta name="twitter:player:stream" content="https://cdn.example/player.mp4"></head><body>
<video src="clip.mp4"></video><video><source src="/media/s.webm" type="video/webm"></video>
<img src="/img/anim.gif"><a href="/files/demo.mov">demo</a><a href="/about">about</a></body></html>"""


def test_which_media_a_page_names_in_each_of_the_ways():
    found = {m.url: (m.kind, m.via) for m in find_media(PAGE, "https://example.com/post/")}
    assert found["https://example.com/post/clip.mp4"] == ("video", "video-tag")
    assert found["https://example.com/media/s.webm"] == ("video", "source-tag")
    assert found["https://example.com/media/og.mp4"] == ("video", "og:video")
    assert found["https://cdn.example/player.mp4"] == ("video", "twitter:player")
    assert found["https://example.com/img/anim.gif"] == ("gif", "gif-img")
    assert found["https://example.com/files/demo.mov"][0] == "video"
    assert found["https://cdn.example/hero.png"] == ("image", "og:image")
    assert not any("about" in u for u in found)
    readme = find_media_in_markdown(
        "![d](https://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc)\n[v](docs/demo.mp4)", "https://raw.x/o/r/HEAD/"
    )
    assert {m.url for m in readme} == {
        "https://github.com/user-attachments/assets/12345678-1234-1234-1234-123456789abc",
        "https://raw.x/o/r/HEAD/docs/demo.mp4",
    }


def test_robots_longest_rule_wins_and_a_rule_for_the_crawler_beats_the_general_one():
    txt = "User-agent: *\nDisallow: /private/\nAllow: /private/ok/\nUser-agent: MediaHOTBot\nDisallow: /blocked$\n"
    assert robots_allows(txt, "/public") and not robots_allows(txt, "/private/x") and robots_allows(txt, "/private/ok/y")
    assert not robots_allows(txt, "/blocked") and robots_allows(txt, "/blocked/more") is True
    assert robots_allows("", "/anything")


def l2_server(pages: dict[str, str], files: dict[str, tuple[str, bytes]], lengths: dict[str, int] | None = None):
    requests: list[str] = []

    def get(url, headers=None):
        requests.append(url)
        body = pages.get(url)
        return Page(404, url, "text/plain", "") if body is None else Page(200, url, "text/plain" if url.endswith("robots.txt") else "text/html", body)

    def get_binary(url, head=False, max_bytes=0):
        requests.append(("HEAD " if head else "") + url)
        if url not in files:
            return Binary(404, url, "", None, b"")
        ctype, body = files[url]
        return Binary(200, url, ctype, (lengths or {}).get(url, len(body)), b"" if head else body)

    return get, get_binary, requests


@needs_ffmpeg
def test_a_pages_videos_gif_and_main_picture_are_downloaded_and_a_logo_is_not(conn, workspace, media, tag):
    page_url = f"https://l2.example/{tag}/post"
    t = topic_at(conn, page_url)
    html = '<video src="/a.mp4"></video><video src="/b.mp4"></video><img src="/anim.gif"><meta property="og:image" content="/hero.png"><a href="/logo.png">x</a>'
    html = html.replace('<meta property="og:image" content="/hero.png">', "") + '<head><meta property="og:image" content="/hero.png"></head>'
    files = {
        "https://l2.example/a.mp4": ("video/mp4", media["v1.mp4"]),
        "https://l2.example/b.mp4": ("video/mp4", media["v2.mp4"]),
        "https://l2.example/anim.gif": ("image/gif", media["anim.gif"]),
        "https://l2.example/hero.png": ("image/png", media["hero.png"]),
    }
    get, get_binary, _ = l2_server({page_url: html}, files)
    r = fetch_layer2(conn, t.topic_id, get=get, get_binary=get_binary, delay=0)
    assert sorted(d["kind"] for d in r.downloaded) == ["gif", "image", "video", "video"]
    assert {(row["origin"], row["fetch_layer"]) for row in assets_of(conn, t.topic_id)} == {("official", 2)}


@needs_ffmpeg
def test_a_picture_with_a_side_under_400_px_is_not_kept_and_a_big_file_is_a_gap(conn, workspace, media, tag):
    page_url = f"https://l2s.example/{tag}"
    t = topic_at(conn, page_url)
    html = '<head><meta property="og:image" content="/logo.png"></head><video src="/big.mp4"></video>'
    get, get_binary, _ = l2_server(
        {page_url: html},
        {"https://l2s.example/logo.png": ("image/png", media["logo.png"]), "https://l2s.example/big.mp4": ("video/mp4", media["v1.mp4"])},
        lengths={"https://l2s.example/big.mp4": 900 * 1024 * 1024},
    )
    r = fetch_layer2(conn, t.topic_id, get=get, get_binary=get_binary, delay=0)
    assert r.downloaded == []
    assert any("圖片太小" in s["reason"] and s["failed"] is False for s in r.skipped)
    assert conn.execute("SELECT count(*)::int AS n FROM topic_fetch_gaps WHERE topic_id = %s AND layer = 2", (t.topic_id,)).fetchone()["n"] == 1


def test_a_page_robots_forbids_is_not_fetched_and_requests_to_a_host_are_spaced(conn, workspace, tag):
    page_url = f"https://robots.example/{tag}/private/post"
    t = topic_at(conn, page_url)
    get, get_binary, requests = l2_server(
        {"https://robots.example/robots.txt": "User-agent: *\nDisallow: /", page_url: "<video src='/a.mp4'></video>"}, {}
    )
    r = fetch_layer2(conn, t.topic_id, get=get, get_binary=get_binary, delay=0)
    assert r.skipped and r.skipped[0]["failed"] is False and "robots" in r.skipped[0]["reason"]
    assert page_url not in requests

    paced = topic_at(conn, f"https://pace.example/{tag}/p")
    now = [0.0]
    sleeps: list[float] = []
    get2, gb2, _ = l2_server(
        {f"https://pace.example/{tag}/p": "<video src='/a.mp4'></video>"}, {"https://pace.example/a.mp4": ("video/mp4", b"x" * 10)}
    )
    fetch_layer2(conn, paced.topic_id, get=get2, get_binary=gb2, delay=1.0, sleep=lambda s: sleeps.append(s), clock=lambda: now[0])
    assert sleeps and all(abs(s - 1.0) < 1e-9 for s in sleeps)


# ── L3 ──────────────────────────────────────────────────────────────────────────────────


def test_explain_says_what_to_do_about_each_kind_of_complaint():
    assert explain("ERROR: Sign in to confirm you're not a bot", "yt-dlp").code == "LOGIN"
    assert explain("no supported JavaScript runtime", "yt-dlp").code == "JS_RUNTIME"
    assert explain("Unable to extract uploader id", "yt-dlp").code == "OUTDATED"
    assert "python -m pip install -U gallery-dl" in explain("please update", "gallery-dl").message
    assert explain("File is larger than max-filesize", "yt-dlp").code == "TOO_LARGE"
    assert explain("x\nERROR: something broke\n", "yt-dlp").message == "ERROR: something broke"
    assert "avc|h264" in VIDEO_FORMAT and "height<=1080" in VIDEO_FORMAT


def test_players_a_page_embeds_become_the_page_of_that_video():
    html = '<iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe><iframe data-src="https://player.vimeo.com/video/12345"></iframe><iframe src="https://ads.example/x"></iframe>'
    assert embedded_videos(html, "https://example.com/") == ["https://www.youtube.com/watch?v=abcdefghijk", "https://vimeo.com/12345"]


def fake_exec(video="ok", gallery=True, missing=()):
    calls: list[list[str]] = []

    def run(command, args):
        calls.append([command, *args])
        name = args[1] if args[:1] == ["-m"] else command
        if command in missing:
            return ExecResult("ENOENT", "", "not found")
        if args[-1:] == ["--version"]:
            return ExecResult(0, "1.0", "")
        if name in ("yt-dlp", "yt_dlp"):
            if video != "ok":
                return ExecResult(1, "", video)
            template = args[args.index("-o") + 1]
            f = Path(template.replace("%(id)s", "abc123").replace("%(ext)s", "mp4"))
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"video bytes")
            return ExecResult(0, f"{f}\n", "")
        if name in ("gallery-dl", "gallery_dl") and gallery:
            d = Path(args[args.index("-D") + 1])
            d.mkdir(parents=True, exist_ok=True)
            (d / "1.png").write_bytes(b"png bytes")
            return ExecResult(0, "", "")
        return ExecResult(1, "", f"unexpected {command}")

    return run, calls


def test_a_youtube_source_is_downloaded_as_third_party_and_a_failure_says_what_to_do(conn, workspace, tag):
    vid = re.sub(r"[^A-Za-z0-9]", "", tag).ljust(11, "x")[:11]
    t = topic_at(conn, f"https://www.youtube.com/watch?v={vid}")
    run, calls = fake_exec()
    r = fetch_layer3(conn, t.topic_id, exec_=run, get=no_page, cookies_file=None)
    assert [(d["kind"], d["method"]) for d in r.downloaded] == [("video", "yt-dlp")]
    args = next(c for c in calls if "-f" in c)
    assert args[args.index("-f") + 1] == VIDEO_FORMAT and "--no-playlist" in args and "--cookies" not in args
    assert [(a["origin"], a["fetch_layer"], a["fetch_method"]) for a in assets_of(conn, t.topic_id)] == [("third_party", 3, "yt-dlp")]

    t2 = topic_at(conn, f"https://www.youtube.com/watch?v={vid[::-1]}")
    bad, _ = fake_exec(video="ERROR: Sign in to confirm you're not a bot")
    r2 = fetch_layer3(conn, t2.topic_id, exec_=bad, get=no_page, cookies_file=None)
    assert r2.failed[0]["code"] == "LOGIN"
    missing, _ = fake_exec(missing=("yt-dlp", "python", "py"))
    r3 = fetch_layer3(conn, t2.topic_id, exec_=missing, get=no_page, cookies_file=None)
    assert r3.failed[0]["code"] == "MISSING_TOOL"
    stats = {s.layer: s for s in fetch_stats(conn, 30)}
    assert stats[3].attempts >= 1


# ── L4 ──────────────────────────────────────────────────────────────────────────────────


@needs_ffmpeg
def test_a_page_with_no_video_is_recorded_to_1080p_30fps_h264_and_a_black_one_is_a_gap(conn, workspace, tag):
    t = topic_at(conn, f"https://rec.example/{tag}/demo")

    def good(url, out, seconds):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=25", "-c:v", "libvpx", out], check=True
        )

    r = fetch_layer4(conn, t.topic_id, record=good, get=no_page, seconds=2)
    assert len(r.recorded) == 1
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(workspace / "topics" / t.topic_id / r.recorded[0]["path"])],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )["streams"][0]
    assert (info["codec_name"], info["width"], info["height"], info["r_frame_rate"], info["pix_fmt"]) == ("h264", 1920, 1080, "30/1", "yuv420p")
    assert [(a["origin"], a["fetch_layer"], a["fetch_method"]) for a in assets_of(conn, t.topic_id)] == [("third_party", 4, "playwright")]

    black_topic = topic_at(conn, f"https://black.example/{tag}/demo")

    def black(url, out, seconds):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:size=640x360:rate=25:duration=2", "-c:v", "libvpx", out], check=True
        )

    rb = fetch_layer4(conn, black_topic.topic_id, record=black, get=no_page, seconds=2)
    assert rb.recorded == [] and "黑" in rb.skipped[0]["reason"]
    assert (
        conn.execute("SELECT count(*)::int AS n FROM topic_fetch_gaps WHERE topic_id = %s AND layer = 4", (black_topic.topic_id,)).fetchone()["n"]
        == 1
    )

    def broken(url, out, seconds):
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    gone = topic_at(conn, f"https://gone.example/{tag}/demo")
    assert "頁面沒有載入" in fetch_layer4(conn, gone.topic_id, record=broken, get=no_page).skipped[0]["reason"]


def test_l4_does_not_run_for_a_source_with_a_video_or_a_topic_without_a_reel_and_respects_robots(conn, workspace, tag):
    carousel = topic_at(conn, f"https://nor.example/{tag}", formats=("carousel",))
    assert fetch_layer4(conn, carousel.topic_id, record=lambda *a: pytest.fail("no"), get=no_page).recorded == []
    reel = topic_at(conn, f"https://yes.example/{tag}/p")
    have = {r["source_id"] for r in conn.execute("SELECT source_id FROM topic_sources WHERE topic_id = %s", (reel.topic_id,)).fetchall()}
    r = fetch_layer4(conn, reel.topic_id, record=lambda *a: pytest.fail("no"), get=no_page, have_video=have)
    assert r.skipped[0]["reason"] == "已有影片，不錄影" and r.skipped[0]["failed"] is False
    blocked = topic_at(conn, f"https://blocked.example/{tag}/p")
    r = fetch_layer4(
        conn,
        blocked.topic_id,
        record=lambda *a: pytest.fail("no"),
        get=lambda url, headers=None: Page(200, url, "text/plain", "User-agent: *\nDisallow: /"),
    )
    assert r.skipped[0]["failed"] is False and "robots" in r.skipped[0]["reason"]


@needs_ffmpeg
def test_to_mp4_measures_how_much_of_a_recording_is_black(tmp_path):
    src = tmp_path / "half.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=320x180:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x180:rate=25",
            "-filter_complex",
            "[0][1]concat=n=2:v=1:a=0",
            "-c:v",
            "libvpx",
            str(src),
        ],
        check=True,
    )
    share = to_mp4(str(src), str(tmp_path / "out.mp4"))
    assert 0.4 < share < 0.6


# ── assets, preprocess, scenes ──────────────────────────────────────────────────────────


def test_which_gaps_a_format_has_and_which_file_types_count():
    video = Asset("a", "media/raw/a.mp4", "video", "self")
    image = Asset("b", "media/raw/b.png", "image", "self")
    assert [g.need for g in gaps_for(["reel"], [])] == ["visual"]
    assert gaps_for(["reel"], [video]) == [] and [g.need for g in gaps_for(["reel"], [image], voiceover=True)] == ["voiceover"]
    assert [g.need for g in gaps_for(["carousel"], [video])] == ["hero-image"] and gaps_for(["carousel"], [image]) == []
    assert type_of("x.MP4") == "video" and type_of("截圖 1.png") == "screenshot" and type_of("a.png") == "image" and type_of("a.exe") is None


@needs_ffmpeg
def test_inventory_lists_what_a_person_put_in_keeps_their_notes_and_a_rename_keeps_the_id(conn, workspace, media, tag):
    t = topic_at(conn, f"https://inv.example/{tag}")
    raw = workspace / "topics" / t.topic_id / "media" / "raw"
    raw.mkdir(parents=True)
    (raw / "clip.mp4").write_bytes(media["v1.mp4"])
    (raw / "pic.png").write_bytes(media["hero.png"])
    first = inventory(conn, t.topic_id, detect=lambda f, ms: [(0, ms)])
    by_path = {a.path: a for a in first.assets}
    video, pic = by_path["media/raw/clip.mp4"], by_path["media/raw/pic.png"]
    assert (video.type, video.origin, video.duration_ms, video.usable_ranges_ms) == ("video", "self", 2000, [[0, 2000]])
    assert pic.duration_ms is None and [g.need for g in first.gaps] == []
    doc = json.loads((workspace / "topics" / t.topic_id / "assets.json").read_text(encoding="utf-8"))
    doc["assets"][0]["notes"] = "我寫的註記"
    (workspace / "topics" / t.topic_id / "assets.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    (raw / "clip.mp4").rename(raw / "renamed.mp4")
    second = inventory(conn, t.topic_id, detect=lambda f, ms: pytest.fail("ranges were written already"))
    renamed = next(a for a in second.assets if a.path.endswith("renamed.mp4"))
    assert renamed.asset_id == video.asset_id and renamed.notes in ("我寫的註記", "")
    (raw / "pic.png").unlink()
    assert pic.asset_id in inventory(conn, t.topic_id).removed
    assert "素材" in (workspace / "topics" / t.topic_id / "media.md").read_text(encoding="utf-8")


@needs_ffmpeg
def test_a_24_fps_video_comes_out_30_fps_constant_and_the_original_is_untouched(conn, workspace, tag):
    t = topic_at(conn, f"https://pre.example/{tag}")
    raw = workspace / "topics" / t.topic_id / "media" / "raw"
    raw.mkdir(parents=True)
    src = raw / "odd.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=321x241:rate=24",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    before = src.read_bytes()
    r = preprocess(conn, t.topic_id, audio=False)
    assert len(r.made) == 1 and src.read_bytes() == before
    out = workspace / "topics" / t.topic_id / r.made[0]["path"]
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(out)], capture_output=True, text=True, check=True
        ).stdout
    )["streams"][0]
    assert (info["codec_name"], info["pix_fmt"], info["r_frame_rate"], info["avg_frame_rate"]) == ("h264", "yuv420p", "30/1", "30/1")
    assert info["width"] % 2 == 0 and info["height"] % 2 == 0
    again = preprocess(conn, t.topic_id, audio=False)
    assert again.made == [] and len(again.kept) == 1


@needs_ffmpeg
def test_a_recording_becomes_48khz_wav_with_the_silence_gone_and_near_minus_14_lufs(conn, workspace, tag):
    t = topic_at(conn, f"https://aud.example/{tag}")
    raw = workspace / "topics" / t.topic_id / "media" / "raw"
    raw.mkdir(parents=True)
    src = raw / "voice.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:d=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-filter_complex",
            "[0]atrim=duration=1[a];[2]atrim=duration=1[c];[a][1][c]concat=n=3:v=0:a=1",
            str(src),
        ],
        check=True,
    )
    r = preprocess(conn, t.topic_id, video=False)
    assert len(r.made) == 1 and r.failed == []
    out = workspace / "topics" / t.topic_id / r.made[0]["path"]
    assert abs(measure_lufs(str(out)) - (-14)) < 1.5
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(out)], capture_output=True, text=True, check=True
        ).stdout
    )
    assert info["streams"][0]["sample_rate"] == "48000" and float(info["format"]["duration"]) < 2.6


def test_from_cuts_to_ranges_a_flash_is_not_a_scene_and_the_ranges_cover_the_video():
    assert ranges_from_cuts([], 5000) == [(0, 5000)]
    assert ranges_from_cuts([2000, 2200, 4800], 5000) == [(0, 2000), (2000, 5000)]
    assert ranges_from_cuts([3000, 1000], 6000) == [(0, 1000), (1000, 3000), (3000, 6000)]


@needs_ffmpeg
def test_a_video_of_three_scenes_gives_three_ranges(tmp_path):
    src = tmp_path / "three.mp4"
    # Three scenes of one second each: red, blue, green.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-filter_complex",
            "color=c=red:s=320x240:r=25:d=1[a];color=c=blue:s=320x240:r=25:d=1[b];color=c=green:s=320x240:r=25:d=1[c];[a][b][c]concat=n=3:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    ranges = detect_scenes(str(src), 3000)
    expected = [(0, 1000), (1000, 2000), (2000, 3000)]
    assert len(ranges) == 3 and all(abs(s - es) <= 120 and abs(e - ee) <= 120 for (s, e), (es, ee) in zip(ranges, expected, strict=True))
