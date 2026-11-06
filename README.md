# AI Media Studio 內容產線

把 AI 與科技新聞做成 Threads 貼文、IG 輪播和 Reel 的半自動產線。目前是內部 POC。

**主要流程**：收集素材 → 選題 → 寫稿 → 做輪播／字幕／Reel → 人工確認 → 發布 → 回收成效。創作者在 Claude Code 裡用口語下需求，agent 依 Markdown skill 編排確定性工具（參考 [gbrain](https://github.com/garrytan/gbrain) 的 thin harness, fat skills）。完整操作流程見 [User Flow 圖](docs/diagrams/user-flow.png)。

---

## 這包裡有很多說明檔，先看哪一份

| 我想知道… | 看這份 |
|---|---|
| 整個專案是什麼、誰做什麼、怎麼合作 | 這份（根目錄 README.md） |
| **我（Albert）做到哪、哪些完成、哪些沒完成** | [AIHOT/PROGRESS.md](AIHOT/PROGRESS.md) |
| **AIHOT 後端改成 Python 之後，每個步驟我怎麼做、修了什麼** | 這份往下的〈後端改成 Python 之後〉 |
| 收集、補料、inbox 的指令怎麼用、環境變數 | 這份往下的〈收集、補料、inbox 的指令（Python）〉與〈環境變數〉 |
| AIHOT 授權聲明 | [AIHOT/README.md](AIHOT/README.md)（只有授權，不用看） |
| 後端怎麼跑起來、和原本 TypeScript 版一致到什麼程度 | [docs/process/backend-python-handover.md](docs/process/backend-python-handover.md)（逐項細節在 [backend-python-remaining.md](docs/process/backend-python-remaining.md)） |
| 切換演練的步驟 | [docs/process/backend-python-run.md](docs/process/backend-python-run.md) |
| AIHOT 原作者的架構、部署、自訂說明 | `AIHOT/docs/`（原作者的檔案，部分說法仍指向原專案；`AIHOT/AGENTS.md`、`CONTRIBUTING.md`、`SECURITY.md` 也是原作者的） |
| 需求、規格、介面契約、技術選型 | 下面〈文件在哪裡〉 |

`docs/archive/` 是舊版文件，不要看。`AIHOT/industry/prompts/` 與 `src/newsroom/engine/pack/prompts/` 裡的 `.md` 是模型提示詞，不是說明文件，兩邊內容一致。

---

## 誰負責什麼

**Albert Chiang｜素材收集**
- 專案骨架、工具介面、工作區
- 收集器：手動網址、RSS、GitHub、Hacker News、電子報、X、網頁變動監測
- 去重、聚類、熱度分級、每日 inbox、建題
- 原文擷取、圖片與影片補料、素材盤點

**周長曉｜產出文章、剪輯影片**
- 寫稿：事實抽取、草稿生成、引用檢查、版本管理
- 輪播圖卡
- 字幕：對齊、斷詞、分行
- Reel 模板與渲染（Remotion）、動畫層

**楊舒媁｜自動排程與發布**
- 資料庫、執行紀錄（run_id、冪等、鎖）、工具層關卡、排程器與 headless 執行、通知、預算控管
- 確認流程、發布包、發布紀錄、自動發布
- 成效回收、週報
- Meta App 與平台權限

**三人共同**：模組之間的介面契約（`schemas/`）、每個 Sprint 結束時的整合驗收。

---

## 怎麼合作

1. **兩週一個 Sprint**，共 7 個 Sprint。每張票 1–3 人天，寫在 [docs/tickets/](docs/tickets/README.md)。
2. **契約先行**：S1 第一週三人一起定好模組之間的 JSON 格式，並做一份假資料（fixture）。下游拿假資料就能先開工，不用等上游做完。
3. **一張票一條分支**：從 `dev` 開 `feat/t-021-inbox` 這種分支，做完開 PR 回 `dev`。至少一人核准才能合併；改到 `schemas/` 要三人都核准。
4. **Sprint 最後一天**：在 `dev` 上做整合驗收，通過後把 `dev` 合進 `main`，打上 tag。`main` 永遠是驗收過的版本。
5. **每日站會 15 分鐘**：先看關鍵路徑上的票。每人回答兩件事：今天要交棒什麼、在等誰的什麼。
6. **改票**：改了票的 front-matter，就執行 `python docs/tools/build_dag.py`，更新總表和接力圖。

細節：[分支策略](docs/process/branching-strategy.md)、[分工與 Sprint 計畫](docs/process/team-dag-sprint.md)。

---

## 文件在哪裡

| 我想知道… | 看這份 |
|---|---|
| 要做什麼、為什麼做、怎樣算成功 | [產品需求（PRD）](docs/product/PRD.md) |
| 創作者實際怎麼操作，從選題到回收成效 | [User Flow 圖](docs/diagrams/user-flow.png)；互動版是 [user-flow.html](docs/diagrams/user-flow.html)，下載後用瀏覽器開 |
| 每個功能的詳細規格 | [功能規格（SRS）總覽](docs/spec/00_overview.md)，各模組見下表 |
| 模組之間交接的資料格式 | [介面契約](docs/spec/07_interfaces.md) |
| 系統怎麼搭、技術堆疊、架構圖 | [架構](docs/architecture/architecture.md)：[系統架構](docs/diagrams/system-architecture.png)、[軟體架構](docs/diagrams/software-architecture.png)、[資料流](docs/diagrams/data-flow.png)；互動版是同名的 `.html` |
| 為什麼改成 skills＋agent 編排 | [架構 ADR-09](docs/architecture/architecture.md#adr-09編排skillsai-agent取代固定流程協調器) |
| 每個環節用什麼工具、為什麼 | [技術選型](docs/architecture/tech-selection.md) |
| 誰做什麼、交棒順序、每個 Sprint 的目標 | [分工、DAG 與 Sprint 計畫](docs/process/team-dag-sprint.md) |
| 分支怎麼開、怎麼合併 | [分支策略](docs/process/branching-strategy.md) |
| 我這個 Sprint 要做哪幾張票 | [票的總表](docs/tickets/README.md) |
| 某件事要怎麼操作 | [操作手冊](docs/runbooks/)，例如 [Reddit API 申請](docs/runbooks/reddit-api-access.md) |
| 後端（Python）怎麼跑起來、和原本 TypeScript 後端一致到什麼程度 | [交付說明](docs/process/backend-python-handover.md)；本機一次全開用根目錄的 `python start_dev.py`（停止用 `python stop_dev.py`），Docker 用 `deploy/python-backend.yml`（見該檔開頭） |

**各模組規格**

| 模組 | 規格 | 負責人 |
|---|---|---|
| 核心（skills 編排、工具介面、資料庫、執行紀錄、排程） | [01_core](docs/spec/01_core.md) | Albert Chiang、周長曉、楊舒媁 |
| 收集 | [02_collect](docs/spec/02_collect.md) | Albert Chiang |
| 補料 | [03_fetch_assets](docs/spec/03_fetch_assets.md) | Albert Chiang |
| 寫稿 | [04_draft](docs/spec/04_draft.md) | 周長曉 |
| 輪播、字幕、Reel | [05_production](docs/spec/05_production.md) | 周長曉 |
| 確認、發布、成效 | [06_publish_feedback](docs/spec/06_publish_feedback.md) | 楊舒媁 |
| 狀態機 | [08_state_machines](docs/spec/08_state_machines.md) | 全體 |

**第一次來，照這個順序讀**：PRD → SRS 總覽與介面契約 → 自己負責的模組規格 → 分工與 Sprint 計畫 → 分支策略。

---

## 資料怎麼收集進來（抓取條件）

以下是 Python 後端（`newsroom worker`）的行為，條件寫在 `src/newsroom/engine/sources/`、`src/newsroom/engine/jobs/sources.py`，數字是程式裡的預設值。

**要打開才會抓。** 環境變數 `COLLECT_ENABLED=true` 才會排程收集（`python start_dev.py` 預設打開，`--no-collect` 關閉）。收集到的文章要經過模型分析才會出現在網站，沒有模型（`--models`）時只會停在「待分析」。

| 項目 | 條件 |
|---|---|
| 多久檢查一次 | worker 每分鐘看哪些信源到期，一次最多排 40 個，同時最多抓 8 個 |
| 每個信源多久抓一次 | 各信源自己的間隔（`interval_minutes`），預設清單設 1 到 6 小時（見下面的表）。**每天凌晨 4:20** 依最近 7 天的產量重算：一般信源 15 到 60 分鐘（產量越高越勤），X 與付費渲染的列表頁最慢 2 小時，只當熱度參考的討論類最慢 3 小時 |
| 抓失敗時 | 連續失敗 3 次後，後臺信源列表會提醒；詳情頁抓失敗先快速重試 2 次，之後等 1 小時、再倍增，最長 1 天，不會放棄 |
| 新加的信源（第一次） | 最多取 30 筆（可設 `initialBackfillLimit`），範圍是最近 12 個月內（`initialBackfillMonths`）；這批算「歷史」，不會進「今天」，也不會被推送 |
| 之後每次 | 只收「沒看過的網址」，而且要是信源加入時間再往前 48 小時之後發布的；已經收過的不重收 |
| 去重 | 依網址整理後的身分碼（去掉追蹤參數等）判斷，換寫法的同一個網址也只算一筆 |
| 發布時間 | 比抓取時間晚超過 1 小時的視為不可信；抓取時已經超過 48 小時前發布的，算「歷史」 |
| 過濾 | 各信源可設前綴、分類、雜訊詞與 `publishedAfter` 發布時間下限（在後臺 `/admin/sources` 設定） |
| 正文 | 每篇新文章都會排一個「抓正文」工作；擋爬蟲的網站要付費的 Jina 金鑰才補抓得到，抓不到的標為「未確認」 |

### 預設會抓哪些平台、多久抓一次

預設清單（`AIHOT/industry/sources.json`）共 **33 個信源，全部啟用**：RSS／Atom 30 個、JSON 介面 3 個。下表的「預設間隔」是剛建立時的設定，**每天凌晨 4:20 會依各信源最近 7 天的產量自動重算**，一般信源會落在 15 到 60 分鐘之間，所以跑過一天之後實際上通常比表上更頻繁。

| 信源 | 網站 | 類型 | 等級 | 預設間隔 |
|---|---|---|---|---|
| Ars Technica · AI | arstechnica.com | RSS／Atom | 媒體／二手 | 1 小時 |
| Hacker News (AI) | hn.algolia.com | JSON 介面 | 媒體／二手 | 1 小時 |
| TechCrunch · AI | techcrunch.com | RSS／Atom | 媒體／二手 | 1 小時 |
| The Decoder | the-decoder.com | RSS／Atom | 媒體／二手 | 1 小時 |
| The Verge · AI | theverge.com | RSS／Atom | 媒體／二手 | 1 小時 |
| AWS Machine Learning Blog | aws.amazon.com | RSS／Atom | 第一手 | 2 小時 |
| GitHub Blog · AI & ML | github.blog | RSS／Atom | 第一手 | 2 小時 |
| Google DeepMind | deepmind.google | RSS／Atom | 第一手 | 2 小時 |
| Google Research | research.google | RSS／Atom | 第一手 | 2 小時 |
| Hugging Face Blog | huggingface.co | RSS／Atom | 第一手 | 2 小時 |
| MIT Technology Review · AI | technologyreview.com | RSS／Atom | 媒體／二手 | 2 小時 |
| Microsoft Research | microsoft.com | RSS／Atom | 第一手 | 2 小時 |
| Mistral AI | mistral.ai | RSS／Atom | 第一手 | 2 小時 |
| NVIDIA Blog | blogs.nvidia.com | RSS／Atom | 第一手 | 2 小時 |
| OpenAI News | openai.com | RSS／Atom | 第一手 | 2 小時 |
| Simon Willison | simonwillison.net | RSS／Atom | 媒體／二手 | 2 小時 |
| iThome | ithome.com.tw | RSS／Atom | 媒體／二手 | 2 小時 |
| 科技新報 AI | technews.tw | RSS／Atom | 媒體／二手 | 2 小時 |
| Berkeley AI Research | bair.berkeley.edu | RSS／Atom | 第一手 | 3 小時 |
| Import AI | importai.substack.com | RSS／Atom | 媒體／二手 | 3 小時 |
| Latent Space | latent.space | RSS／Atom | 媒體／二手 | 3 小時 |
| GitHub Releases: anthropics/claude-code | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: comfyanonymous/ComfyUI | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: ggml-org/llama.cpp | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: huggingface/transformers | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: langchain-ai/langchain | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: ollama/ollama | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: openai/codex | github.com | RSS／Atom | 第一手 | 4 小時 |
| GitHub Releases: vllm-project/vllm | github.com | RSS／Atom | 第一手 | 4 小時 |
| AINews (smol.ai) | news.smol.ai | RSS／Atom | 媒體／二手 | 6 小時 |
| Hugging Face Daily Papers | huggingface.co | JSON 介面 | 媒體／二手 | 6 小時 |
| Hugging Face Trending Models | huggingface.co | JSON 介面 | 媒體／二手 | 6 小時 |
| TLDR AI | tldr.tech | RSS／Atom | 媒體／二手 | 6 小時 |

**程式支援、但預設清單沒有放的平台：** 網頁列表（沒有 RSS 的網站，用選擇器抓列表）、X（推特）搜尋、微信公眾號、外部程式推送（`POST /api/ingest/items`）。X 要 SocialData、公眾號要 Dajiala、擋爬蟲的網站要 Jina 的付費金鑰；在後臺 `/admin/sources` 新增信源並設好金鑰才會抓。**Reddit 沒有支援**（規則查過，建議停用，見進度文件）。

**等級：** 「第一手」是官方來源（如官方部落格、GitHub 版本發布），「媒體／二手」是媒體、電子報與個人部落格。

**想調整：** 單一信源的間隔、過濾與啟用在後臺 `/admin/sources`；預設清單改 `AIHOT/industry/sources.json`（只在建立資料庫時放進去）。更多細節見[交付說明](docs/process/backend-python-handover.md)。

---

## 後端改成 Python 之後

老師 2026-11-04 要求後端用 Python。我把 AIHOT 的後端（原版約 2.3 萬行 TypeScript：`packages/backend`、`apps/api`、`apps/worker`）改寫成 Python，放在 [src/newsroom/engine/](src/newsroom/engine/) 與 [src/newsroom/api/](src/newsroom/api/)，網站畫面 `AIHOT/apps/web` 不動。做完之後又修了一輪問題、補了功能。目前進度（哪些完成、哪些沒驗證）看 [AIHOT/PROGRESS.md](AIHOT/PROGRESS.md) 第 3 節。

### AIHOT 的流程，每一步我怎麼做

AIHOT 的流程是：信源 → 採集（判重、抓原文）→ 判斷與寫作（預篩、評分、標題摘要、結構化）→ 歸組（事件、熱度、綜述）→ 公開讀取層 → 日報週報月報與各種出口（見 [AIHOT/docs/architecture.md](AIHOT/docs/architecture.md)）。我是照這個順序逐步移植，每一步都拿 Node 版比對：讀取端用同一份資料庫打同樣的請求；會寫資料的功能把資料庫複製兩份、Node 與 Python 各處理一份再逐列比對；抓網站的功能架假網站兩邊各抓一次；模型步驟用本機假模型伺服器，比對送出的每個提示詞與結果。

| 步驟 | 原版（TypeScript） | 我怎麼做（Python） | 位置 |
|---|---|---|---|
| 0. 建資料表 | `scripts/migrate.ts`、86 個 migration | `newsroom migrate` 從空資料庫一個指令建完；佇列另用自己的資料表（migration 0123） | `core/migrations/`、`engine/jobs/` |
| 1. 信源收集 | `sources/`：RSS、網頁、JSON、X、公眾號、外部推送 | RSS／Atom／RDF、JSON、網頁列表逐個移植，含過濾、信源健康度、到期排程、每日間隔調整；X、公眾號、Jina 用本機假服務比對請求 | `engine/sources/`、`engine/socialdata.py`、`engine/dajiala.py`、`engine/jina.py` |
| 2. 入庫與判重 | `content/` | 網址整理後的身分碼去重、版本、時間線規則、原發信源歸屬；舊文（發布超過 48 小時）歸檔不進「今天」 | `engine/ingest.py`、`engine/content.py` |
| 3. 抓正文 | Mozilla Readability | 逐方法移植 Readability，加來源規則、發布時間判斷、詳情頁補抓；用 2,000 頁隨機網頁加 74 個真實頁面比對 | `engine/readability.py`、`engine/extract.py` |
| 4. 花錢的請求 | `providers/`：回執、預算、熔斷 | 每個付費請求先記回執、結果先存再用，重啟不重複付費；預算與備援模型都移植 | `engine/receipts.py`、`engine/budget.py`、`engine/circuit.py`、`engine/llm.py`、`engine/models.py` |
| 5. 判斷與寫作 | `editorial/`：預篩、兩次評分、結構抽取、摘要 | 31 份提示詞搬進 Python 並逐字比對；分析工作的狀態與重試照原版 | `engine/analyze.py`、`engine/prompts.py`、`engine/pack/prompts/` |
| 6. 全文翻譯 | 精選全文逐段翻譯 | 連結、圖片、行內程式碼先換佔位符再還原；答錯的段落用保守提示詞重問；沿用舊譯文的邏輯單獨比對 | `engine/translate.py` |
| 7. 事件歸組與綜述 | `events/` | 嵌入向量召回（沒有嵌入金鑰改用字元重疊）、四種關係判斷、第二家模型複核、事件綜述、人工更正；後臺已接上 | `engine/events/` |
| 8. 日報週報月報 | `reports/` | 日報用規則排；週報月報由日報彙整，模型只寫總述與導語，提到事件裡沒有的名稱或數字就不採用 | `engine/reports_*.py` |
| 9. 發布與熱度 | `publication/` | 公開投影、精選席位、帳本、熱度排行與每小時快照 | `engine/publication/` |
| 10. 對外出口 | 網頁 API、公開 API v1、RSS、MCP、sitemap、`llms.txt`、圖片代理、分享圖 | FastAPI 逐路由移植；圖片縮放與分享圖改用 Pillow（像素不會與原版相同） | `api/`、`engine/publication/`、`engine/media/` |
| 11. 後臺 | `admin/` | 登入、信源、模型、執行紀錄、內容、回饋、設定、稽核；另補上原版有函式沒路由的「發布日期校正」 | `engine/admin/` |
| 12. 通知與營運 | `notify/`、`operations/` | 飛書推送、警報、備份、監看、資料保留、復原、IndexNow；備份上傳用本機 S3 相容儲存實測 | `engine/notify_*.py`、`engine/operations*.py` |
| 13. 工作行程 | pg-boss、`apps/worker` | 自己寫的佇列（去重、延遲、重試退避、逾時、cron、漏跑補跑、優雅關機）；`newsroom worker` 註冊所有定時任務 | `engine/jobs/`、`engine/worker.py` |

### 改成 Python 之後又修了什麼

| 日期 | 問題 | 怎麼修 |
|---|---|---|
| 11-04 | 自動檢查（`check.yml`）放在 `AIHOT/.github/`，GitHub 只讀根目錄的 `.github/`，一直沒真的跑過 | 移到根目錄，指定在 `AIHOT/` 執行，加上 `dev` 分支（還沒 push，沒在 GitHub 上跑過） |
| 11-05 | 從空資料庫 `newsroom migrate` 會報錯 | 修好，現在一個指令建完 86 個 migration |
| 11-05 | 正文擷取與 Node 有一處不同：網頁用 `<template>` 宣告 shadow DOM 時，Node 的 Readability 不會刪裡面的 `<style>`，Python 版會刪，影響評分 | 改成不進入 `<template>`，加回歸測試（舊程式碼下失敗）；重比 74 個真實頁面全部一致 |
| 11-05 | `LLM_EXTRA_JSON`、`LLM_REASONING_TOKENS` 設了被默默忽略（Python 版漏掉） | 補上，錯誤訊息與 Node 一致，對照測試帶這兩個設定 |
| 11-05 | 本機模型不照提示詞填值：16 篇有 5 篇被擋（分類填「article」、分數填 0.85） | 加可選的結構化輸出（`LLM_STRUCTURED_OUTPUT`），讓伺服器限制可填的值；16 篇全部成功 |
| 11-05 | 相關文章有 32%（106/329）沒有分類 | 結構抽取也用結構化輸出，重新分析 102 篇後降到 4.3% |
| 11-05 | 結構抽取輸出被截斷（上限 1,200 token，本機模型平均用掉約 1,170） | `start_dev.py` 對本機模型多給 1,500 token（`LLM_REASONING_TOKENS`） |
| 11-05 | 本機模型一次只處理很少請求，worker 同時送 6 個，後面的逾時（ReadTimeout） | 新增 `ANALYZE_CONCURRENCY`，`start_dev.py` 對本機模型設成 2；0 篇失敗 |
| 11-05 | 本機模型常用簡體字寫標題（337 篇有 47%） | 提示詞最後加一句要求繁體（`LLM_TRADITIONAL`），簡體字從 59 個降到 1 個；後來又在顯示時用 OpenCC 轉（見下一節） |
| 11-05 | Docker 裡安裝後的套件找不到 `AIHOT/` 資料夾（遷移檔、靜態檔、字型）；映像沒有 `pg_dump`，備份失敗 | 用 `AIHOT_ROOT` 指定位置；映像加裝 PostgreSQL 客戶端 |
| 11-05 | 真實收集才出現：工作結果裡有日期時寫不進佇列，工作一直停在進行中 | 日期與 pg-boss 一樣存成 ISO 字串，加回歸測試 |
| 11-05 | 模型選擇的快取會在同一個行程裡留到下一個測試，測試互相影響 | 修好；新增測試要注意清除這類快取 |
| 11-05 | 警報測試資料裡幾種錯誤次數剛好相同，同次數排序沒有定義，測試偶爾失敗 | 改成次數都不同 |
| 11-05 | 網站地圖對照測試偶爾差 1 秒（快取還剩幾秒，兩個伺服器依序被請求、碰到秒的邊界） | 測試不再比對這個剩餘秒數 |
| 11-05 | 發布日期校正（預覽與套用）原版後臺沒有路由、Python 版原本沒有 | 移植，14 個案例與 Node 比對一致 |
| 11-05 | 後臺寫入操作沒有經過前端驗證過 | 用同樣的 22 筆寫入分別經接 Python 與接 Node 的前端送進各自資料庫複本，狀態碼、回應、資料列全部一致 |
| 11-06 | 本機啟動會跳出三個終端 | 改成同一個終端，輸出每行加服務名稱前綴，Ctrl+C 全停；`--separate` 可回到三個視窗 |
| 11-06 | 簡體網站的中文文章原樣顯示，簡體字出現在繁體網站上 | 顯示時用 OpenCC 轉換，見下一節 |
| 11-06 | 程式宣告支援 Python 3.11，實際用了 3.12 才有的語法（`pyproject.toml` 與程式不一致）；`ruff` 有 25 個問題，GitHub 上的 lint 會紅燈 | `requires-python`、ruff、mypy 都改成 3.12；修掉測試檔的小毛病，並把測試目錄對 3 條規則放寬（pytest 常見寫法） |
| 11-06 | `mypy src` 有 282 個型別錯誤（260 個在移植的後端），GitHub 上的型別檢查要不就沒有、要不就紅燈 | 全部修掉，現在 `mypy src` 全過、已加進 `check.yml`。主要做法：`core/db.py` 的 `connect()` 標明回傳 dict 列（一次清掉一批）、`_readability_p1.py` 宣告被子類別實作的方法、`dom.py` 宣告動態屬性、其餘用型別標註或 `cast`；沒有放寬規則、沒有用 `ignore_errors`。幾處 `re.match` 後加了 `assert`（只在不可能發生的路徑上，錯誤型別從 AttributeError 變 AssertionError） |
| 11-06 | 用真實候選試 `set-title-zh` 時發現 inbox 的標題有簡體字（「订单」），沒經過簡轉繁 | inbox 產生時也轉換（`collect/inbox.py`），加單元測試 |

---

## 本機開發與繁體中文顯示

**一個終端跑全部。** `python start_dev.py` 啟動資料庫、API、worker、網站，三個服務的輸出都在同一個終端，每行前面加 `[MediaHOT API]`、`[MediaHOT worker]`、`[MediaHOT 網站]`。按 Ctrl+C 全部停掉，任何一個服務自己結束時其餘也會停。要回到「三個服務各開一個視窗」，加 `--separate`。`python stop_dev.py` 仍然可以停（程序編號記在 `.data/dev-pids.json`）。

**簡體網站的文章顯示成繁體。** 中文文章原本是抓到什麼就顯示什麼，簡體網站的文章會以簡體字出現在繁體的網站上。現在可以在網頁顯示時，用 OpenCC（`s2twp` 設定）把正文轉成繁體並換成臺灣用語（例如「软件」→「軟體」、「内存」→「記憶體」）：

| 項目 | 做法 |
|---|---|
| 開關 | 環境變數 `TRADITIONAL_DISPLAY=true`；`python start_dev.py` 預設打開，其他環境預設關著 |
| 轉什麼 | 中文文章的正文（簡體網站來的）。標籤、屬性與網址不轉，只轉標籤之間的文字 |
| 不動什麼 | 資料庫裡存的原文、匯出（Markdown、完整 RSS）與搜尋，都維持抓到的樣子；轉換只發生在送給網頁的那一刻 |
| 標題、摘要、推薦理由、事件標題 | 同樣在送出時轉換（`to_item_summary`），所以**已經分析過的舊標題不用重跑**，下次顯示就是繁體；原文標題（`originalTitle`）不轉 |
| 每日 inbox 的標題 | 寫 inbox 時同樣轉換（`collect/inbox.py` 的 `render_block`），資料庫存的標題不動；用 `newsroom write-inbox` 手動產生時要自己設 `TRADITIONAL_DISPLAY=true`，worker 的定時產生會帶 `start_dev.py` 設的值 |
| 外文文章 | 本來就由模型翻成中文，提示詞另外用 `LLM_TRADITIONAL` 要求繁體（用本機模型時 `start_dev.py --models` 自動打開），顯示時再轉一次當保險 |
| 程式 | `src/newsroom/engine/traditional.py`，接在 `src/newsroom/engine/publication/detail.py` 組合正文的地方，測試在 `tests/unit/test_traditional.py` |
| 依賴 | `opencc`（已加進 `pyproject.toml`；已有的環境要 `pip install opencc` 或重新 `pip install -e ".[dev]"`） |

**限制：** 預設關著，是因為與 Node 版的對照測試會逐字比對正文，轉換後會不一致。資料庫裡的舊標題沒有被改寫，只是顯示時轉換；搜尋用的是原本的字，所以用繁體字搜尋簡體標題可能搜不到。OpenCC 偶爾會轉錯人名或品牌名，目前沒有例外詞表。

---

## 收集、補料、inbox：我們改了什麼

| 專案 | 內容 |
|---|---|
| 站名與設定 | 站名 MediaHOT、繁體中文（zh-TW）、爬蟲標識 `MediaHOTBot`（`site/site.ts`） |
| 信源 | 33 個：官方 AI blog、英文科技媒體、TLDR AI 與 AINews 電子報、Hacker News、Hugging Face 論文與熱門模型、8 個 GitHub release、iThome 與科技新報（`industry/sources.json`） |
| 精選標準 | 讀者設定成「經營 Threads／IG 的 AI 內容創作者」，加入開源工具與產業事件的加分、一般資安新聞的壓分（`industry/prompts/selection-score.md`） |
| 分類 | 六個分類的顯示名稱改成繁體（`industry/taxonomy.ts`） |
| **inbox 與建題** | 已改寫成 Python（`src/newsroom/`）：每日 inbox、手動新增候選、跨日規則、建題與合併、原文擷取、素材盤點。詳見下表 |

提示詞與標籤詞表的其餘部分仍是簡體，門檻（`industry/selection.ts`）還沒有用我們標註的樣本校準。

## 收集、補料、inbox 的指令（Python）

原本在 `modules/inbox` 的 TypeScript 已全部改寫成 Python，放在外層的 [src/newsroom/](src/newsroom/)，直接讀寫這個專案的 PostgreSQL（同一個資料庫、同一張 `schema_migrations`）。AIHOT 的網站、API、worker 照舊，只是不再載入 inbox 模組。改寫的對照與不同之處見 [../docs/process/python-port.md](docs/process/python-port.md)。

安裝與使用（在外層 repo 根目錄）：

```bash
python -m pip install -e ".[dev]"      # 需要 Python 3.12 以上；補料還需要 ffmpeg，選用 yt-dlp、gallery-dl
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # AIHOT 的 migration 已套用過的資料庫
newsroom migrate                        # 建立 inbox／補料用的資料表
newsroom init ./newsroom                # 建立工作區；再設定 NEWSROOM_HOME
newsroom doctor                         # 檢查環境
newsroom run-scheduler                  # 常駐排程；--once 把此刻到期的跑一輪
```

用 Docker：`docker compose -f AIHOT/docker-compose.yml -f deploy/newsroom.yml up -d --build`（`deploy/newsroom.yml` 加上 `newsroom` 排程服務，與網站共用資料庫和 `data` 資料卷）。

`newsroom <工具>`，加 `--json` 時輸出依整個專案的工具介面（IF-020），結束碼：`0` 成功、`1` 失敗、`2` 輸入有誤、`3` 被阻擋（要人補資料）。

| 工具 | 做什麼 | 對應票 |
|---|---|---|
| `add-source <url> [--note --program --formats --title]` | 手動新增候選；同網址（任何寫法）只留一筆並附加備註 | T-011 |
| `backtest-heat <labels.json>` | 回測熱度分級：人工標記的 S 級事件，有多少被判為 S 或 A（目標 ≥ 80%） | T-075 |
| `write-inbox`；（排程）`inbox-write` | 寫 `inbox/YYYY-MM-DD.md`：熱點事件、手動新增、前幾天帶過來的；排程每 30 分鐘一次 | T-021 |
| `open-topic <cand_id>... [--program --formats --angle]` | 依勾選建立 topic 與各格式的 content，不重建已有的 | T-022 |
| `merge-candidates <保留> <併入>` | 把兩個候選合成一題 | T-022 |
| `fetch-topic <topic_id> [--force]` | 擷取每個來源的原文，並補上報導連到的官方來源 | T-023 |
| `fetch-media <topic_id> [--layers 1,2,3,4]` | 下載媒體：L1 平臺 API（X 貼文取最高位元率 mp4、Threads 取 og:video）→ L2 網頁直接給的影片、GIF、主視覺圖（遵守 robots.txt、每頁最多 10 個、單檔 500 MB）→ L3 yt-dlp、gallery-dl → L4 用瀏覽器錄 20 秒（1080p 30fps，題目有 reel 且前面都沒拿到影片才錄；需要 Playwright 的 Chromium，頁面沒載入或畫面大半是黑的就記成缺口，不產生空影片）。前一層拿到影片的來源，後面的層不再處理 | T-038、T-039、T-073、T-106 |
| `preprocess-topic <topic_id> [--video-only 或 --audio-only] [--lufs -14]` | 素材前處理：影片與 GIF 轉 30fps CFR yuv420p H.264；旁白轉 48kHz WAV、去頭尾靜音、響度 −14 LUFS；輸出到 `media/proc/`，已處理的不重做 | T-057 |
| `fetch-selftest`、`fetch-stats [--days 30]` | 補料自我測試（每層固定網址、檢查 yt-dlp 版本，每天 06:40 排程）、最近 30 天各層成功率 | T-056 |
| `assets-topic <topic_id> [--voiceover]` | 盤點 `media/` 的素材，寫 `assets.json` 與 `media.md`，列出缺口；新影片會做場景偵測，寫進 `usable_ranges_ms` | T-024、T-058 |
| `init [path]`、`doctor`、`migrate` | 建立工作區（只補缺、不覆蓋）、檢查環境並給修正建議、建立資料表 | T-008 |
| `collect-signals`；（排程）`signals-snapshot`、`newsletter-split` | 每小時記 HN 分數、GitHub star、HF likes 的時間序列；把電子報每一期拆成每則連結 | T-036、T-037 |
| `source-stats [--days 14]` | 各信源最近幾天的抓取成功率與最近一次錯誤（來自 `fetch_runs`） | T-040 |
| `set-title-zh --list` 或 `<cand_id> "<中文標題>" [--force]` | 列出還是外文標題的候選；替候選存中文標題（只翻一次），inbox 之後改顯示中文，沒有就顯示原文 | T-101 |
| （排程）`page-watch` | 每 10 分鐘讀自架的 changedetection.io（`deploy/changedetection.yml`），網頁有變動就在 inbox 加一則「<頁面> 有更新」候選，備註附差異；沒設定就略過，服務停了不影響其他來源 | T-105 |

另外：S 級候選第一次出現時通知一次（T-103，`FEISHU_INTERNAL_ENABLED=true` 且設了 `FEISHU_ALERT_WEBHOOK_URL` 才真的送，否則只記日誌）；S／A 級候選用 Threads 關鍵字搜尋查中文圈有沒有人報導，寫在 inbox 的「在地」一行（T-104，需要 `THREADS_ACCESS_TOKEN`，沒有就顯示「未檢查」）；X 貼文的互動數相對該帳號中位數的倍數是熱度訊號 `x_velocity`（預設關閉）。

相關 skill 在外層 [../skills/](skills/)：`open-topic`、`gather-assets`、`triage-inbox`。

**發表會行事曆**：在工作區的 `calendar/events.md` 填表（日期 `YYYY-MM-DD`、時間 `10:00` 或 `10:00-12:30`、活動、關鍵字用逗號隔開、官方連結，臺北時間）。活動前一天 inbox 會多一則「準備：…」候選；活動結束 24 小時內，標題含關鍵字的新聞直接判為 S 級。表格有誤的列會被略過。活動前後把收集頻率改成每 5 分鐘還沒做。

**手動新增的短網址**（t.co、bit.ly 等）會先展開成真正的頁面再去重複；展開失敗就維持原網址。

**前置資訊讀寫**（`src/newsroom/core/frontmatter.py`）：改指定欄位，其他欄位與內文原封不動。

**下載的限制**：YouTube 可以下載；Vimeo 與 X 推文目前需要登入（用自己帳號時，匯出 cookies 檔並設定 `YTDLP_COOKIES_FILE`）；網站擋下或 `robots.txt` 不允許的，只會成為素材缺口，不會繞過。需要先裝 `python -m pip install --user yt-dlp gallery-dl`。

**熱度分級**（`src/newsroom/collect/heat.config.json`）：S ＝ 預告過的發表會剛結束、或 6 小時內 3 個以上來源共振、或分數 ≥ 70；A ＝ 2 個來源、或分數 ≥ 40；其餘 B。分數是共振、HN 增速、GitHub／HF 增速、電子報提及、來源權重加權（0 到 100），每個候選在 inbox 都列出分數明細與分級原因，同樣的輸入一定得到同樣的結果。X 互動與在地缺口目前停用（權重 0，不報錯）。**門檻是初始值，還沒有用人工標記的樣本校準**，要用 `backtest-heat` 調整。

**inbox 的規則**：人只改勾選、節目、格式、備註；程式只改受管區塊。沒勾的候選帶到隔天標「昨日」，超過 48 小時自動略過；勾了還沒建題的持續保留。

**原文擷取的順序**：GitHub、arXiv、YouTube 用各自的介面，其他網頁用 Readability（Python 版用 readability-lxml，比 AIHOT 的 TypeScript 版少了串流內容、隱藏元素、各站規則的處理），有設定 `JINA_API_KEY` 時再用 Jina Reader 備援，都失敗就 blocked 並請人把原文貼進 `sources/manual-1.md`。依據見 [../docs/spikes/SPK-FET-1.md](docs/spikes/SPK-FET-1.md)。

## 手動安裝與跑測試（Windows）

網站與後端（TypeScript）需要 Node.js 24.22.1 以上、一個 PostgreSQL 17（用 Docker 跑最省事）；收集與補料（Python）需要 Python 3.12 以上與 ffmpeg（素材盤點要用 `ffprobe`）。

```bash
docker run -d --name mediahot-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v mediahot-pgdata:/var/lib/postgresql/data postgres:17
npm install
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # 先建好這個資料庫
node scripts/migrate.ts                   # AIHOT 的資料表
cd .. && python -m pip install -e ".[dev]" && newsroom migrate   # 收集／補料用的資料表
export NEWSROOM_HOME=./AIHOT/.data/workspace    # 工作區（inbox、topics）放哪裡，預設就是這個
```

測試：

```bash
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_test   # 名稱必須以 _test 或 _ci 結尾
cd AIHOT && npm run typecheck && npm test            # 網站與後端（TypeScript）
cd .. && python -m pytest tests                       # 收集與補料（Python）；沒有 DATABASE_URL 時，要資料庫的測試會略過
```

注意：完整的 `npm test` 在 Windows 上有 3 個 `SIGTERM during …` 測試會逾時（Windows 沒有真正的 SIGTERM），這與我們的改動無關，其他測試通過；並行建測試資料庫時偶爾有一個檔案互搶失敗，單獨跑都會通過（可用 `--test-concurrency=1`）。

## 環境變數（我們新增的）

| 變數 | 用途 |
|---|---|
| `NEWSROOM_HOME` | 工作區位置（預設 `AIHOT/.data/workspace`） |
| `INBOX_DIR` | inbox 放哪裡（預設工作區底下的 `inbox`） |
| `GITHUB_TOKEN`、`JINA_API_KEY` | 選用，擷取 GitHub 與備援讀取時用（引擎原有的憑證機制） |
| `CHANGEDETECTION_URL`、`CHANGEDETECTION_API_KEY` | 選用，自架 changedetection.io 的位置與金鑰；沒設定就不做網頁變動監測 |
| `THREADS_ACCESS_TOKEN` | 選用，Threads 關鍵字搜尋權杖（在地熱度檢查）；沒有就顯示「未檢查」 |
| `FEISHU_INTERNAL_ENABLED`、`FEISHU_ALERT_WEBHOOK_URL` | 選用，S 級通知送到飛書警報群；沒開就只記日誌 |
| `SOCIALDATA_API_KEY` | 選用，X 貼文影片補料（L1）與 AIHOT 的 X 收集器共用 |
| `YTDLP_COOKIES_FILE` | 選用，自己帳號的 cookies 檔路徑，給 yt-dlp、gallery-dl 用；不要放進版本控制 |

其餘設定見 [.env.example](AIHOT/.env.example)。

## AIHOT 原作者的說明檔

- 引擎怎麼運作、怎麼換行業：[docs/customize.md](AIHOT/docs/customize.md)、[docs/architecture.md](AIHOT/docs/architecture.md)、[docs/sources.md](AIHOT/docs/sources.md)、[docs/selection.md](AIHOT/docs/selection.md)、[docs/deploy.md](AIHOT/docs/deploy.md)（AIHOT 原作者的檔案，部分說法仍指向原專案）。
- 我們的需求與規格：[../docs/spec/02_collect.md](docs/spec/02_collect.md)、[../docs/spec/03_fetch_assets.md](docs/spec/03_fetch_assets.md)、[../docs/spec/07_interfaces.md](docs/spec/07_interfaces.md)。

---

## 資料夾

```
docs/
  product/        產品需求（PRD）
  spec/           功能規格（SRS）、介面契約、狀態機
  architecture/   架構、技術選型
  process/        分工與 Sprint 計畫、分支策略
  runbooks/       操作手冊
  tickets/        所有的票（一張票一個檔案）
  diagrams/       架構圖、資料流、User Flow（archify JSON）；DAG、ERD、狀態圖（由 docs/tools/ 產生）
  tools/          產生 DAG 與圖表的腳本
  archive/        已被取代的舊文件，只供參考
```

程式碼會放在根目錄的 `src/`、`render/`、`schemas/`、`tests/`。結構見[架構 §4.1](docs/architecture/architecture.md#41-程式碼目錄)。
