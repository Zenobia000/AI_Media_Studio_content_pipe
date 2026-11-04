"""doctor (CORE-FR-002): what this machine has and lacks for the pipeline, each item ok, warn or fail with how to fix it.
fail: something the pipeline cannot work without. warn: something a part of it needs, or that cannot be checked here."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

MIN_PYTHON = (3, 11)
SERVICES = [
    ("Hacker News", "https://hn.algolia.com/api/v1/search?query=ai&hitsPerPage=1"),
    ("GitHub", "https://api.github.com/zen"),
    ("Hugging Face", "https://huggingface.co/api/daily_papers"),
]
FFMPEG_FIX = "安裝 ffmpeg 並確認 ffmpeg 與 ffprobe 都在 PATH：Windows `winget install Gyan.FFmpeg`、macOS `brew install ffmpeg`、Linux `sudo apt install ffmpeg`"


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    fix: str | None = None


@dataclass
class Deps:
    exec: Callable[[str, list[str]], tuple[bool, str]]  # runs a program; ok is False when it is missing or exits non-zero
    env: dict[str, str]
    reach: Callable[[str], bool]  # whether an address answers at all
    database: Callable[[], str | None]  # an error message when the database cannot be used, else None
    python_version: tuple[int, int, int]
    workspace: str | None  # the workspace folder found, or None


def default_exec(command: str, args: list[str]) -> tuple[bool, str]:
    if not shutil.which(command):
        return False, ""
    try:
        res = subprocess.run([command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return res.returncode == 0, res.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def default_reach(url: str) -> bool:
    try:
        httpx.get(url, timeout=10.0, follow_redirects=True)
        return True
    except httpx.HTTPError:
        return False


def default_deps(env: dict[str, str] | None = None) -> Deps:
    from newsroom.core import db  # noqa: PLC0415
    from newsroom.core.workspace import find_workspace  # noqa: PLC0415

    def database() -> str | None:
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1 FROM schema_migrations LIMIT 1")
            return None
        except Exception as e:  # noqa: BLE001
            return str(e).splitlines()[0][:160]

    ws = find_workspace(Path.cwd())
    v = tuple(int(x) for x in platform.python_version_tuple()[:3])
    return Deps(default_exec, dict(os.environ if env is None else env), default_reach, database, v, str(ws) if ws and ws.exists() else None)  # type: ignore[arg-type]


def run_checks(d: Deps) -> list[Check]:
    checks: list[Check] = []
    add = checks.append
    ver = ".".join(str(x) for x in d.python_version)
    add(
        Check("Python", "ok", ver)
        if d.python_version[:2] >= MIN_PYTHON
        else Check(
            "Python",
            "fail",
            f"{ver}（需要 {'.'.join(map(str, MIN_PYTHON))} 以上）",
            "安裝 Python 3.11 以上：Windows `winget install Python.Python.3.12`",
        )
    )
    for tool in ("ffmpeg", "ffprobe"):
        ok, out = d.exec(tool, ["-version"])
        add(Check(tool, "ok", out.split("\n")[0].strip()) if ok else Check(tool, "fail", "找不到", FFMPEG_FIX))
    ok, out = d.exec("docker", ["info", "--format", "{{.ServerVersion}}"])
    add(
        Check("Docker", "ok", f"服務運作中（{out.strip()}）")
        if ok
        else Check("Docker", "warn", "沒有執行中的 Docker 服務", "啟動 Docker Desktop；用 Docker 跑 PostgreSQL 或網頁變動監測時需要")
    )
    ok, out = d.exec("nvidia-smi", ["-L"])
    add(
        Check("GPU", "ok", out.split("\n")[0].strip())
        if ok
        else Check("GPU", "warn", "找不到 NVIDIA GPU", "語音辨識與影片渲染較快需要 GPU；只做文字與輪播可以忽略")
    )
    ok, out = d.exec("claude", ["--version"])
    add(
        Check("Claude Code", "warn", f"{out.strip()}（已安裝；登入狀態無法自動檢查）", "在終端機執行 `claude` 確認已登入")
        if ok
        else Check("Claude Code", "warn", "找不到 claude 指令", "安裝 Claude Code：https://docs.claude.com/en/docs/claude-code，再執行 `claude` 登入")
    )
    ok, out = d.exec("yt-dlp", ["--version"])
    add(Check("yt-dlp", "ok", out.strip()) if ok else Check("yt-dlp", "warn", "找不到", "補料 L3 需要：`python -m pip install yt-dlp`"))

    if not d.env.get("DATABASE_URL"):
        add(
            Check(
                "資料庫",
                "fail",
                "沒有設定 DATABASE_URL",
                "設定 DATABASE_URL，例如 postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev，並執行 `newsroom migrate`",
            )
        )
    else:
        error = d.database()
        add(
            Check("資料庫", "ok", "連得上，且已有資料表")
            if error is None
            else Check("資料庫", "fail", error, "確認 PostgreSQL 已啟動、資料庫存在，並執行 `newsroom migrate`")
        )

    wants = lambda name: d.env.get(name) == "true"  # noqa: E731
    if (wants("MODEL_CALLS_ENABLED") or wants("COLLECT_ENABLED")) and not d.env.get("LLM_API_KEY"):
        add(Check("模型金鑰", "fail", "已啟用模型呼叫或收集，但沒有 LLM_API_KEY", "在 .env 設定 LLM_API_KEY"))
    elif d.env.get("LLM_API_KEY"):
        add(Check("模型金鑰", "ok", "已設定"))
    else:
        add(Check("模型金鑰", "warn", "沒有設定（模型呼叫目前也沒有啟用）", "要用模型精選與翻譯時，在 .env 設定 LLM_API_KEY"))
    for name, use in (("GITHUB_TOKEN", "GitHub 擷取較不會被限速"), ("JINA_API_KEY", "原文擷取失敗時的備援")):
        add(Check(name, "ok", "已設定") if d.env.get(name) else Check(name, "warn", "沒有設定（選用）", f"設定後可用於：{use}"))
    add(
        Check("THREADS_ACCESS_TOKEN", "ok", "已設定（權限有沒有通過審核要實際搜尋才知道）")
        if d.env.get("THREADS_ACCESS_TOKEN")
        else Check(
            "THREADS_ACCESS_TOKEN",
            "warn",
            "沒有設定（選用）：S／A 級候選的「在地」欄會顯示「未檢查」",
            "要檢查中文圈有沒有人報導，需要 Meta 核准 Threads 關鍵字搜尋權限（threads_keyword_search），核准後把權杖設為 THREADS_ACCESS_TOKEN",
        )
    )
    for name, url in SERVICES:
        add(
            Check(f"連線：{name}", "ok", "連得上")
            if d.reach(url)
            else Check(f"連線：{name}", "warn", "連不上", "確認網路；對應的收集器會暫時失敗，但不影響其他來源")
        )
    add(
        Check("工作區", "ok", d.workspace)
        if d.workspace
        else Check("工作區", "warn", "還沒有工作區", "執行 `newsroom init <路徑>` 建立，再設定 NEWSROOM_HOME")
    )
    return checks


def exit_code_of(checks: list[Check]) -> int:
    """Exit code: 1 when anything failed."""
    return 1 if any(c.status == "fail" for c in checks) else 0
