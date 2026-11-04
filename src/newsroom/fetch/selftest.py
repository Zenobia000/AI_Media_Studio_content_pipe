"""Self-test of the fetch layers (FET-FR-010): a fixed address for each layer, run one after another, each on its own, so a layer that no
longer works shows as failed and the others are still tried. The downloader's version is checked too. The addresses are in selftest.json
next to this file; change them when a page goes away."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from newsroom.core.http import Get, assert_public_url, default_get
from newsroom.fetch.attempts import Attempt, record_attempts
from newsroom.fetch.extract import extract_source, word_count
from newsroom.fetch.layers.l2_html import find_media
from newsroom.fetch.layers.l3_dl import Exec, default_exec, explain, tool

SELFTEST_FILE = Path(__file__).with_name("selftest.json")
STALE_DAYS = 45  # a yt-dlp older than this is called out even when the newest release cannot be looked up


@dataclass
class SelftestCheck:
    layer: int  # the fetch layer (0 is the source text)
    name: str
    status: str  # ok | warn | fail
    detail: str
    url: str | None = None


def load_targets(file: Path | str = SELFTEST_FILE) -> dict[str, list[str]]:
    t = json.loads(Path(file).read_text(encoding="utf-8"))
    return {"extract": t.get("extract", []), "l2": t.get("l2", []), "l3": t.get("l3", [])}


def default_reachable(url: str) -> bool:
    assert_public_url(url)
    res = httpx.get(url, headers={"range": "bytes=0-1023"}, timeout=20.0, follow_redirects=True)
    return res.status_code in (200, 206)


def _default_latest(get: Get) -> str | None:
    try:
        page = get("https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest", {"accept": "application/vnd.github+json"})
    except Exception:  # noqa: BLE001
        return None
    return json.loads(page.text).get("tag_name") if page.status == 200 else None


def compare_versions(a: str, b: str) -> int:
    """Compares yt-dlp versions ("2026.10.26", "2026.10.26.123456"): negative when `a` is older than `b`."""
    x, y = ([int(p) for p in v.lstrip("v").split(".")] for v in (a, b))
    for i in range(max(len(x), len(y))):
        d = (x[i] if i < len(x) else 0) - (y[i] if i < len(y) else 0)
        if d:
            return d
    return 0


def _version_date(v: str) -> datetime | None:
    m = re.match(r"^v?(\d{4})\.(\d{2})\.(\d{2})", v)
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc) if m else None


def run_selftest(
    conn,
    targets: dict[str, list[str]],
    get: Get | None = None,
    exec_: Exec | None = None,
    reachable: Callable[[str], bool] | None = None,
    latest_ytdlp: Callable[[], str | None] | None = None,
    now: datetime | None = None,
) -> list[SelftestCheck]:
    get = get or default_get
    exec_ = exec_ or default_exec
    reachable = reachable or default_reachable
    now = now or datetime.now(timezone.utc)
    checks: list[SelftestCheck] = []

    def check(layer: int, name: str, url: str | None, fn: Callable[[], str]) -> None:
        try:
            checks.append(SelftestCheck(layer, name, "ok", fn(), url))
        except Exception as e:  # noqa: BLE001
            checks.append(SelftestCheck(layer, name, "fail", str(e)[:200], url))

    for url in targets["extract"]:

        def extract(u=url) -> str:
            x = extract_source(u, get)
            return f"{x.method}，{word_count(x.markdown)} 字"

        check(0, "原文擷取", url, extract)
    for url in targets["l2"]:

        def l2(u=url) -> str:
            page = get(u)
            if page.status != 200:
                raise RuntimeError(f"頁面回 HTTP {page.status}")
            media = [m for m in find_media(page.text, page.url) if m.kind != "image"]
            if not media:
                raise RuntimeError("頁面上找不到影片或 GIF（頁面可能改版）")
            if not reachable(media[0].url):
                raise RuntimeError(f"找到 {len(media)} 個媒體，但第一個讀不到：{media[0].url[:80]}")
            return f"找到 {len(media)} 個媒體，可讀取"

        check(2, "L2 網頁檔案", url, l2)
    ytdlp = tool(exec_, "yt-dlp", "yt_dlp")
    for url in targets["l3"]:

        def l3(u=url) -> str:
            if not ytdlp:
                raise RuntimeError("找不到 yt-dlp。安裝：`python -m pip install yt-dlp`")
            r = exec_(ytdlp[0], [*ytdlp[1:], "--simulate", "--no-warnings", "--no-playlist", "--print", "%(title)s", u])
            if r.code != 0:
                raise RuntimeError(explain(f"{r.err}\n{r.out}", "yt-dlp").message)
            return f"讀得到影片：{r.out.strip().split(chr(10))[0][:60]}"

        check(3, "L3 下載器（yt-dlp）", url, l3)

    # The downloaders break when sites change; the fix is nearly always a newer version.
    if ytdlp:
        installed = exec_(ytdlp[0], [*ytdlp[1:], "--version"]).out.strip()
        latest = (latest_ytdlp or (lambda: _default_latest(get)))()
        update = "更新：`python -m pip install -U yt-dlp`"
        born = _version_date(installed)
        age = (now - born).days if born else None
        if latest and compare_versions(installed, latest) < 0:
            checks.append(SelftestCheck(3, "yt-dlp 版本", "warn", f"已安裝 {installed}，最新是 {latest}。{update}"))
        elif not latest and age is not None and age > STALE_DAYS:
            checks.append(SelftestCheck(3, "yt-dlp 版本", "warn", f"已安裝 {installed}（{age} 天前的版本），查不到最新版。{update}"))
        else:
            checks.append(SelftestCheck(3, "yt-dlp 版本", "ok", f"{installed}（已是最新）" if latest else f"{installed}（查不到最新版，但還不算舊）"))
    record_attempts(
        conn, [Attempt(c.layer, c.url, c.status != "fail", c.detail if c.status == "fail" else None) for c in checks if c.url], "selftest", now
    )
    return checks
