# Python 後端怎麼跑、怎麼跟網站和別人串接

2026-11-05 做過一次「從空資料庫開始」的演練，下面每一步都實際跑過。

## 1. 需要什麼
- Python 3.12、PostgreSQL（沒有其他資料庫或佇列服務；工作佇列是自己的資料表）。
- 網站畫面（`AIHOT/apps/web`，React）不用改，照舊用 `API_BASE_URL` 連後端。
- 安裝：`pip install -e ".[dev]"`

## 2. 最少要設的環境變數（`AIHOT/.env.example` 有完整清單）
| 變數 | 說明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `SITE_URL` | 網站對外位址，例 `http://127.0.0.1:3000` |
| `API_BASE_URL` | 後端 API 位址，網站用它連後端，例 `http://127.0.0.1:3101` |
| `API_PORT` | 後端監聽的埠 |
| `SESSION_SECRET`、`IMG_PROXY_SIGN_SECRET` | 登入與圖片簽章用，至少 32 個字元 |
| `ADMIN_PASSWORD` | 後臺密碼 |
| `INGEST_TOKEN` | 外部程式推送文章用的令牌（沒設就不能推送） |
| `AIHOT_DATA_DIR` | 圖片快取、分享圖、備份、上傳檔放的資料夾 |
| `COLLECT_ENABLED=true` | 要自動收集信源才設；預設關 |
| `MODEL_CALLS_ENABLED=true` | 要呼叫模型才設；預設關。另需 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` |

選用的整合（沒設就整塊不動，不會報錯）：`FEISHU_*`（通知）、`SOCIALDATA_API_KEY`、`DAJIALA_KEY`、`JINA_API_KEY`、`EMBEDDING_API_KEY`、`DB_BACKUP_STORE_*`、`INDEXNOW_*`、`WEBHOOK_HUB_URL`/`WEBHOOK_HUB_KEY`。

## 3. 三個指令
```
newsroom migrate                                              # 一個指令建完全部資料表（引擎 86 個 migration）；重跑不會重複套用
python -m uvicorn --factory newsroom.api.app:build_app --port 3101   # API
newsroom worker                                               # 工作行程：收集、抓正文、分析、歸組、翻譯、報告、通知、營運排程
```
網站：`cd AIHOT/apps/web && API_BASE_URL=http://127.0.0.1:3101 npm run dev`（或 `npm run build && npm start`）。

## 3.1 本機一鍵啟動（Windows）
專案根目錄的 `start-dev.ps1` 會啟動資料庫容器、API、worker 和網站（各開一個視窗），`stop-dev.ps1` 全部停掉。`.\start-dev.ps1 -Collect` 同時打開自動收集信源。腳本裡的密碼與密鑰是本機測試用的。

## 4. 演練結果（空資料庫）
- `newsroom migrate`：套用 86 個 migration，第二次執行顯示「已是最新」。
- API 啟動後 `/api/health` 回 `{"ok":true,"db":"ok"}`，`/api/v1/items` 回空列表。
- 用 `INGEST_TOKEN` 推送一則文章成功（`{"ok":true,"created":1}`）。
- worker 啟動後自己跑起排程（熱度排行、補排、翻譯）與歸組佇列，兩個程序都寫入心跳。
- 後臺：`POST /api/auth/password`（表單欄位 `password`）登入後，`/api/admin/runs`、`content`、`sources`、`models`、`feedback` 都回 200。
- 網站（React，開發模式）連到 Python API：`/`、`/all`、`/daily`、`/hot`、`/topics`、`/about`、`/agent`、`/admin/login` 都回 200。

## 5. 跟別人串接時要知道的
- **API 合約沒變**：網址、參數、回應格式都和 Node 版一致（用逐項比對測試保證）；`apps/web` 與其他呼叫者不用改。
- **工作佇列和 Node 的 pg-boss 不互通**：切換時 Node 佇列裡還沒處理的工作不會帶過去；兩個引擎不要同時對同一個資料庫跑 worker。
- **MCP / Agent / RSS / sitemap / 圖片代理** 都在同一個 API 行程裡。
- **沒有金鑰時**：收集、模型、通知各自關著，後端仍能啟動、提供網站與後臺。
- 已知差異與沒驗證的部分：見 `AIHOT/PROGRESS.md` 第 8 節與 `backend-python-remaining.md`。
