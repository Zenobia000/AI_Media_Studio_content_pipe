# AI Media Studio 內容產線

把 AI 與科技新聞做成 Threads 貼文、IG 輪播和 Reel 的半自動產線。目前是內部 POC。

**主要流程**：收集素材 → 選題 → 寫稿 → 做輪播／字幕／Reel → 人工確認 → 發布 → 回收成效。創作者在 Claude Code 裡用口語下需求，agent 依 Markdown skill 編排確定性工具（參考 [gbrain](https://github.com/garrytan/gbrain) 的 thin harness, fat skills）。完整操作流程見 [User Flow 圖](docs/diagrams/user-flow.png)。

---

## 這包裡有很多說明檔，先看哪一份

| 我想知道… | 看這份 |
|---|---|
| 整個專案是什麼、誰做什麼、怎麼合作 | 這份（根目錄 README.md） |
| **我（Albert）做到哪、哪些完成、哪些沒完成** | [AIHOT/PROGRESS.md](AIHOT/PROGRESS.md) |
| **AIHOT 後端改成 Python 之後，每個步驟我怎麼做** | 這份往下的〈後端改成 Python 之後〉 |
| 改寫之後修了哪些問題 | [docs/process/backend-python-fixes.md](docs/process/backend-python-fixes.md) |
| 收集、補料、inbox 的指令怎麼用、手動安裝、環境變數 | [docs/process/newsroom-commands.md](docs/process/newsroom-commands.md) |
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

**要打開才會抓。** 環境變數 `COLLECT_ENABLED=true` 才會排程收集（`python start_dev.py` 預設打開，`--no-collect` 關閉）。收集到的文章要經過模型分析才會出現在網站；`python start_dev.py` 預設會呼叫模型（設定從 `AIHOT/.env` 讀），加 `--no-models` 或 `.env` 沒有金鑰時不呼叫，文章只會停在「待分析」。

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

預設清單（`AIHOT/industry/sources.json`）共 **33 個信源，全部啟用**：RSS／Atom 30 個、JSON 介面 3 個。清單上的「預設間隔」是剛建立時的設定，**每天凌晨 4:20 會依各信源最近 7 天的產量自動重算**，一般信源會落在 15 到 60 分鐘之間，所以跑過一天之後實際上通常比表上更頻繁。

逐個信源的清單（名稱、網站、類型、等級、預設間隔）在 [docs/process/default-sources.md](docs/process/default-sources.md)。

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

改寫完成後發現並修掉的 26 個問題（從空資料庫建表、本機模型不照規則填值、輸出被截斷、簡體字、CI 第一次在 GitHub 上跑出來的問題、Ars Technica 正文只抓到一段等），逐項的原因與修法在 [docs/process/backend-python-fixes.md](docs/process/backend-python-fixes.md)。

---

## 本機開發與繁體中文顯示

**一個終端跑全部。** `python start_dev.py` 啟動資料庫、API、worker、網站，三個服務的輸出都在同一個終端，每行前面加 `[MediaHOT API]`、`[MediaHOT worker]`、`[MediaHOT 網站]`。按 Ctrl+C 全部停掉，任何一個服務自己結束時其餘也會停。要回到「三個服務各開一個視窗」，加 `--separate`。`python stop_dev.py` 仍然可以停（程序編號記在 `.data/dev-pids.json`）。　要把整套裝到另一台電腦（例如老師的），照 [docs/process/setup-new-machine.md](docs/process/setup-new-machine.md) 一步步做，裝完先跑 `python check_setup.py` 檢查環境。

**本機模型的繁體字。** 本機模型常用簡體字寫標題，所以用本機模型時（`python start_dev.py`，模型網址是本機）會自動打開 `LLM_TRADITIONAL=true`，在寫中文標題與摘要的提示詞最後加一句要求繁體；雲端模型與預設環境不加。網站顯示的是資料庫裡存的字，沒有另外做簡轉繁，簡體網站的中文文章會以簡體字顯示。

---

## 站內問答、聊天頁與 MCP（新增）

用本機的 Ollama（向量模型 `bge-m3`、聊天模型 `qwen3.5:9b-ctx32k`）做的，**只根據網站裡已分析過的文章**回答，不用雲端模型。程式在 `src/newsroom/rag/`。

聊天模型要先建好（只做一次）：`ollama pull qwen3.5:9b`，再 `ollama create qwen3.5:9b-ctx32k -f deploy/qwen3.5-9b-ctx32k.Modelfile`。文章分析用本機模型時也用同一個：`AIHOT/.env` 設 `LLM_MODEL=qwen3.5:9b-ctx32k` 和 `LLM_EXTRA_JSON={"reasoning_effort":"none"}`（關掉思考）。Ollama 預設的上下文只有 4096 個 token，分析的提示詞比它長，超過的部分會被截掉開頭而且不報錯，所以不能直接用 `qwen3.5:9b`。

| 要做什麼 | 怎麼用 |
|---|---|
| 把文章轉成向量 | `newsroom rag-index`（只處理新的或改過的；向量放在 AIHOT 原有的 `embeddings` 資料表，不用 pgvector） |
| 用意思找文章 | `newsroom rag-search "問題" --k 5` |
| 問答 | `newsroom ask "問題"`，會列出出處；找不到相關文章就說找不到 |
| 聊天頁 | 網站左側「AI 問答」（`/chat`）。可以問新聞內容、請它推薦文章、請它改寫或翻譯上一則回答；跟網站無關的要求（常識、寫程式、寫信、閒聊）一律回固定的一句「只能回答這個網站收錄的內容」，不交給模型；每次重新整理都是新對話，沒有登入就不保存。是 AIHOT 的「模組」（`AIHOT/modules/chat/`），沒有改任何現有頁面。後端是 `POST /api/chat` |
| 讓 Claude 用語意搜尋 | 原本的 MCP（`/api/mcp`，7 個工具）沒有動，和 Node 版一致；另外開了 `/api/mcp-rag`，只有一個工具 `mediahot_semantic_search`。Claude Code（加 `--scope user` 才會在 VS Code 面板的任何路徑都看得到；加完要開新對話）：`claude mcp add --scope user --transport http mediahot http://127.0.0.1:3000/api/mcp` 和 `claude mcp add --scope user --transport http mediahot-semantic http://127.0.0.1:3000/api/mcp-rag` |

**新文章：** `python start_dev.py` 的 worker 每 10 分鐘自動把新文章轉成向量（`RAG_INDEX_ENABLED=true`）；其他環境預設關著，要手動跑 `newsroom rag-index`。

**限制：** 回答品質受 `qwen3.5:9b` 限制：出處編號偶爾標錯（讀過 60 則回答，1 句標錯篇、1 則把出處寫成頁面認不得的格式）、資料庫裡已有的簡體標題原樣顯示。聊天沒有登入也沒有使用限制，只適合在本機用。claude.ai 網頁版與 Claude 桌面版的自訂連接器要公開的 HTTPS 網址，連不到 `127.0.0.1`；Claude Code 可以。

## 素材區：在網站上點一下，就抓原文、照片、影片

不用打指令（`add-source`、`open-topic`、`fetch-topic`、`fetch-media`）也能抓素材。左側欄「內容」有一項「素材」（`/materials`，是 AIHOT 的「模組」`AIHOT/modules/materials/`；後端是 `src/newsroom/api/routes/materials.py` 與 `src/newsroom/collect/picks.py`）：

1. 在文章旁邊按**書籤（收藏）**，文章就進了素材區。收藏與素材區是同一份清單：取消收藏就是移出素材區。
2. 打開「素材」頁，每篇有三個勾選：**文章**（網頁文字，存成 Markdown）、**照片**（網頁上的主要圖片）、**影片**（影片與 GIF）。勾要的，按「下載」；「全部下載」照各篇的勾選一起跑。
3. 下載在背景進行（同時最多 2 篇，其餘排隊），頁面每 3 秒更新狀態，完成後列出抓到的檔案、缺口和錯誤。檔案放在工作區 `topics/<題目編號>/`（`python start_dev.py` 時是 `.data/workspace/`），每篇第一次下載時自動建一個題目，所以之後仍可用 `newsroom assets-topic`、`preprocess-topic` 接著處理。

| 勾選 | 做什麼 |
|---|---|
| 文章 | `fetch-topic`：GitHub、arXiv、YouTube 用各自的介面，其他網頁用 Readability，失敗時用 Jina（要 `JINA_API_KEY`） |
| 照片 | 第 2 層（網頁自己的圖片，遵守 robots.txt）、第 3 層 gallery-dl |
| 影片 | 第 1 層（X、Threads 的平臺介面）、第 2 層（網頁自己的影片與 GIF）、第 3 層 yt-dlp |

**限制：** 素材區是給自己在本機用的，沒有登入：任何連得到這個 API 的人都能開始下載，所以不要把它開到網路上（要開放的話，先在 `/api/materials/{id}/fetch` 前面加上後臺登入）。請求要帶只有網站自己的頁面才會送的標頭，避免瀏覽器裡開著的其他網頁偷偷觸發下載。素材區最多 500 篇。第 4 層（瀏覽器錄影）只給要做 reel 的題目，素材區不跑。沒裝 yt-dlp、gallery-dl 時頁面會提醒，網頁自己提供的圖片和影片仍會抓。抓到的照片在「檔案與紀錄」裡顯示縮圖，影片可以直接播放，文章的 Markdown 點檔名開啟（`GET /api/materials/{id}/file`，只送清單上列出的檔案）。

---

## 收集、補料、inbox：我們改了什麼

| 專案 | 內容 |
|---|---|
| 站名與設定 | 站名 MediaHOT、繁體中文（zh-TW）、爬蟲標識 `MediaHOTBot`（`site/site.ts`） |
| 信源 | 33 個：官方 AI blog、英文科技媒體、TLDR AI 與 AINews 電子報、Hacker News、Hugging Face 論文與熱門模型、8 個 GitHub release、iThome 與科技新報（`industry/sources.json`） |
| 精選標準 | 讀者設定成「經營 Threads／IG 的 AI 內容創作者」，加入開源工具與產業事件的加分、一般資安新聞的壓分（`industry/prompts/selection-score.md`） |
| 分類 | 六個分類的顯示名稱改成繁體（`industry/taxonomy.ts`） |
| **inbox 與建題** | 已改寫成 Python（`src/newsroom/`）：每日 inbox、手動新增候選、跨日規則、建題與合併、原文擷取、素材盤點。指令見 [newsroom-commands.md](docs/process/newsroom-commands.md) |

提示詞與標籤詞表的其餘部分仍是簡體，門檻（`industry/selection.ts`）還沒有用我們標註的樣本校準。

## 收集、補料、inbox 的指令、安裝與環境變數

`newsroom <工具>` 的每個指令（`add-source`、`write-inbox`、`open-topic`、`fetch-topic`、`fetch-media`、`assets-topic` 等）、熱度分級與 inbox 的規則、手動安裝與跑測試、我們新增的環境變數，都在 [docs/process/newsroom-commands.md](docs/process/newsroom-commands.md)。

最短的開始方式：`python -m pip install -e ".[dev]" -c constraints.txt`（`constraints.txt` 固定每個套件的版本，讓每台電腦、GitHub 上的檢查和 Docker 映像裝到一樣的），再 `python start_dev.py`。

## AIHOT 原作者的說明檔

- 引擎怎麼運作、怎麼換行業：[customize.md](AIHOT/docs/customize.md)、[architecture.md](AIHOT/docs/architecture.md)、[sources.md](AIHOT/docs/sources.md)、[selection.md](AIHOT/docs/selection.md)、[deploy.md](AIHOT/docs/deploy.md)（AIHOT 原作者的檔案，部分說法仍指向原專案）。
- 我們的需求與規格：[02_collect](docs/spec/02_collect.md)、[03_fetch_assets](docs/spec/03_fetch_assets.md)、[07_interfaces](docs/spec/07_interfaces.md)。

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

程式在根目錄的 `src/newsroom/`，測試在 `tests/`，skill 在 `skills/`，Docker 與模型設定在 `deploy/`；規格裡的 `render/`、`schemas/` 還沒有建立。規劃的結構見[架構 §4.1](docs/architecture/architecture.md#41-程式碼目錄)。
