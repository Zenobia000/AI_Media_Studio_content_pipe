"""一次啟動本機開發環境（Windows）：資料庫（Docker 裡的 parity-pg）、Python 後端 API、worker、網站。

用法（在專案根目錄）：
    python start_dev.py              啟動
    python start_dev.py --collect    同時打開自動收集信源（要連外網）
停止：python stop_dev.py

三個服務各開一個視窗，視窗標題寫明是哪一個；它們的程序編號記在 .data/dev-pids.json，stop_dev.py 只停這些。
這裡的密碼與密鑰都是本機測試用的，不要拿去正式環境。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIDS = ROOT / ".data" / "dev-pids.json"
DB_CONTAINER = "parity-pg"
ENV = {
    "DATABASE_URL": "postgres://postgres:ci@127.0.0.1:55432/seeded",
    "SITE_URL": "http://127.0.0.1:3000",
    "API_BASE_URL": "http://127.0.0.1:3101",
    "API_PORT": "3101",
    "SESSION_SECRET": "ci-session-secret-0123456789abcdef",
    "IMG_PROXY_SIGN_SECRET": "ci-img-secret-0123456789abcdef",
    "ADMIN_PASSWORD": "ci-admin-password-123",
    "INGEST_TOKEN": "ci-ingest-token-0123456789abcdef",
    "AIHOT_DATA_DIR": str(ROOT / ".data"),
    "PYTHONUTF8": "1",
}
SERVICES = [
    ("MediaHOT API", ROOT, [sys.executable, "-m", "uvicorn", "--factory", "newsroom.api.app:build_app", "--port", "3101"]),
    ("MediaHOT worker", ROOT, ["newsroom", "worker"]),
    ("MediaHOT 網站", ROOT / "AIHOT" / "apps" / "web", ["npm", "run", "dev"]),
]


def database() -> None:
    started = subprocess.run(["docker", "start", DB_CONTAINER], capture_output=True)
    if started.returncode != 0:
        sys.exit(f"資料庫容器 {DB_CONTAINER} 啟動失敗：請先打開 Docker Desktop。")
    for _ in range(30):
        if subprocess.run(["docker", "exec", DB_CONTAINER, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
            return
        time.sleep(1)
    sys.exit("資料庫 30 秒內沒有準備好。")


def main() -> None:
    parser = argparse.ArgumentParser(description="一次啟動本機開發環境")
    parser.add_argument("--collect", action="store_true", help="同時打開自動收集信源（要連外網）")
    collect = parser.parse_args().collect
    if os.name != "nt":
        sys.exit("start_dev.py 為 Windows 的開發環境寫的（每個服務開一個視窗）。")
    env = {**os.environ, **ENV, "COLLECT_ENABLED": "true" if collect else "false"}
    database()
    pids = {}
    for title, cwd, command in SERVICES:
        # 新視窗（CREATE_NEW_CONSOLE），標題寫明是哪一個服務；視窗關掉服務就停
        line = subprocess.list2cmdline(command)
        window = subprocess.Popen(["cmd", "/k", f"title {title} && {line}"], cwd=cwd, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
        pids[title] = window.pid
    PIDS.parent.mkdir(exist_ok=True)
    PIDS.write_text(json.dumps(pids, ensure_ascii=False), encoding="utf-8")
    print()
    print("已啟動三個視窗（API、worker、網站），網站約 20 秒後可用：")
    print("  網站  http://127.0.0.1:3000")
    print("  後臺  http://127.0.0.1:3000/admin/login   密碼 ci-admin-password-123")
    print("  API   http://127.0.0.1:3101/api/health")
    print("停止：python stop_dev.py")
    if not collect:
        print("自動收集是關著的；要打開請用  python start_dev.py --collect")


if __name__ == "__main__":
    main()
