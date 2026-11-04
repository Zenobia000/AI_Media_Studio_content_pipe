"""ffmpeg and ffprobe, run the way every tool of the pipeline runs them."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class ToolMissing(Exception):
    """ffmpeg or ffprobe is not installed (E_INPUT: the person has to install it)."""

    code = "E_INPUT"


def ffmpeg(args: list[str]) -> str:
    """Runs ffmpeg and returns what it wrote to stderr (its report goes there); a failure says why."""
    try:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        raise ToolMissing("找不到 ffmpeg；請安裝 ffmpeg 並確認它在 PATH 裡") from e
    if res.returncode != 0:
        last = " ".join([ln for ln in res.stderr.strip().split("\n") if ln][-2:])
        raise RuntimeError(f"ffmpeg 失敗：{last[:200]}")
    return res.stderr


def ffprobe_json(args: list[str]) -> dict:
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as e:
        raise ToolMissing("找不到 ffprobe；請安裝 ffmpeg 並確認它在 PATH 裡") from e
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe 讀不了這個檔案（可能已損壞）：{Path(args[-1]).name}")
    return json.loads(res.stdout)
