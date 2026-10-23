# SRS 01｜核心：CLI、工作區、資料庫、Job、排程、通知、預算

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | CORE-FR-001～008（CLI、工作區、資料庫）：**Albert Chiang**；CORE-FR-010～016（Job 協調）與 OPS-FR：**楊舒媁** |
| 對應 Epic | E01 核心與工作區、E10 排程、通知與營運 |

---

## 1. 模組責任

| 子模組 | 責任 | 不負責 |
|---|---|---|
| CLI | 解析指令、找到工作區、呼叫對應服務、格式化輸出 | 任何商業邏輯 |
| workspace | 初始化、路徑解析、受管區塊讀寫、原子寫入、front-matter 讀寫 | 判斷內容好壞 |
| db | SQLite 連線、schema migration、Repository | 跨模組的流程 |
| orchestrator | 建立 Job、鎖、指紋比對、執行 Step、記錄結果、重跑 | Step 內部做什麼 |
| scheduler | 定時觸發收集、早報、成效回收、發表會頻率切換 | 收集本身的邏輯 |
| notify | 推播與通知 | 決定什麼時候該通知（由呼叫者決定） |
| budget | 記錄每次付費呼叫的花費、檢查上限 | 計算單價（由 adapter 回報） |

## 2. CLI 指令總表

| 指令 | 說明 | 擁有者 | Release |
|---|---|---|---|
| `newsroom init [path]` | 建立工作區 | Albert | R1 |
| `newsroom doctor` | 檢查環境：Python、Node、ffmpeg、Docker、GPU、金鑰、外部服務 | Albert | R1 |
| `newsroom add-source <url> [--note]` | 手動新增候選 | Albert | R1 |
| `newsroom collect [--source <name>]` | 執行一輪收集 | Albert | R2 |
| `newsroom inbox [--date]` | 重新產生或檢視 inbox | Albert | R1 |
| `newsroom produce [--date] [--dry-run]` | 處理勾選的候選：建題 → 補料 → 草稿 | 楊舒媁（流程）／Albert（建題、補料）／周長曉（草稿） | R1 |
| `newsroom fetch <topic_id>` | 單獨重跑補料 | Albert | R1 |
| `newsroom assets <topic_id>` | 重新盤點素材 | Albert | R1 |
| `newsroom redraft <content_ref>` | 重新生成草稿，另存新版本 | 周長曉 | R1 |
| `newsroom select <content_ref> <revision>` | 選用某個草稿版本 | 周長曉 | R1 |
| `newsroom subtitle <content_ref>` | 產生字幕 | 周長曉 | R3 |
| `newsroom carousel <content_ref>` | 產生輪播 | 周長曉 | R2 |
| `newsroom reel <content_ref>` | 產生 Reel | 周長曉 | R3 |
| `newsroom review <content_ref>` | 顯示預覽與待確認項 | 楊舒媁 | R1 |
| `newsroom approve <content_ref> [--build]` | 確認成品 | 楊舒媁 | R1 |
| `newsroom schedule <content_ref> --platform --at` | 排程發布 | 楊舒媁 | R1（紀錄）／R6（自動） |
| `newsroom mark-published <content_ref> --platform --url [--at]` | 填回發布資訊 | 楊舒媁 | R1 |
| `newsroom metrics add <publication_id> ...` | 手填成效 | 楊舒媁 | R2 |
| `newsroom retro [--week]` | 產生週報 | 楊舒媁 | R2 |
| `newsroom status [<ref>]` | 顯示進度、阻擋與失敗原因 | 楊舒媁 | R1 |
| `newsroom retry <job_id>` | 重跑失敗的 Job | 楊舒媁 | R1 |
| `newsroom budget` | 顯示花費與上限 | 楊舒媁 | R4 |

Claude Code 的 slash command（`/inbox`、`/produce`、`/redraft`、`/carousel`、`/reel`、`/review`、`/retro`）放在 `.claude/commands/`，內容只呼叫對應的 CLI，並把結果整理給人看。

---

## 3. 功能需求｜CLI、工作區、資料庫（Albert Chiang）

**CORE-FR-001｜工作區初始化**（R1｜M｜US-001）
- **輸入**：`newsroom init [path]`；path 預設為目前目錄下的 `newsroom/`。
- **處理**：建立 SRS 00 §5.1 的目錄；寫入範本檔（`config.toml`、`voice.md`、`glossary.md`、`watchlist/*.md`、`calendar/events.md`）；建立 SQLite 並執行所有 migration；寫入 `.gitignore`（忽略 `data/`、`logs/`、`media/`）。
- **輸出**：建立的檔案清單；下一步提示（設定 `NEWSROOM_HOME`、填寫 voice.md）。
- **例外**：目錄已存在而且已經初始化過時，只補上缺少的檔案，不覆寫任何已存在的檔案。
- **驗收**：在空目錄執行，建立完整結構；再執行一次，任何既有檔案的 mtime 都不變。

**CORE-FR-002｜環境檢查**（R1｜M｜US-001）
- **處理**：檢查 Python 版本、Node 版本、ffmpeg／ffprobe、Docker 服務、GPU（`nvidia-smi`）、config 裡啟用的整合所需要的環境變數、各外部服務是否連得上。
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

## 4. 功能需求｜Job 協調（楊舒媁）

**CORE-FR-010｜Job 建立與執行**（R1｜M｜US-002、US-004）
- **輸入**：Step 實作（IF-015）＋目標物件。
- **處理**：
  1. 計算 `fingerprint`。若已有相同指紋而且成功的 Job，直接回傳先前的結果，不再執行。
  2. 取得目標物件的鎖。取不到時回報 `E_LOCKED`。
  3. 建立 Job（queued → running），呼叫 `Step.run`。
  4. 依 StepResult 把 Job 設為 succeeded／blocked／failed，記錄輸出路徑、錯誤、花費。
  5. 釋放鎖。
- **驗收**：同一個 Step 用相同輸入執行兩次，`run` 只被呼叫一次；平行執行兩次，第二次得到 `E_LOCKED`。

**CORE-FR-011｜鎖**（R1｜M）
- **處理**：以 SQLite 的 `locks` 資料表實作，記錄持有者的 PID 與心跳時間。持有者 PID 已不存在，或心跳超過 10 分鐘時，視為失效，可以接手。
- **驗收**：kill 掉持有鎖的程序，下一次執行可以取得鎖。

**CORE-FR-012｜中斷復原**（R1｜M｜NFR-002）
- **處理**：每次 CLI 啟動時，把「running 但持有者已不存在」的 Job 改成 failed，`error_code=E_INTERRUPTED`，可以重跑。
- **驗收**：渲染途中 kill，重新執行 `status` 會顯示這個 Job 失敗、可以重跑。

**CORE-FR-013｜單步重跑**（R1｜M｜US-002）
- **輸入**：`newsroom retry <job_id>`，或重新執行原本的指令。
- **處理**：建立同一個目標、同一個 kind 的新 attempt；先前成功的其他 Job 不重跑。
- **驗收**：produce 裡草稿那一步失敗，修好後重跑，補料不會再執行一次。

**CORE-FR-014｜流程串接（produce 管線）**（R1｜M）
- **處理**：`produce` 依序執行：讀 inbox 勾選 → 建題（COL）→ 補料（FET）→ 盤點素材（FET）→ 對每則 content 生成草稿（DRF）。任一步 blocked 或 failed 時，同一個 topic 的後續步驟暫停，其他 topic 繼續。
- **輸出**：每個 topic、每則 content 的結果摘要。
- **驗收**：三題中有一題補料失敗，其他兩題仍然產出草稿；摘要清楚列出失敗那一題的原因。

**CORE-FR-015｜進度與狀態查詢**（R1｜M｜US-003、NFR-010）
- **處理**：`newsroom status` 彙整 Content 與 Job 的狀態；依「需要人處理（blocked）」→「失敗」→「進行中」→「完成」排序；每一項附上下一步要執行的指令。同時更新 topic.md、content.md 的狀態受管區塊。
- **驗收**：製造 blocked（缺錄音）與 failed（API 錯誤）兩種狀況，輸出都附有正確的下一步指令。

**CORE-FR-016｜Content 狀態轉換**（R1｜M）
- **處理**：以 07 狀態圖定義的轉換表實作狀態機；所有改狀態的操作都要經過它；不合法的轉換一律拒絕。
- **驗收**：對一則 drafting 的內容執行 approve，被拒絕並說明原因。

## 5. 功能需求｜排程、通知、預算（楊舒媁）

**OPS-FR-001｜排程器**（R2｜M｜US-090）
- **處理**：用 systemd timer（WSL 開啟 systemd）或 cron 定時執行 `newsroom collect`；排程設定寫在 `config.toml`（預設每 15 分鐘）。提供 `newsroom scheduler install|uninstall|status` 產生並安裝排程設定。
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
- **處理**：每天 07:30 產生 `reports/morning/YYYY-MM-DD.md`：昨晚到現在的 S／A 級候選、進行中的內容、被阻擋的工作、今天的發表會；有設定通知時推播摘要。
- **驗收**：在固定時間觸發後，檔案內容與資料庫一致。

**OPS-FR-006｜預算控管**（R4｜M｜US-092、NFR-006）
- **處理**：每次付費呼叫前檢查 `budget` 設定（每日、每月，依整合分開）；呼叫後記錄實際花費。用到 80% 時警告，達到 100% 時回報 `E_BUDGET`，Job 設為 blocked。
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
| `jobs` | job_id PK、kind、target、status、attempt、input_fingerprint、output_paths_json、error_code、error_message、retry_after、cost_usd、時間戳 | 楊舒媁 |
| `locks` | target PK、job_id、pid、heartbeat_at | 楊舒媁 |
| `builds` | build_id PK、content_ref、format、manifest_path、status、stale | 周長曉 |
| `approvals` | approval_id PK、content_ref、build_id（文字格式可空）、text_sha256、approved_at、revoked_at | 楊舒媁 |
| `publications` | publication_id PK、content_ref、build_id、platform、status、scheduled_at、published_at、url、external_id、error | 楊舒媁 |
| `metric_snapshots` | publication_id FK、captured_at、source、metrics_json | 楊舒媁 |
| `spend` | ts、integration、job_id、cost_usd、units | 楊舒媁 |
| `heartbeats` | component、ts | 楊舒媁 |
| `events` | event_id PK、title、starts_at、ends_at、url、expected_topics | Albert |

規則：每個表都有 migration；所有時間都存 ISO 8601 含時區；JSON 欄位都要能用 07 的 schema 驗證。
