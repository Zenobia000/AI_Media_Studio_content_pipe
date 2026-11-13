"""一次啟動本機開發環境（Windows）：資料庫（Docker 裡的 parity-pg）、Python 後端 API、worker、網站。

用法（在專案根目錄）：
    python start_dev.py                 啟動：自動收集信源（要連外網），並呼叫模型（分析、評分、翻譯、歸組、報告），設定與金鑰從 AIHOT/.env 讀；
                                        模型是雲端的會產生費用，是本機 Ollama 的不會。AIHOT/.env 沒有金鑰時不呼叫模型，照常啟動
    python start_dev.py --no-collect    不收集信源
    python start_dev.py --no-models     不呼叫模型
    python start_dev.py --models        一定要呼叫模型：沒有金鑰就直接結束
    python start_dev.py --db seeded     改用測試用的資料庫（裡面是造出來的假文章）；預設是自己的 dev 資料庫
停止：python stop_dev.py

預設用獨立的 dev 資料庫（第一次啟動時自動建立、跑 newsroom migrate、放進預設的信源），不碰對照測試用的 seeded。
收集到的文章要經過模型分析、評分、翻譯才會出現在網站的精選裡；不呼叫模型時文章只會停在「待分析」。
三個服務預設在同一個終端，輸出每行加 [API]、[worker]、[網站] 前綴，Ctrl+C 全停；加 --separate 則各開一個視窗。
程序編號記在 .data/dev-pids.json，stop_dev.py 只停這些。
這裡的密碼與密鑰都是本機測試用的，不要拿去正式環境。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIDS = ROOT / ".data" / "dev-pids.json"
DB_CONTAINER = "parity-pg"
DB_HOST = "postgres://postgres:ci@127.0.0.1:55432"
ENV = {
    "SITE_URL": "http://127.0.0.1:3000",
    "API_BASE_URL": "http://127.0.0.1:3101",
    "API_PORT": "3101",
    "SESSION_SECRET": "ci-session-secret-0123456789abcdef",
    "IMG_PROXY_SIGN_SECRET": "ci-img-secret-0123456789abcdef",
    "ADMIN_PASSWORD": "ci-admin-password-123",
    "INGEST_TOKEN": "ci-ingest-token-0123456789abcdef",
    "AIHOT_DATA_DIR": str(ROOT / ".data"),
    "PYTHONUTF8": "1",
    # 站內問答（聊天、語意搜尋）：worker 定時把新文章轉成向量（需要本機 Ollama 的 bge-m3）
    "RAG_INDEX_ENABLED": "true",
}
SERVICES = [
    ("MediaHOT API", ROOT, [sys.executable, "-m", "uvicorn", "--factory", "newsroom.api.app:build_app", "--port", "3101"]),
    ("MediaHOT worker", ROOT, ["newsroom", "worker"]),
    ("MediaHOT 網站", ROOT / "AIHOT" / "apps" / "web", ["npm", "run", "dev"]),
]
# AIHOT/.env 裡這些不由它決定（本腳本自己定，或由命令列旗標決定）
OWNED = {*ENV, "DATABASE_URL", "COLLECT_ENABLED", "MODEL_CALLS_ENABLED"}


def psql(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", DB_CONTAINER, "psql", "-U", "postgres", "-tA", *args], capture_output=True, text=True, encoding="utf-8")


def database(name: str) -> None:
    started = subprocess.run(["docker", "start", DB_CONTAINER], capture_output=True)
    if started.returncode != 0:
        sys.exit(f"資料庫容器 {DB_CONTAINER} 啟動失敗：請先打開 Docker Desktop。")
    for _ in range(30):
        if subprocess.run(["docker", "exec", DB_CONTAINER, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
            break
        time.sleep(1)
    else:
        sys.exit("資料庫 30 秒內沒有準備好。")
    if psql("-c", f"SELECT 1 FROM pg_database WHERE datname = '{name}'").stdout.strip() != "1":
        created = psql("-c", f'CREATE DATABASE "{name}"')
        if created.returncode != 0:
            sys.exit(f"建立資料庫 {name} 失敗：{created.stderr.strip()}")
        print(f"已建立資料庫 {name}")


def prepare(env: dict) -> None:
    """建表（已有的不重做），資料庫裡還沒有任何信源時放進預設的信源（AIHOT/industry/sources.json）。"""
    migrated = subprocess.run(["newsroom", "migrate"], env=env, capture_output=True, text=True, encoding="utf-8")
    if migrated.returncode != 0:
        sys.exit(f"newsroom migrate 失敗：\n{migrated.stdout}{migrated.stderr}")
    import psycopg  # noqa: PLC0415
    from psycopg.types.json import Jsonb  # noqa: PLC0415

    sources = json.loads((ROOT / "AIHOT" / "industry" / "sources.json").read_text(encoding="utf-8"))["sources"]
    with psycopg.connect(env["DATABASE_URL"]) as conn:
        if conn.execute("SELECT count(*) FROM sources").fetchone()[0] > 0:
            return
        for s in sources:
            tier = s.get("tier", "T2")
            conn.execute(
                """INSERT INTO sources (id, name, kind, config, tier, first_party, owner_entity_id, participation_mode, interval_minutes, tags,
                                        site_fulltext, syndicate_fulltext, enabled)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING""",
                (
                    s["id"], s["name"], s["kind"], Jsonb(s["config"]), tier, tier == "T1", s.get("owner_entity_id"), s.get("participation_mode", "editorial"),
                    s.get("interval_minutes", 60), s.get("tags", []), s.get("site_fulltext", False), s.get("syndicate_fulltext", False), s.get("enabled", True),
                ),
            )  # fmt: skip
    print(f"已放進 {len(sources)} 個預設信源")


def dotenv(path: Path) -> dict[str, str]:
    """AIHOT/.env 的 KEY=VALUE（略過註解與空行），不含本腳本自己決定的那幾個。"""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                if key not in OWNED and value.strip():
                    values[key] = value.strip().strip("\"'")
    return values


LOCAL_HOSTS = ("//127.0.0.1", "//localhost")
# 模型網址是本機時的預設值；.env 裡自己設了就照它。
LOCAL_MODEL_DEFAULTS = {
    # 本機的模型（Ollama 等）常常不照提示詞的規則填值；讓伺服器直接限制它能寫的值（engine/structured.py）
    "LLM_STRUCTURED_OUTPUT": "true",
    # 結構抽取的輸出上限是 1,200 個 token，本機模型平均就用掉約 1,170，稍長就被截斷；在每一步的上限上再加這麼多（只是上限，沒用到不花時間）
    "LLM_REASONING_TOKENS": "1500",
    # 本機模型一次只處理很少的請求，worker 同時送 6 個時後面的排隊太久會逾時（ReadTimeout，結果未知）；改成同時 2 個
    "ANALYZE_CONCURRENCY": "2",
    # 本機模型常常不管提示詞，直接用簡體字寫；網站是繁體，在提示詞最後加一句明確的指示（engine/output_language.py）
    "LLM_TRADITIONAL": "true",
}


def local_model_defaults(env: dict) -> None:
    """模型網址指向本機時，補上 LOCAL_MODEL_DEFAULTS 裡 .env 沒設的值。"""
    if "LLM_STRUCTURED_OUTPUT" not in env and any(h in env.get("LLM_BASE_URL", "") for h in LOCAL_HOSTS):
        env["LLM_STRUCTURED_OUTPUT"] = LOCAL_MODEL_DEFAULTS["LLM_STRUCTURED_OUTPUT"]
    if env.get("LLM_STRUCTURED_OUTPUT") == "true":
        for key, value in LOCAL_MODEL_DEFAULTS.items():
            env.setdefault(key, value)


def main() -> None:
    parser = argparse.ArgumentParser(description="一次啟動本機開發環境")
    parser.add_argument("--no-collect", action="store_true", help="不收集信源")
    parser.add_argument("--models", action="store_true", help="一定要打開模型呼叫：AIHOT/.env 沒有金鑰時直接結束（不加時，沒有金鑰就不開模型繼續啟動）")
    parser.add_argument("--no-models", action="store_true", help="不呼叫模型（文章只收集，停在「待分析」）")
    parser.add_argument("--separate", action="store_true", help="三個服務各開一個視窗（預設在同一個終端，輸出加前綴）")
    parser.add_argument("--db", default="dev", help="資料庫名稱（預設 dev；seeded 是測試用的假資料）")
    args = parser.parse_args()
    if os.name != "nt":
        sys.exit("start_dev.py 為 Windows 的開發環境寫的（每個服務開一個視窗）。")
    if args.models and args.no_models:
        sys.exit("--models 和 --no-models 不能一起用。")
    # 模型預設打開：設定從 AIHOT/.env 讀。沒有金鑰時，明講 --models 就結束，沒講就不開模型照常啟動。
    models = not args.no_models
    env = {**os.environ, **(dotenv(ROOT / "AIHOT" / ".env") if models else {}), **ENV}
    if models and not env.get("LLM_API_KEY"):
        if args.models:
            sys.exit("--models 需要模型金鑰：請在 AIHOT/.env 填 LLM_API_KEY（和 LLM_BASE_URL、LLM_MODEL），見 AIHOT/.env.example。")
        print("AIHOT/.env 沒有 LLM_API_KEY，這次不呼叫模型（要分析文章請先填好，見 AIHOT/.env.example）。")
        models = False
        env = {**os.environ, **ENV}
    args.models = models
    env["DATABASE_URL"] = f"{DB_HOST}/{args.db}"
    env["COLLECT_ENABLED"] = "false" if args.no_collect else "true"
    env["MODEL_CALLS_ENABLED"] = "true" if args.models else "false"
    if args.models:
        local_model_defaults(env)
    database(args.db)
    if args.db != "seeded":
        prepare(env)
    pids = {}
    procs = []
    for title, cwd, command in SERVICES:
        if args.separate:
            # 新視窗（CREATE_NEW_CONSOLE），標題寫明是哪一個服務；視窗關掉服務就停
            line = subprocess.list2cmdline(command)
            window = subprocess.Popen(["cmd", "/k", f"title {title} && {line}"], cwd=cwd, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
            pids[title] = window.pid
        else:
            # 同一個終端：輸出接過來，每行前面加服務名稱
            command = [shutil.which(command[0]) or command[0], *command[1:]]
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            pids[title] = proc.pid
            procs.append((title, proc))
    if not args.separate:
        pids["start_dev"] = os.getpid()
    PIDS.parent.mkdir(exist_ok=True)
    PIDS.write_text(json.dumps(pids, ensure_ascii=False), encoding="utf-8")
    print()
    print(f"已啟動三個服務（API、worker、網站），{'各一個視窗' if args.separate else '輸出都在這個終端'}，資料庫 {args.db}，網站約 20 秒後可用：")
    print("  網站  http://127.0.0.1:3000")
    print("  後臺  http://127.0.0.1:3000/admin/login   密碼 ci-admin-password-123")
    print("  API   http://127.0.0.1:3101/api/health")
    print("停止：python stop_dev.py")
    print(
        f"收集信源：{'關著' if args.no_collect else '打開（約每分鐘檢查一次哪些信源到期）'}；模型呼叫：{'打開（會產生費用）' if args.models else '關著'}"
    )
    if not args.models and not args.no_collect:
        print("沒有打開模型，收集到的文章會停在「待分析」，不會出現在網站的精選裡；要分析請在 AIHOT/.env 填好模型設定，並且不要加 --no-models")
    if procs:
        follow(procs)


def follow(procs: list) -> None:
    """把各服務的輸出加上名稱前綴印在這個終端；Ctrl+C 或任何一個服務結束時，全部停掉。"""
    print("按 Ctrl+C 停止全部服務。\n", flush=True)
    lock = threading.Lock()

    def pump(title: str, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            with lock:
                print(f"[{title}] {raw.decode('utf-8', errors='replace').rstrip()}", flush=True)

    for title, proc in procs:
        threading.Thread(target=pump, args=(title, proc), daemon=True).start()
    try:
        while all(proc.poll() is None for _, proc in procs):
            time.sleep(0.5)
        for title, proc in procs:
            if proc.poll() is not None:
                print(f"[{title}] 已結束（代碼 {proc.returncode}），停止其餘服務。")
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        for _, proc in procs:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        PIDS.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
