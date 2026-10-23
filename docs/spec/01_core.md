# SRS 01｜核心：Skills 編排、工具介面、工作區、資料庫、執行紀錄、排程、通知、預算

| 項目 | 內容 |
|---|---|
| 版本 | v1.1｜2026-10-24（編排改為 Skills＋AI agent，見架構 ADR-09） |
| 擁有者 | CORE-FR-001～008（工具介面、工作區、資料庫）：**Albert Chiang**；CORE-FR-010～016（執行紀錄）、AGT-FR-004～006、008、OPS-FR：**楊舒媁**；AGT-FR-001～003、007（skill 格式、路由、eval）：**周長曉** |
| 對應 Epic | E01 核心與工作區、E10 排程、通知與營運 |

---

## 1. 模組責任

| 子模組 | 責任 | 不負責 |
|---|---|---|
| skills | 每個環節的流程、判斷標準、交付物；告訴 agent 該呼叫哪些工具 | 改狀態（只能透過工具） |
| 工具介面（cli.py） | 把每個工具包成 `newsroom <tool>`，JSON 進出（IF-020），寫執行紀錄 | 任何商業邏輯 |
| workspace | 初始化、路徑解析、受管區塊讀寫、原子寫入、front-matter 讀寫 | 判斷內容好壞 |
| db | SQLite 連線、schema migration、Repository | 跨模組的流程 |
| ledger | run_id、工具呼叫紀錄、指紋、鎖、狀態機、重跑 | 流程順序（由 skill 決定） |
| scheduler | 定時執行不需判斷的工具（收集、成效回收），以及 headless 執行需要判斷的 skill（早報、週報） | 收集本身的邏輯 |
| notify | 推播與通知 | 決定什麼時候該通知（由呼叫者決定） |
| budget | 記錄每次付費呼叫與 headless agent 的花費、檢查上限 | 計算單價（由 adapter 回報） |

## 2. 操作方式與工具目錄

**人怎麼操作**：在工作區目錄開 Claude Code，直接說要做什麼，例如「看今天的 inbox」「第 3 題做成 Reel，Threads 也出一篇」「這篇改短一點再出一版」。agent 依 `CLAUDE.md` 的 RESOLVER 找到對應的 skill，照 skill 呼叫工具。人不需要記指令；要除錯時也可以直接執行工具。

**Skills**（`skills/<name>/SKILL.md`，init 時連結到工作區 `.claude/skills/`）

| Skill | 做什麼 | 主要呼叫的工具 | 擁有者 | Release |
|---|---|---|---|---|
| `triage-inbox` | 讀 inbox，補中文標題、建議節目與格式、指出值得做的題 | `inbox`、`status` | Albert | R5 |
| `open-topic` | 依人選的候選建題，決定節目、格式、角度 | `open-topic` | Albert | R1 |
| `gather-assets` | 決定要用哪一層補料、缺什麼、要不要請人補 | `fetch`、`assets` | Albert | R1 |
| `draft-content` | 抽事實、寫稿、照 voice 與節目模板、處理待確認 | `facts-verify`、`draft-save`、`select` | 周長曉 | R1 |
| `make-carousel` | 決定版型與分頁，出圖、檢查溢版 | `carousel` | 周長曉 | R2 |
| `make-subtitles` | 對齊、斷詞分行、處理念稿差異 | `subtitle` | 周長曉 | R3 |
| `make-reel` | 節奏表轉鏡頭、渲染、規格檢查、低解析預覽 | `reel`、`spec-check` | 周長曉 | R3 |
| `produce-topic` | 從選題一路到草稿：建題 → 補料 → 草稿；依情況跳過或補做 | 呼叫上面三個 skill | 楊舒媁 | R1 |
| `review-and-approve` | 整理預覽與待確認項給人看，人說確認後才呼叫 approve | `review`、`approve` | 楊舒媁 | R1 |
| `publish` | 產生發布包、記錄排程與發布網址；R6 起自動發布 | `package`、`schedule`、`mark-published` | 楊舒媁 | R1 |
| `recover` | 看失敗與阻擋原因，決定重跑哪一步或請人處理 | `status`、`retry` | 楊舒媁 | R1 |
| `morning-brief` | 每天早上整理 S／A 級候選、進行中與被擋住的工作 | `inbox`、`status`、`notify` | 楊舒媁 | R4 |
| `weekly-retro` | 讀成效數據寫週報與調整建議 | `retro-data`、`metrics` | 楊舒媁 | R2 |

**工具**（`newsroom <tool> [args] --json`，輸出格式見 IF-020）

| 工具 | 說明 | 擁有者 | Release |
|---|---|---|---|
| `init [path]` | 建立工作區；產生 `CLAUDE.md`，把 skills 連結到 `.claude/skills/` | Albert | R1 |
| `doctor` | 檢查環境：Python、Node、ffmpeg、Docker、GPU、金鑰、外部服務、Claude Code | Albert | R1 |
| `tools` | 列出所有工具、參數與說明，給 agent 查 | Albert | R1 |
| `add-source <url> [--note]` | 手動新增候選 | Albert | R1 |
| `collect [--source <name>]` | 執行一輪收集 | Albert | R2 |
| `inbox [--date]` | 重新產生或讀取 inbox | Albert | R1 |
| `open-topic <candidate_id>... --program --formats` | 建題 | Albert | R1 |
| `fetch <topic_id> [--layer]` | 補料 | Albert | R1 |
| `assets <topic_id>` | 盤點素材 | Albert | R1 |
| `facts-verify <topic_id>` | 驗證 agent 寫的 facts.json：schema、原文摘錄逐字存在 | 周長曉 | R1 |
| `draft-save <content_ref> <dir>` | 驗證 agent 寫的草稿（schema、asset_id、引用比對），存成新 revision | 周長曉 | R1 |
| `select <content_ref> <revision>` | 選用某個草稿版本 | 周長曉 | R1 |
| `carousel <content_ref>` | 產生輪播 | 周長曉 | R2 |
| `subtitle <content_ref>` | 產生字幕 | 周長曉 | R3 |
| `reel <content_ref> [--preview]` | 產生 Reel | 周長曉 | R3 |
| `spec-check <build_id>` | 成品規格檢查 | 楊舒媁 | R3 |
| `review <content_ref>` | 預覽與待確認項 | 楊舒媁 | R1 |
| `approve <content_ref> [--build]`／`unapprove` | 確認成品；只接受人在對話中明確確認後的呼叫（AGT-FR-005） | 楊舒媁 | R1 |
| `package <content_ref>` | 產生發布包 | 楊舒媁 | R2 |
| `schedule <content_ref> --platform --at` | 排程發布 | 楊舒媁 | R1（紀錄）／R6（自動） |
| `mark-published <content_ref> --platform --url [--at]` | 填回發布資訊 | 楊舒媁 | R1 |
| `metrics add|pull` | 手填或自動回收成效 | 楊舒媁 | R2／R4 |
| `retro-data [--week]` | 彙整週報需要的數字（不寫結論） | 楊舒媁 | R2 |
| `status [<ref>] [--run <run_id>]` | 進度、阻擋、失敗原因；指定 run 時列出那次 agent 執行的所有工具呼叫 | 楊舒媁 | R1 |
| `retry <job_id>` | 重跑失敗的工具呼叫 | 楊舒媁 | R1 |
| `run <skill> [--max-turns] [--max-usd]` | headless 執行 skill（包 `claude -p`），排程用 | 楊舒媁 | R2 |
| `budget` | 顯示花費與上限 | 楊舒媁 | R4 |
| `notify <level> <message>` | 推播 | 楊舒媁 | R4 |

---

## 3. 功能需求｜工具介面、工作區、資料庫（Albert Chiang）

**CORE-FR-001｜工作區初始化**（R1｜M｜US-001）
- **輸入**：`newsroom init [path]`；path 預設為目前目錄下的 `newsroom/`。
- **處理**：建立 SRS 00 §5.1 的目錄；寫入範本檔（`config.toml`、`voice.md`、`glossary.md`、`watchlist/*.md`、`calendar/events.md`）；產生工作區 `CLAUDE.md`（RESOLVER 與工作規則，AGT-FR-002）；把 repo 的 `skills/` 連結到 `.claude/skills/`；建立 SQLite 並執行所有 migration；寫入 `.gitignore`（忽略 `data/`、`logs/`、`media/`）。
- **輸出**：建立的檔案清單；下一步提示（設定 `NEWSROOM_HOME`、填寫 voice.md）。
- **例外**：目錄已存在而且已經初始化過時，只補上缺少的檔案，不覆寫任何已存在的檔案。
- **驗收**：在空目錄執行，建立完整結構，在該目錄開 Claude Code 看得到所有 skill；再執行一次，任何既有檔案的 mtime 都不變。

**CORE-FR-002｜環境檢查**（R1｜M｜US-001）
- **處理**：檢查 Python 版本、Node 版本、ffmpeg／ffprobe、Docker 服務、GPU（`nvidia-smi`）、Claude Code 是否安裝並登入、config 裡啟用的整合所需要的環境變數、各外部服務是否連得上。
- **輸出**：每一項是 ok／warn／fail，附修正建議；`--json` 輸出機器可讀格式。
- **驗收**：拿掉 ffmpeg，顯示 fail 並附安裝建議；結束碼不是 0。

**CORE-FR-003｜工作區定位與路徑**（R1｜M）
- **處理**：依序找 `--workspace`、`NEWSROOM_HOME`、目前目錄往上找含 `config.toml` 的 `newsroom/`。所有存進資料庫的路徑都相對於工作區根目錄。
- **例外**：找不到時回報錯誤，並提示執行 `init`。
- **驗收**：在子資料夾執行指令也能找到工作區；把工作區搬到別的路徑後仍可使用（NFR-015）。

**CORE-FR-004｜受管區塊讀寫**（R1｜M｜NFR-004）
- **處理**：提供 `read_block(path, name)`、`write_block(path, name, content)`；只改寫標記之間的內容；寫入使用原子寫入。
- **例外**：標記不成對或重複時，回報 `E_MANAGED_BLOCK`，不寫入。
- **驗收**：區塊外有人寫的文字，寫入後逐位元組相同；標記損壞的檔案不被改動。

**CORE-FR-005｜front-matter 讀寫**（R1｜M）
- **處理**：讀寫 YAML front-matter，保留未知欄位與本文；依 07 的 schema 驗證。
- **驗收**：加入一個自訂欄位後，程式更新其他欄位時，自訂欄位仍然存在。

**CORE-FR-006｜SQLite schema 與 migration**（R1｜M）
- **處理**：用編號的 SQL migration 檔（`migrations/0001_init.sql` …）；啟動時自動套用尚未套用的 migration；開啟 WAL 模式與 foreign key。
- **資料表**（第一版）：見 §6。
- **驗收**：從空資料庫套用所有 migration 成功；重複套用不報錯；`PRAGMA foreign_keys` 為 on。

**CORE-FR-007｜設定載入**（R1｜M）
- **處理**：讀 `config.toml`，用 Pydantic 驗證；金鑰從環境變數讀；每個整合都有 `enabled`。
- **例外**：設定錯誤時，指出是哪個欄位、期待什麼值。
- **驗收**：把某個整合設成 `enabled=false`，相關指令會顯示「未啟用」，而不是報錯。

**CORE-FR-008｜日誌**（R1｜M｜NFR-011）
- **處理**：依 SRS 00 §5.7 寫 JSONL；提供一個遮罩函式，所有外部呼叫的參數與 header 都要先經過它。
- **驗收**：用假的金鑰執行整個流程，`grep` 日誌找不到金鑰字串。

## 4. 功能需求｜執行紀錄與關卡（楊舒媁）

原本的「Job 協調器」不再決定流程順序，只保留冪等、鎖、狀態、重跑。順序由 skill 決定；每個工具呼叫都經過這一層，所以 agent 重試或亂序呼叫也安全。

**CORE-FR-010｜工具呼叫的冪等執行**（R1｜M｜US-002、US-004）
- **輸入**：工具的 Step 實作（IF-015）＋目標物件＋呼叫者的 `run_id`（沒有就是人直接呼叫）。
- **處理**：
  1. 計算 `fingerprint`。若已有相同指紋而且成功的 Job，直接回傳先前的結果，不再執行。
  2. 取得目標物件的鎖。取不到時回報 `E_LOCKED`。
  3. 建立 Job（queued → running，記下 run_id），呼叫 `Step.run`。
  4. 依 StepResult 把 Job 設為 succeeded／blocked／failed，記錄輸出路徑、錯誤、花費。
  5. 釋放鎖。
- **驗收**：同一個 Step 用相同輸入執行兩次，`run` 只被呼叫一次；平行執行兩次，第二次得到 `E_LOCKED`。

**CORE-FR-011｜鎖**（R1｜M）
- **處理**：以 SQLite 的 `locks` 資料表實作，記錄持有者的 PID 與心跳時間。持有者 PID 已不存在，或心跳超過 10 分鐘時，視為失效，可以接手。
- **驗收**：kill 掉持有鎖的程序，下一次執行可以取得鎖。

**CORE-FR-012｜中斷復原**（R1｜M｜NFR-002）
- **處理**：每次工具啟動時，把「running 但持有者已不存在」的 Job 改成 failed，`error_code=E_INTERRUPTED`，可以重跑。
- **驗收**：渲染途中 kill，重新執行 `status` 會顯示這個 Job 失敗、可以重跑。

**CORE-FR-013｜單步重跑**（R1｜M｜US-002）
- **輸入**：`newsroom retry <job_id>`，或重新呼叫原本的工具（通常由 `recover` skill 決定）。
- **處理**：建立同一個目標、同一個 kind 的新 attempt；先前成功的其他 Job 不重跑。
- **驗收**：`produce-topic` 執行中 `draft-save` 失敗，修好後重跑 skill，`fetch` 不會再執行一次。

（CORE-FR-014「produce 管線」已移除，由 AGT-FR-008 `produce-topic` skill 取代。）

**CORE-FR-015｜進度與狀態查詢**（R1｜M｜US-003、NFR-010）
- **處理**：`newsroom status` 彙整 Content 與 Job 的狀態；依「需要人處理（blocked）」→「失敗」→「進行中」→「完成」排序；每一項附上下一步建議（要叫哪個 skill 或工具）。`--run <run_id>` 列出那次 agent 執行呼叫的所有工具、結果與花費。同時更新 topic.md、content.md 的狀態受管區塊。
- **驗收**：製造 blocked（缺錄音）與 failed（API 錯誤）兩種狀況，輸出都附有正確的下一步建議；`--json` 可被 `recover` skill 讀取。

**CORE-FR-016｜Content 狀態轉換**（R1｜M）
- **處理**：以 07 狀態圖定義的轉換表實作狀態機；所有改狀態的操作都要經過它；不合法的轉換一律拒絕。
- **驗收**：對一則 drafting 的內容執行 approve，被拒絕並說明原因。


## 4.1 功能需求｜Skills 編排（AGT）

**AGT-FR-001｜Skill 檔案格式**（R1｜M）
- **處理**：每個 skill 是 `skills/<name>/SKILL.md`，front-matter 依 IF-019：`name`、`description`（Claude Code 用它判斷何時載入）、`inputs`、`outputs`（引用 IF 編號）、`tools`（允許呼叫的工具）、`gates`（需要人確認的點）。本文寫：什麼時候用、步驟、判斷標準、每一步呼叫哪個工具、失敗時怎麼辦、不要做什麼。
- **規則**：skill 不能寫入 SQLite，也不能改受管區塊；所有狀態變更都透過工具。skill 可以直接寫自己負責的草稿檔（例如 drafts 的暫存目錄），再交給工具驗證。
- **驗收**：`newsroom tools --check-skills` 檢查每個 SKILL.md 的 front-matter 符合 IF-019，`tools` 欄位裡的工具都存在。

**AGT-FR-002｜RESOLVER 與工作區 CLAUDE.md**（R1｜M）
- **處理**：`skills/RESOLVER.md` 是一張對照表：哪種需求（例如「做成 Reel」「為什麼失敗」「今天有什麼」）先讀哪份 skill。`init` 產生的工作區 `CLAUDE.md` 控制在 200 行內，只放：角色、硬性規則（AGT-FR-005 的關卡）、RESOLVER 的指標、工具目錄的指標。
- **驗收**：準備 15 句真實的口語需求，agent 載入正確 skill 的比例 ≥ 90%（列入 AGT-FR-007 的案例）。

**AGT-FR-003｜工具介面契約**（R1｜M）
- **處理**：每個工具都是 `newsroom <tool> [args] --json`；成功與失敗都輸出 IF-020 的 JSON（`ok`、`data`、`error_code`、`message`、`next`）；結束碼依 SRS 00 §5.4。`newsroom tools --json` 列出所有工具、參數與說明。
- **驗收**：對每個工具做契約測試：故意給錯參數，輸出仍是合法的 IF-020 JSON，結束碼正確。

**AGT-FR-004｜執行紀錄（run_id）**（R1｜M｜NFR-010）
- **處理**：每次 agent 執行（互動 session 的一個任務，或一次 `newsroom run`）有一個 `run_id`（ULID），寫進 `agent_runs`（skill、觸發方式、開始結束時間、花費、結果）。agent 呼叫工具時透過環境變數 `NEWSROOM_RUN_ID` 帶上；工具建立的 Job 都記錄 run_id。
- **驗收**：跑一次 `produce-topic`，`status --run <run_id>` 依時間順序列出它呼叫的每個工具、結果與花費。

**AGT-FR-005｜硬性關卡在工具層**（R1｜M｜US-070、PRD G5）
- **處理**：以下規則寫在工具裡，不靠 skill：
  1. `approve` 需要 `--confirmed-by-human` 加上人在對話中的確認原文（記在 approvals）；headless 執行（`newsroom run`）一律不能呼叫 `approve`、`schedule`（R6 自動發布除外，見 PUB-FR）。
  2. 發布類工具沒有有效的 approval 就拒絕（`E_NOT_APPROVED`）。
  3. 狀態轉換只走狀態機（CORE-FR-016）；預算超過就 blocked（OPS-FR-006）。
- **驗收**：在 headless 模式讓 agent 嘗試 approve，工具拒絕並記錄；在互動模式沒有確認原文時，approve 被拒絕。

**AGT-FR-006｜headless 執行 skill**（R2｜M｜US-090）
- **處理**：`newsroom run <skill>` 包裝 `claude -p`：在工作區目錄執行、指定 skill、設定最大回合數與花費上限（config）、逾時（預設 20 分鐘）；讀取 JSON 輸出的花費寫入 `agent_runs` 與 `spend`；失敗或逾時時通知。
- **驗收**：用一個會無限重試的假 skill 執行，達到最大回合數後停止，`agent_runs` 記錄 `max_turns`，並發出通知。

**AGT-FR-007｜Skill 固定案例（eval）**（R1｜M）
- **處理**：每個 skill 有 `evals/*.yaml`：輸入（fixture 工作區狀態與一句需求）、預期（載入哪個 skill、呼叫了哪些工具、產出通過哪些 schema 與檢查）。`newsroom eval <skill>` 用 headless 模式跑，結果寫成報告；斷言只檢查確定性的東西（檔案、schema、工具呼叫序列），不比對文字。
- **規則**：改任何 SKILL.md 的 PR，都要附上改前改後的 eval 結果。
- **驗收**：故意把 `draft-content` 的一條規則刪掉（例如「沒有 fact 的數字不能寫」），對應案例失敗。

**AGT-FR-008｜produce-topic skill**（R1｜M｜US-002）
- **處理**：取代原本寫死的 produce 管線（舊 CORE-FR-014）。skill 依序呼叫 `open-topic` → `gather-assets` → `draft-content`，但可以依情況調整：素材已齊就跳過補料；人說「只出 Threads」就只寫一種；補料有缺口時先回報並問人要不要等。任一題 blocked 或 failed 時，記下原因並繼續處理其他題。
- **輸出**：每個 topic、每則 content 的結果摘要，附 run_id。
- **驗收**：三題中有一題補料失敗，其他兩題仍然產出草稿，摘要清楚列出失敗那一題的原因；同一個 eval 案例跑 3 次，3 次都產出通過 schema 的草稿。

## 5. 功能需求｜排程、通知、預算（楊舒媁）

**OPS-FR-001｜排程器**（R2｜M｜US-090）
- **處理**：用 systemd timer（WSL 開啟 systemd）或 cron 定時執行兩類工作：不需判斷的工具直接執行（`newsroom collect`，預設每 15 分鐘）；需要判斷的 skill 透過 `newsroom run`（AGT-FR-006，例如 07:30 `morning-brief`、週一 `weekly-retro`）。排程設定寫在 `config.toml`。提供 `newsroom scheduler install|uninstall|status` 產生並安裝排程設定。
- **例外**：前一輪還沒結束時，跳過這一輪（靠鎖）。
- **驗收**：安裝後重開 WSL，排程仍然存在並會執行；在日誌裡看到每 15 分鐘一筆收集紀錄。

**OPS-FR-002｜心跳與健康檢查**（R2｜S｜US-090）
- **處理**：每次收集都寫一筆心跳。超過設定時間（預設 1 小時）沒有心跳時，下一次執行任何指令都會警告；有設定通知時也推播一次。
- **驗收**：停用排程 1 小時後執行 `status`，顯示警告。

**OPS-FR-003｜通知**（R4｜S｜US-016）
- **處理**：透過 notifier adapter 發送通知（第一個實作是 ntfy，見技術選型）。通知分 `info`／`warn`／`urgent`；同一事件 30 分鐘內不重複通知。
- **驗收**：觸發 S 級事件，手機收到一則通知；30 分鐘內再觸發，不再收到。

**OPS-FR-004｜收集頻率切換**（R4｜S｜US-017）
- **處理**：依 `calendar/events.md`，在活動開始前 1 小時到結束後 6 小時之間，把收集頻率改成每 5 分鐘；其他時間恢復預設。
- **驗收**：建立一個 10 分鐘後開始的假活動，觀察頻率自動切換。

**OPS-FR-005｜早報**（R4｜S｜US-091）
- **處理**：每天 07:30 以 `newsroom run morning-brief` 產生 `reports/morning/YYYY-MM-DD.md`：昨晚到現在的 S／A 級候選與建議切角、進行中的內容、被阻擋的工作、今天的發表會；有設定通知時推播摘要。數字一律來自 `inbox`／`status` 工具的輸出，agent 只負責取捨與文字。
- **驗收**：在固定時間觸發後，檔案裡的數字與資料庫一致；`agent_runs` 有這次執行與花費。

**OPS-FR-006｜預算控管**（R4｜M｜US-092、NFR-006）
- **處理**：每次付費呼叫前檢查 `budget` 設定（每日、每月，依整合分開；headless agent 執行也算一個整合）；呼叫後記錄實際花費。用到 80% 時警告，達到 100% 時回報 `E_BUDGET`，Job 設為 blocked；`newsroom run` 在預算用完時不啟動。
- **驗收**：上限設成 0.01 美元，下一次 LLM 呼叫被擋下，Job 顯示 blocked 與原因。

**OPS-FR-007｜補料自我測試排程**（R2｜S｜US-025）
- **處理**：每天早上用 config 裡的固定測試網址呼叫 FET-FR-010，失敗時通知。
- **驗收**：放一個已經失效的測試網址，隔天早上收到通知。

## 6. 資料表（第一版）

| 資料表 | 主要欄位 | 擁有者 |
|---|---|---|
| `sources` | source_id PK、url、canonical_url、kind、publisher、author、published_at、first_seen_at、raw_json、local_path | Albert |
| `source_signals` | source_id FK、collector、signal（score、comments、likes…）、captured_at | Albert |
| `candidates` | candidate_id PK、title、title_zh、tier、suggest_program、suggest_formats、first_seen_at、cluster_key、status（new／picked／skipped／merged）、merged_into | Albert |
| `candidate_sources` | candidate_id FK、source_id FK | Albert |
| `topics` | topic_id PK、title、program、tier、created_at | Albert |
| `topic_sources` | topic_id FK、source_id FK | Albert |
| `topic_candidates` | topic_id FK、candidate_id FK | Albert |
| `contents` | topic_id＋content_id PK、program、format、status、selected_revision、wave、created_at、updated_at | 楊舒媁（狀態機）／周長曉（revision） |
| `assets` | topic_id＋asset_id PK、path、type、origin、source_id、fetch_layer、sha256、duration_ms、meta_json | Albert |
| `agent_runs` | run_id PK、skill、trigger（interactive／schedule）、status、started_at、ended_at、turns、cost_usd、summary | 楊舒媁 |
| `jobs` | job_id PK、run_id、kind、target、status、attempt、input_fingerprint、output_paths_json、error_code、error_message、retry_after、cost_usd、時間戳 | 楊舒媁 |
| `locks` | target PK、job_id、pid、heartbeat_at | 楊舒媁 |
| `builds` | build_id PK、content_ref、format、manifest_path、status、stale | 周長曉 |
| `approvals` | approval_id PK、content_ref、build_id（文字格式可空）、text_sha256、approved_at、revoked_at、run_id、human_quote | 楊舒媁 |
| `publications` | publication_id PK、content_ref、build_id、platform、status、scheduled_at、published_at、url、external_id、error | 楊舒媁 |
| `metric_snapshots` | publication_id FK、captured_at、source、metrics_json | 楊舒媁 |
| `spend` | ts、integration、job_id、cost_usd、units | 楊舒媁 |
| `heartbeats` | component、ts | 楊舒媁 |
| `events` | event_id PK、title、starts_at、ends_at、url、expected_topics | Albert |

規則：每個表都有 migration；所有時間都存 ISO 8601 含時區；JSON 欄位都要能用 07 的 schema 驗證。
