# AI Media Studio 內容產線

把 AI 與科技新聞做成 Threads 貼文、IG 輪播和 Reel 的半自動產線。目前是內部 POC。

**主要流程**：收集素材 → 選題 → 寫稿 → 做輪播／字幕／Reel → 人工確認 → 發布 → 回收成效。創作者在 Claude Code 裡用口語下需求，agent 依 Markdown skill 編排確定性工具（參考 [gbrain](https://github.com/garrytan/gbrain) 的 thin harness, fat skills）。完整操作流程見 [User Flow 圖](docs/diagrams/user-flow.png)。

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
