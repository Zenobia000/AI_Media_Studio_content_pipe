# 整個後端改成 Python：計畫

日期 2026-11-04。**目標**：AIHOT 的後端（`packages/backend`、`apps/api`、`apps/worker`、`packages/contracts`、`scripts/`）全部改成 Python，只留網站前端（`apps/web`，React Router）。
**順序**：MVP 已完成（見 [mvp-handoff.md](mvp-handoff.md)）。2026-11-04 起依這份計畫開始改寫，不等串接結果。收集、補料、inbox 已經是 Python，不在這份範圍。

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

## 進度（`src/newsroom/engine/`、`src/newsroom/api/`）

| 階段 | 狀態 | 內容 |
|---|---|---|
| 0 底層 | ✅（除模組註冊） | `time.py`、`lib/{text,ids,cursor,video_url,image_url}.py`（用 Node 版輸出對照）、`lib/netguard.py`（出站位址防護，40 個位址對照）、`lib/http_fetch.py`（出站請求：逐跳檢查、固定連線位址、大小與時間上限）、`lib/page_content.py`、`lib/cache.py`、`audit.py`、`config.py`、`pack.py`＋`pack/*.json`、`http_policy.py`。`modules.ts` 沒搬：網站已經沒有自己的模組（inbox 已搬出去），註冊表是空的 |
| 1 契約 | 併入階段 2 | Node 版的契約只是 TypeScript 型別，行為在各路由裡；Python 版在搬每條路由時定義回應結構，用對照測試確認 |
| 2 唯讀公開 API | ◐ 進行中 | ✅ FastAPI 骨架（`api/app.py`）、`api/respond.py`、`/api/v1/*` 的 404／405／預檢、**`/api/v1/items`**（`engine/publication/{scope,pool,v1}.py`、`engine/taxonomy.py`：模式、視窗、排序、分類、搜尋與容量限制、游標分頁、ETag／304、各種錯誤請求；52 條對照測試）。⬜ `hot-topics`、`stories/:id`（需要 `events/hot`、`events/related`、代表報導、故事文字、主題）、`dailies`／`weeklies`／`monthlies`（`publication/reports.ts` 661 行）、`selected/snapshot`／`changes`（需要 `publication/publish.ts` 的帳本）、`/api/site/*`、feeds、sitemap、OG 圖、媒體、agent、MCP、靜態檔 |
| 3～7 | ⬜ | |

### 對照用的資料
`AIHOT/scripts/seed-parity.ts` 用 Node 版自己的函式（`upsertMaterial`、`publishArticle`、`computeHotRanking`）在全新、已遷移的資料庫上造一份固定結構的資料：40 篇公開文章（9 篇精選、含沒有分數與低於瀏覽門檻的、超過 24 小時視窗的、一篇 X 貼文）、5 個事件（各有 3 篇報導）、1 份熱度榜、日報／週報／月報各 1 份。**不需要模型金鑰**。造資料時間是相對「現在」的，所以每次要重新造一份（`PARITY_DATABASE_URL` 指向它）。等 Python 的 `publish` 搬完，這支腳本也要改寫成 Python，Node 就不再需要。

### 對照工具（`tests/parity/`）
同一個資料庫、同一個請求，Node 版 API 與 Python 版各答一次，比對狀態碼、重要標頭與 JSON（請求編號除外）。需要：`PARITY_DATABASE_URL` 指向一份已用 `scripts/migrate.ts` 遷移過的真資料庫副本，與 Node。副本做法在 `tests/parity/conftest.py` 開頭。目前副本裡有 449 筆分析、448 筆公開文章，但**沒有事件（stories）、日報、週報**（要模型呼叫才會產生），而且 2026-11-02～04 的資料不在 24 小時視窗內，所以事件、報告相關的路由用這份副本只能比對到「空結果」。要比對有內容的結果，需要有金鑰跑一次模型流程，或由 TypeScript 的測試輔助函式造一份資料。

### 這輪發現的差異（TypeScript 版的行為，Python 版照搬並註明）
- `lib/text.ts` 的 `cjkRatio` 想寫相容漢字區（U+F900），實際打成 `豈`（U+8C48），所以範圍是 U+3400–U+FAFF，**韓文也算 CJK**。Python 版保持一樣，並在程式註解寫明。如果這是 bug，兩邊要一起改。

### 設定包
`site.ts` 裡的 `subjectAfter`、`withSubject` 是函式，已手動搬成 `pack.subject_after`、`pack.with_subject`，並用匯出的已求值字串驗證一致。`industry/` 的提示詞（`prompts/*.md`）、`sources.json`、`topics.json` 本來就是資料，階段 5 再接。
