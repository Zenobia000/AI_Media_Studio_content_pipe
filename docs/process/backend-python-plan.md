# 整個後端改成 Python：計畫

日期 2026-11-04。**目標**：AIHOT 的後端（`packages/backend`、`apps/api`、`apps/worker`、`packages/contracts`、`scripts/`）全部改成 Python，只留網站前端（`apps/web`，React Router）。
**順序**：先有 MVP、跟周長曉與楊舒媁串接（見 [mvp-handoff.md](mvp-handoff.md)），再做整體改寫與優化。收集、補料、inbox 已經是 Python，不在這份範圍。

## 規模（行數，不含測試）

| 部分 | 行數 | 內容 |
|---|---|---|
| `packages/backend/src/publication` | 4,390 | 熱門榜、故事頁、搜尋、公開讀取 |
| `sources` | 2,669 | 信源收集器（RSS、JSON、網頁、X、公眾號） |
| `events` | 1,990 | 事件聚類、熱度排行 |
| `editorial` | 1,777 | 模型精選、評分、寫作 |
| `admin` | 1,570 | 後臺 |
| `content` | 1,526 | 文章入庫、原文擷取 |
| `operations` | 1,478 | 警報、心跳、備份 |
| `providers`、`media`、`reports`、`notify`、`jobs`、`lib` 等 | 約 6,600 | LLM 與 SocialData 等外部服務、圖片、日報、飛書、工作佇列 |
| `apps/api`（約 100 條路由） | 2,000 | HTTP API，網站前端用它 |
| `packages/contracts` | 1,300 | 型別與驗證（網站前端也在用） |
| `scripts/` 與其他 | 約 1,700 | |
| **合計** | **約 3 萬行**，另有 171 個測試檔約 2.2 萬行 | |

## 做法與驗證

- **網站前端不動**，它透過 HTTP 讀 API。所以 Python 版 API 的**回應必須和 TypeScript 版逐條一致**。驗證方式：同一個資料庫同時跑兩個版本，對每條路由送同樣的請求、比對 JSON。這是這次改寫最重要的測試。
- **一個模組一個模組搬，搬完一個就用比對測試確認、再刪對應的 TypeScript**；任何時候網站都有一個能跑的後端（可以兩個版本並存，由 API 前面的路由決定打哪一個）。
- 技術選擇（待確認）：FastAPI 取代 Fastify；工作佇列用 PostgreSQL（`FOR UPDATE SKIP LOCKED`）取代 pg-boss；`pydantic` 取代 zod；`psycopg` 已在用。

## 順序

| 階段 | 內容 | 為什麼先做 |
|---|---|---|
| 0 | 底層：時間、文字、ID、游標、設定、網址（`core/urls.py` 已完成）、HTTP 防護 | 其他部分都用到；有 Node 版輸出可對照 |
| 1 | 契約（`packages/contracts`）：API 回應的型別與驗證 | 比對測試要靠它；網站前端也用，要一起確認 |
| 2 | 唯讀公開 API：首頁、列表、文章、故事、搜尋、feeds、sitemap、OG 圖 | 最多路由、風險最低（不寫資料），比對測試最容易 |
| 3 | 收集：信源收集器、文章入庫（取代 `collect/materials.py` 的精簡版）、原文擷取 | 讓新資料進來；補回精簡版沒做的分析與聚類佇列 |
| 4 | 工作佇列與排程（取代 pg-boss、`apps/worker`） | 3 之後才有東西可排 |
| 5 | 事件聚類、熱度排行、模型精選與寫作（`events`、`editorial`、`providers`） | 最複雜，且花模型費用，要有錄製的回應才能測 |
| 6 | 後臺、營運（警報、心跳、備份）、日報、飛書通知 | 依賴前面全部 |
| 7 | 刪掉 TypeScript 後端；CI 只跑 Python＋網站前端 | |

## 風險

- 約 3 萬行加上測試，估計是好幾週的工作，不是一兩天。階段 5 的模型呼叫沒有錄製的回應就不能驗證，而且正式環境要花錢。
- API 回應的細節（欄位順序、日期格式、空值）網站前端可能依賴，所以才用逐條比對。
- 其他人的資料表與程式（楊舒媁的狀態機、發布）會接在這個後端上，順序要在串接時一起確認，避免改兩次。
