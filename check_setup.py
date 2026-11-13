"""在要跑 `python start_dev.py` 的電腦上，先檢查環境有沒有備齊（Windows）。

用法（在專案根目錄）：
    python check_setup.py

每一項印 [ok  ]、[warn] 或 [FAIL]，失敗的會寫要怎麼補；有任何 [FAIL] 時結束碼是 1。逐步安裝見 docs/process/setup-new-machine.md。
不改任何東西，也不需要資料庫或網站已經啟動。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OLLAMA = "http://127.0.0.1:11434"
MODELS = {"bge-m3": "向量模型（站內問答搜尋用）", "qwen3.5:9b-ctx32k": "聊天與文章分析用的模型"}
MIN_VRAM_MIB = 8000  # qwen3.5:9b at a 32k context takes about 6.7 GB; the embedding model and the desktop need the rest
MIN_FREE_GB = 30  # the models (about 8 GB), the Docker image and the database, the web app's packages, and room for downloaded materials


@dataclass
class Check:
    level: str  # ok | warn | FAIL
    name: str
    detail: str
    fix: str = ""


def version_at_least(text: str, minimum: tuple[int, int, int]) -> bool:
    """Whether the first x.y or x.y.z in `text` (a missing part is 0) is at least `minimum`."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return bool(m) and tuple(int(g or 0) for g in m.groups()) >= minimum  # type: ignore[union-attr]


def parse_models(tags: dict) -> set[str]:
    """The model names an Ollama server lists (/api/tags), with ":latest" taken off, so "bge-m3:latest" is "bge-m3"."""
    names = {m.get("name", "") for m in tags.get("models", []) if isinstance(m, dict)}
    return {n.removesuffix(":latest") for n in names if n}


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines of a .env file (comments and blank lines skipped, quotes taken off the value)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip().strip("\"'")
    return out


def run(*cmd: str, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def checks() -> list[Check]:
    out: list[Check] = []

    def add(ok: bool, name: str, good: str, bad: str, fix: str, level: str = "FAIL") -> None:
        out.append(Check("ok" if ok else level, name, good if ok else bad, "" if ok else fix))

    add(os.name == "nt", "作業系統", "Windows", "不是 Windows", "start_dev.py 只能在 Windows 跑；其他系統要改用 Docker（見 deploy/）", "warn")
    add(sys.version_info >= (3, 12), "Python", sys.version.split()[0], f"{sys.version.split()[0]} 太舊", "安裝 Python 3.12 以上")
    add(importlib.util.find_spec("newsroom") is not None and shutil.which("newsroom") is not None, "newsroom 套件", "已安裝", "還沒安裝", '在專案根目錄執行：python -m pip install -e ".[dev]"')
    code, text = run("node", "--version")
    add(code == 0 and version_at_least(text, (24, 22, 1)), "Node.js", text, text if code else f"{text} 太舊", "安裝 Node.js 24.22.1 以上（https://nodejs.org）")
    add((ROOT / "AIHOT" / "node_modules").is_dir(), "網站的套件", "已安裝", "還沒安裝", "執行：cd AIHOT && npm ci")
    code, text = run("docker", "info", "--format", "{{.ServerVersion}}")
    add(code == 0, "Docker", f"服務運作中（{text}）", "沒有在跑或沒安裝", "安裝並打開 Docker Desktop，等右下角圖示穩定後再試")
    for tool in ("ffmpeg", "ffprobe"):
        add(shutil.which(tool) is not None, tool, "找到了", "找不到", "執行：winget install Gyan.FFmpeg，再開一個新的終端機")
    for name, module in (("yt-dlp", "yt_dlp"), ("gallery-dl", "gallery_dl")):
        found = shutil.which(name) is not None or importlib.util.find_spec(module) is not None
        add(found, name, "找到了", "找不到（影片或照片的部分網站抓不到）", "執行：python -m pip install --user yt-dlp gallery-dl", "warn")

    code, text = run("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits")
    if code != 0:
        out.append(Check("FAIL", "NVIDIA 顯卡", "找不到 nvidia-smi", "沒有 NVIDIA 顯卡的話，本機模型會慢到不能用；要改接雲端模型（README 的環境變數）"))
    else:
        name, _, mem = text.splitlines()[0].rpartition(",")
        mib = int(mem.strip()) if mem.strip().isdigit() else 0
        add(mib >= MIN_VRAM_MIB, "NVIDIA 顯卡", f"{name.strip()}，{mib} MiB", f"{name.strip()} 只有 {mib} MiB，至少要 {MIN_VRAM_MIB}", "顯示記憶體不夠，要換小一點的模型或改接雲端模型")

    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as res:
            have = parse_models(json.load(res))
        out.append(Check("ok", "Ollama", "服務運作中"))
        for model, what in MODELS.items():
            fix = f"執行：ollama pull {model}" if model == "bge-m3" else "執行：ollama pull qwen3.5:9b，再 ollama create qwen3.5:9b-ctx32k -f deploy/qwen3.5-9b-ctx32k.Modelfile"
            add(model in have, f"模型 {model}", what, f"還沒有（{what}）", fix)
    except OSError:
        out.append(Check("FAIL", "Ollama", "連不上 127.0.0.1:11434", "安裝 Ollama（https://ollama.com）並打開，或在終端機執行：ollama serve"))

    env_file = ROOT / "AIHOT" / ".env"
    if not env_file.is_file():
        out.append(Check("FAIL", "AIHOT/.env", "不存在", "照 docs/process/setup-new-machine.md 第 5 步建立（這個檔案不在 git 裡，換電腦要自己建）"))
    else:
        env = parse_env(env_file.read_text(encoding="utf-8"))
        missing = [k for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL") if not env.get(k)]
        add(not missing, "AIHOT/.env 的模型設定", "有", f"缺 {'、'.join(missing)}", "補上 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL（見 docs/process/setup-new-machine.md）")
        if "qwen3" in env.get("LLM_MODEL", "").lower() and "reasoning_effort" not in env.get("LLM_EXTRA_JSON", ""):
            out.append(Check("FAIL", "關閉思考", "LLM_EXTRA_JSON 沒設", 'Qwen 3 以後的模型不關思考，文章分析會失敗：LLM_EXTRA_JSON={"reasoning_effort":"none"}'))

    for port, what in ((3000, "網站"), (3101, "後端 API")):
        add(not port_busy(port), f"埠 {port}（{what}）", "空著", "已經有程式在用", f"如果是上次的 start_dev，先執行 python stop_dev.py；否則找出佔用 {port} 的程式", "warn")
    free = shutil.disk_usage(ROOT).free / 1024**3
    add(free >= MIN_FREE_GB, "硬碟空間", f"剩 {free:.0f} GB", f"只剩 {free:.0f} GB（建議至少 {MIN_FREE_GB} GB）", "清出空間，模型和下載的素材會用掉不少", "warn")
    return out


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    results = checks()
    for c in results:
        print(f"[{c.level:4}] {c.name}：{c.detail}")
        if c.fix:
            print(f"       → {c.fix}")
    n = {k: sum(1 for c in results if c.level == k) for k in ("ok", "warn", "FAIL")}
    print(f"\nok {n['ok']}、warn {n['warn']}、fail {n['FAIL']}")
    print("備齊了，可以執行 python start_dev.py" if not n["FAIL"] else "還有 [FAIL] 要先處理，補完後再跑一次 python check_setup.py")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
