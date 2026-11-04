# 素材收集部分改寫成 Python

| 項目 | 內容 |
|---|---|
| 日期 | 2026-11-04 |
| 範圍 | Albert 負責的 `AIHOT/modules/inbox`（收集、補料、熱度、通知）及其工具腳本。**不含** AIHOT 的網站、API、worker、RSS／GitHub／HN／HF 收集器、事件聚類、熱門排行（這些仍是 TypeScript） |
| 做法 | 新套件放在 repo 根目錄 `src/newsroom/`（結構照 [architecture.md](../architecture/architecture.md) §4.1），直接讀寫 AIHOT 的 PostgreSQL（同一個資料庫、同一張 `schema_migrations`） |
| 結果 | `AIHOT/modules/inbox` 與 14 支 `AIHOT/scripts/*.ts` 已刪除；工具變成 `newsroom <工具>`，排程變成 `newsroom run-scheduler` |
| 測試 | `python -m pytest tests`：`tests/unit`（不需要資料庫）＋`tests/integration`（需要 `DATABASE_URL` 指向 `*_test` 或 `*_ci` 資料庫，會自己套用 AIHOT 的 migration 和我們的）。共 114 個；CI 的 `python` 工作會跑 |

## 對照

| 原本（`AIHOT/modules/inbox/`） | 現在（`src/newsroom/`） |
|---|---|
| `frontmatter.ts`、`workspace.ts`、`init.ts`、`doctor.ts` | `core/frontmatter.py`、`core/workspace.py`、`core/init.py`、`core/doctor.py` |
| `format.ts`、`cross-day.ts`、`server.ts`（`writeInbox`） | `collect/inbox.py`、`collect/inbox_writer.py` |
| `heat.ts`、`x-velocity.ts` | `collect/score.py`、`collect/heat_inputs.py`、`collect/x_velocity.py`、`collect/heat.config.json` |
| `events.ts` | `collect/calendar.py` |
| `signals.ts`、`titles.ts`、`manual.ts`＋`shorturl.ts`、`newsletters.ts`、`watch.ts` | `collect/signals.py`、`collect/titles.py`、`collect/manual.py`、`collect/newsletters.py`、`collect/changedetect.py` |
| `topics.ts` | `collect/topics.py` |
| `alerts.ts`、`local-heat.ts` | `collect/alerts.py`、`collect/local_heat.py`、`core/notify.py` |
| `source-extract.ts`、`html-markdown.ts`、`fetch.ts` | `fetch/extract.py`、`fetch/html_markdown.py`、`fetch/topic.py` |
| `layer1.ts` ～ `layer4.ts` | `fetch/layers/l1_api.py`、`l2_html.py`、`l3_dl.py`、`l4_record.py` |
| `assets.ts`、`preprocess.ts`、`scenes.ts`、`ffmpeg.ts`、`attempts.ts`、`selftest.ts` | `fetch/assets.py`、`fetch/preprocess.py`、`fetch/scenes.py`、`fetch/ffmpeg.py`、`fetch/attempts.py`、`fetch/selftest.py` |
| `migrations/0106～0122_*.sql` | `core/migrations/`（內容相同；由 `newsroom migrate` 執行） |
| AIHOT 的 `lib/url.ts`（網址身分規則） | `core/urls.py`（用 Node 版的輸出做對照測試，29 個地址一致） |
| `scripts/*.ts`（add-source、open-topic、fetch-*、preprocess-topic、assets-topic、set-title-zh、doctor、init-workspace、merge-candidates、backtest-heat、source-stats） | `cli.py`（Typer，`newsroom <工具>`，輸出格式與結束碼不變） |
| AIHOT worker 載入的 inbox 模組排程 | `collect/schedule.py`（`newsroom run-scheduler`；`deploy/newsroom.yml` 把它加進 AIHOT 的 compose） |
| `changedetection/docker-compose.yml` | `deploy/changedetection.yml` |

## 與 TypeScript 版不同的地方

- **文章入庫是精簡版**（`collect/materials.py`）：新網址 → 新增文章、第一版內容、一筆 discovery；網址已存在 → 只加一筆 discovery。**不會**把文章排進 AIHOT 的模型分析與事件聚類佇列（pg-boss），也不修訂已存的內文。手動新增、電子報拆出的連結都走這條；手動新增的候選是從 `manual_candidates` 列出的，不需要分析與聚類。電子報拆出的連結因此**不會**被 AIHOT 當成新聞做精選與聚類（TypeScript 版會）。
- **熱門排行**直接讀 `hot_rankings` 最新一筆已發布的紀錄，沒有做 AIHOT 網站在顯示前對每個代表報導的重新檢查。
- **建題時找一個事件背後的文章**（`collect/topics.py`）：用 `story_signals`（官方來源優先、最多 20 篇），不是 AIHOT 的 `loadStoryDetail`（官方報導＋時間線）。
- **原文擷取**用 readability-lxml，比 AIHOT 的 `content/extract.ts` 少了串流內容、隱藏元素、圖形說明、各站規則的處理；Jina 備援直接呼叫 `r.jina.ai`。品質差異沒有用 SPK-FET-1 的樣本重新量過。
- **警報**只送到警報群的 webhook（`FEISHU_ALERT_WEBHOOK_URL`），沒有接楊舒媁的營運中臺。
- **候選快照**（`candidate_state.snapshot`）Python 版寫 snake_case 的欄位；讀的時候兩種寫法都認，缺的欄位給預設值，所以 TypeScript 版寫的舊資料仍可用。
- 全部用同步寫法（`psycopg`、`httpx`），沒有 asyncio。排程每個工作各開一條執行緒，並用 PostgreSQL advisory lock 避免同一個工作同時跑兩份。
- 排程的紀錄只寫到標準輸出，沒有寫進 AIHOT 的 `job_runs`（所以 AIHOT 後臺看不到這些排程的執行紀錄與心跳）。

## 沒有驗證的

- `deploy/Dockerfile.newsroom` 的映像檔有建成功、`newsroom --version`、ffmpeg、yt-dlp 都在；但 **`deploy/newsroom.yml` 與 AIHOT 的 compose 疊加後整個起來、排程真的跑一輪沒有試過**。
- L4 的 `browser_record`（Playwright 錄影）沒有執行過；測試用假的錄影器。
- `socialdata_lookup`（L1 查 X 貼文）、`threads_search`（在地熱度）、`send_alert`（飛書）沒有用真的金鑰跑過。
- 這次沒有拿真實資料庫（Docker 裡正在跑的那個）試過 `newsroom write-inbox`；測試用的是全新的 `*_ci` 資料庫。
- AIHOT 原有的 TypeScript 測試在刪掉 inbox 模組後沒有完整重跑，只跑了 `npm run typecheck`。
