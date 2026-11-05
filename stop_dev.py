"""停掉 start_dev.py 開的後端 API、worker 和網站（資料庫容器不停，資料都在裡面）。

用法（在專案根目錄）：
    python stop_dev.py           只停 start_dev.py 記在 .data/dev-pids.json 的那幾個視窗（連同它們底下的程序）
    python stop_dev.py --scan    另外依命令列找出舊的或漏掉的開發程序（API、worker、網站開發伺服器、Node API）並停掉；
                                 先列出來問一次，加 --yes 就不問
要連資料庫一起停：docker stop parity-pg
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIDS = ROOT / ".data" / "dev-pids.json"
# 找舊程序用的命令列特徵（反斜線先換成斜線再比）：Python API、worker、網站開發伺服器、網站、Node 版 API
# 只看這幾種程式：Python（API、worker）、Node（網站、Node 版 API）、cmd 與 PowerShell（start_dev.py 開的視窗）；別的程式即使命令列有同樣的字也不碰
PROGRAMS = ("python.exe", "pythonw.exe", "node.exe", "cmd.exe", "powershell.exe", "pwsh.exe")
PATTERNS = (
    "uvicorn --factory newsroom.api.app",
    "newsroom worker",
    "react-router",
    "apps/web",
    "apps/api/src/main.ts",
)


def kill_tree(pid: int) -> bool:
    """taskkill /T 連同底下的程序一起停；已經不在了就回 False。"""
    done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    return done.returncode == 0


def recorded() -> int:
    if not PIDS.exists():
        print("沒有 start_dev.py 記下的程序（.data/dev-pids.json 不存在）。舊的程序可以用 --scan 找。")
        return 0
    stopped = 0
    for title, pid in json.loads(PIDS.read_text(encoding="utf-8")).items():
        ok = kill_tree(pid)
        stopped += ok
        print(f"{'已停止' if ok else '已經不在'}：{title}（{pid}）")
    PIDS.unlink()
    return stopped


def scan() -> list[tuple[int, str]]:
    """命令列含上面特徵的程序（不含這支腳本自己）。"""
    command = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId, Name, CommandLine | ConvertTo-Json -Compress"
    listing = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)
    rows = json.loads(listing.stdout.decode("utf-8", errors="replace") or "[]")
    rows = [rows] if isinstance(rows, dict) else rows
    found = []
    for row in rows:
        line = " ".join((row.get("CommandLine") or "").replace("\\", "/").split())
        if (
            (row.get("Name") or "").lower() in PROGRAMS
            and row["ProcessId"] != os.getpid()
            and "stop_dev.py" not in line
            and any(p in line for p in PATTERNS)
        ):
            found.append((row["ProcessId"], line[:110]))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="停掉本機開發環境")
    parser.add_argument("--scan", action="store_true", help="另外依命令列找出舊的或漏掉的開發程序")
    parser.add_argument("--yes", action="store_true", help="--scan 找到的不再問，直接停")
    args = parser.parse_args()
    if os.name != "nt":
        sys.exit("stop_dev.py 為 Windows 的開發環境寫的。")
    recorded()
    if args.scan:
        found = scan()
        if not found:
            print("沒有找到其他開發程序。")
        else:
            print(f"找到 {len(found)} 個開發程序：")
            for pid, line in found:
                print(f"  {pid}  {line}")
            if args.yes or input("全部停掉？ [y/N] ").strip().lower() == "y":
                for pid, _ in found:
                    kill_tree(pid)
                print("已停止。")
            else:
                print("沒有停。")
    print("已完成。要連資料庫一起停：docker stop parity-pg")


if __name__ == "__main__":
    main()
