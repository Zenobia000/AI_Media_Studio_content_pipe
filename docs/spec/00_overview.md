# SRS 00｜總覽與共通規格

| 項目 | 內容 |
|---|---|
| 文件類型 | 軟體需求規格（SA 視角） |
| 版本 | v1.0｜2026-10-23 |
| 上游 | [PRD](../product/PRD.md) |
| 章節 | 00 總覽（本文）、[01 核心](01_core.md)、[02 收集](02_collect.md)、[03 補料](03_fetch_assets.md)、[04 草稿](04_draft.md)、[05 製作](05_production.md)、[06 發布與回饋](06_publish_feedback.md)、[07 介面契約](07_interfaces.md) |

本章定義所有模組都要遵守的共通規則。各模組章節只寫自己的功能需求，不重複這裡的內容。

---

## 1. 系統範圍

系統是一個單機、單使用者的內容產線，由以下部分組成：

- **Python 套件 `newsroom`**：CLI、流程協調、收集、補料、草稿、發布紀錄、成效。
- **Node 專案 `render/`**：Remotion 影片合成、輪播出圖、規格檢查。由 Python 以 subprocess 呼叫。
- **工作區 `newsroom/`**：使用者的 Markdown 與素材，以及 SQLite 資料庫。用 Obsidian 開同一個資料夾閱讀和編輯。
- **常駐服務**：排程器（systemd timer 或 cron）、選用的 Docker 服務（changedetection.io、ntfy）。

## 2. 名詞

| 名詞 | 定義 |
|---|---|
| Source | 一個外部來源項目，例如一篇文章、一則推文、一個 release。以正規化後的網址識別 |
| Candidate | inbox 裡的一則候選；可以對應一個或多個 Source（聚類後） |
| Topic | 一個被選中的事件或主題，共享來源與素材 |
| Content | Topic 底下的一則作品，有自己的節目、格式、稿件、成品與狀態 |
| Asset | 一個素材檔（官方下載、自錄、生成、衍生） |
| Revision | 一份草稿版本，存在 `drafts/<revision>/` |
| Build | 一次製作輸出，存在 `builds/<build_id>/`，綁定稿件版本與素材 |
| Approval | 人對某個 Build（或文字版本）的確認 |
| Publication | 一則作品在某一個平台的發布紀錄 |
| MetricSnapshot | 某個 Publication 在某個時間點的成效數據 |
| Job | 系統執行的一個步驟（例如補料、草稿、渲染），有自己的狀態與重試紀錄 |
| Program | 節目類型：`ai-today`、`tool-review`、`ai-engineering` |
| Format | 內容格式：`threads`、`carousel`、`reel` |
| Tier | 熱度等級：`S`、`A`、`B`、`evergreen` |

## 3. ID 規則

ID 一經建立就不再改變。改標題不會改 ID。

| 物件 | 格式 | 範例 | 產生方式 |
|---|---|---|---|
| source_id | `src_` + 正規化網址 SHA-1 的前 12 碼 | `src_3f9a1c0b7e22` | 決定性：同網址永遠得到同 ID |
| candidate_id | `cand_` + 第一個 source_id 的雜湊前 12 碼 | `cand_3f9a1c0b7e22` | 決定性 |
| topic_id | `YYYY-MM-DD-<slug>`；slug 為 ASCII、小寫、`-` 連接、≤ 40 字元；重複時加 `-2` | `2026-10-17-roadster` | 建立時決定 |
| content_id | `<format>-<nn>`，在 topic 內唯一 | `reel-01` | 建立時決定 |
| 全域內容參照 | `<topic_id>/<content_id>` | `2026-10-17-roadster/reel-01` | CLI 參數使用 |
| asset_id | `ast_` + 檔案內容 SHA-256 的前 12 碼 | `ast_a1b2c3d4e5f6` | 決定性：同檔案同 ID |
| revision | `r<nnn>`，在 content 內遞增 | `r003` | 每次生成草稿＋1 |
| build_id | `b<YYYYMMDD-HHMMSS>` | `b20261017-143012` | 製作開始時間 |
| job_id、publication_id、approval_id | ULID | `01JBX…` | 程式產生 |

## 4. 需求編號

| 前綴 | 模組 | 章節 |
|---|---|---|
| `CORE-FR-nnn` | 核心：CLI、工作區、資料庫、Job、設定 | 01 |
| `OPS-FR-nnn` | 排程、通知、預算控管 | 01 |
| `COL-FR-nnn` | 情報收集與 inbox | 02 |
| `FET-FR-nnn` | 原文擷取、補料、素材盤點 | 03 |
| `DRF-FR-nnn` | 草稿生成 | 04 |
| `CAR-FR-nnn` | 輪播 | 05 |
| `SUB-FR-nnn` | 字幕 | 05 |
| `REEL-FR-nnn` | Reel 合成、動畫、規格檢查 | 05 |
| `PUB-FR-nnn` | 確認、發布、連發 | 06 |
| `FB-FR-nnn` | 成效與回饋 | 06 |
| `IF-nnn` | 介面契約 | 07 |
| `NFR-nnn` | 非功能需求 | 00 §6 |

每條功能需求的寫法：

> **XXX-FR-001｜標題**（Release Rn｜優先 M/S/C｜對應 US-xxx）
> - **輸入**：…
> - **處理**：…
> - **輸出**：…
> - **例外**：…
> - **驗收**：可以測試的條件

## 5. 共通規則

### 5.1 工作區與資料歸屬

工作區結構以 [06 §7](../archive/06_Content_Pipeline_MVP_Handoff.md) 為準，並補上 05 的長期目錄：

```text
newsroom/
  config.toml                 # 非機密設定（來源、排程、預算、模型）
  voice.md  glossary.md
  watchlist/sites.md  watchlist/x.md  watchlist/creators.md  watchlist/forums.md
  calendar/events.md
  inbox/YYYY-MM-DD.md
  reports/weekly.md  reports/morning/YYYY-MM-DD.md
  data/intel.db               # SQLite
  logs/YYYY-MM-DD.jsonl
  topics/<topic_id>/
    topic.md  notes.md
    sources/<source_id>.md
    media/raw/  media/gen/  media/proc/
    assets.json  media.md
    contents/<content_id>/
      content.md  script.md  carousel.md  caption.md
      drafts/<revision>/
      builds/<build_id>/{shots.json,captions.json,manifest.json,out/}
      publication.md
```

| 規則 | 說明 |
|---|---|
| R-DATA-1 | Markdown 是人工內容的主資料。程式每次執行前都要重新讀檔，以檔案內容為準 |
| R-DATA-2 | SQLite 是執行紀錄的主資料（sources、candidates、jobs、approvals、publications、metrics）。Markdown 裡的狀態摘要只是給人看的，程式不把摘要當成指令 |
| R-DATA-3 | 程式**只寫入**自己產生的檔案或區塊。人工檔案用「受管區塊」標記（見 5.2），區塊外的內容永遠不動 |
| R-DATA-4 | `media/raw/` 內的檔案唯讀，衍生檔另存到 `media/proc/` |
| R-DATA-5 | 寫檔一律先寫暫存檔再 rename（原子寫入），避免中斷時留下半個檔案 |

### 5.2 受管區塊

當程式和人需要共用同一個 Markdown 檔（例如 inbox、topic.md），程式只改寫標記之間的內容：

```markdown
<!-- newsroom:begin candidates -->
…程式產生的內容…
<!-- newsroom:end candidates -->
```

- 人在受管區塊內勾選或填寫的欄位（例如 `- [x]`、`備註：`），程式重新產生時要讀回並保留。
- 標記遺失或損壞時，程式不得覆寫整個檔案。要停止並回報 `E_MANAGED_BLOCK`。

### 5.3 前置欄位（front-matter）

`topic.md`、`content.md`、`sources/*.md` 使用 YAML front-matter，欄位定義見 [07 介面契約](07_interfaces.md)。未知欄位要保留，不能刪除。

### 5.4 CLI 慣例

- 指令名稱：`newsroom <command> [args]`；Claude Code slash command 只是呼叫 CLI 的入口。
- 每個指令都支援 `--workspace <path>`（預設讀環境變數 `NEWSROOM_HOME`）、`--json`（輸出機器可讀的結果）、`--dry-run`（會寫檔或花錢的指令要支援）。
- 結束碼：`0` 成功；`1` 一般錯誤；`2` 參數錯誤；`3` 被阻擋（blocked，需要人補資料）；`4` 外部服務錯誤；`5` 預算上限。

### 5.5 錯誤碼

| 錯誤碼 | 意義 | Job 狀態 |
|---|---|---|
| `E_INPUT` | 輸入檔或參數不合法（schema 驗證失敗） | failed |
| `E_MISSING_ASSET` | 必要素材不存在（例如沒有旁白錄音） | blocked |
| `E_MISSING_SOURCE` | 原文抓不到 | blocked |
| `E_EXTERNAL` | 外部 API／工具錯誤（可重試） | failed |
| `E_RATE_LIMIT` | 外部額度用完 | failed（記錄可重試時間） |
| `E_BUDGET` | 超過設定的花費上限 | blocked |
| `E_MANAGED_BLOCK` | 受管區塊標記損壞 | failed |
| `E_LOCKED` | 同一物件已有 Job 在執行 | 不建立 Job |
| `E_STALE` | 上游已變更，輸入版本過期 | failed |
| `E_SPEC` | 成品規格檢查不通過 | failed |

錯誤訊息要用繁體中文寫給人看：說明發生什麼事、影響哪個物件、下一步怎麼做。

### 5.6 設定與機密

- 非機密設定寫在 `newsroom/config.toml`，版本控制可以收。
- 金鑰一律用環境變數（例如 `ANTHROPIC_API_KEY`、`X_BEARER_TOKEN`、`META_ACCESS_TOKEN`），不寫進任何檔案，日誌也不印出來。
- 每個外部整合在 config 裡都有 `enabled = true/false`。關掉時系統要能正常運作，退回手動流程。

### 5.7 日誌

- 一行一筆 JSON（JSONL），寫在 `logs/YYYY-MM-DD.jsonl`。
- 必要欄位：`ts`、`level`、`job_id`（可空）、`module`、`event`、`message`；外部呼叫另外記 `cost_usd`（可空）、`duration_ms`。
- 日誌不得包含金鑰與完整的 Authorization header。

### 5.8 狀態機

Content、Job、Publication 的狀態與轉換以 [07 狀態圖](08_state_machines.md) 為準。

| 物件 | 狀態 |
|---|---|
| Content | `drafting → review → producing → ready → approved → published`；Threads 可以從 review 直接到 ready |
| Job | `queued / running / succeeded / blocked / failed` |
| Publication | `pending → scheduled → published`；另有 `cancelled`、`failed`（自動發布時） |

不合法的狀態轉換一律拒絕，並回報錯誤，不能默默跳過。

## 6. 非功能需求

| ID | 類別 | 需求 | 驗收 |
|---|---|---|---|
| NFR-001 | 可靠 | 任一 Job 失敗不影響其他 Job；已成功的輸出保留 | 故意讓渲染失敗，草稿與素材檔案完好 |
| NFR-002 | 可靠 | 程式中斷（kill、斷電）後重啟，`running` 的 Job 標為可重跑，不會卡死 | kill -9 後重啟，`newsroom status` 顯示可重跑 |
| NFR-003 | 冪等 | 同樣的指令重複執行，結果相同、不重複建立物件、不重複呼叫付費 API | 連續執行兩次 produce，資料庫筆數不變、LLM 只呼叫一次 |
| NFR-004 | 資料安全 | 人工修改的內容不被覆蓋 | 修改 script.md 後執行 redraft，script.md 的內容不變，新稿出現在 drafts/ |
| NFR-005 | 可追溯 | 每個 Build 能回查到稿件版本、素材 asset_id、來源 source_id | 從 manifest.json 能追到每個 source 的網址 |
| NFR-006 | 成本 | 每個付費整合都有每日與每月上限；達到 80% 時警告，100% 時停止 | 把上限設成 0.01 美元，下一次呼叫被擋下並發出通知 |
| NFR-007 | 效能 | 收集一輪（所有來源）≤ 2 分鐘；inbox 產生 ≤ 5 秒 | 以 100 個來源量測 |
| NFR-008 | 效能 | 60 秒 Reel 在 CPU 上渲染 ≤ 10 分鐘（不含 3D 與 AI 段落） | S3 Spike 實測 |
| NFR-009 | 可替換 | 外部工具（LLM、ASR、下載器、發布服務）都經過 adapter 介面呼叫 | 換一個 LLM adapter，只需要改設定，不改流程程式 |
| NFR-010 | 可觀察 | `newsroom status` 能列出所有進行中、阻擋、失敗的工作，並附原因與重跑指令 | 人工製造各種狀態後檢查輸出 |
| NFR-011 | 安全 | 金鑰不進檔案、不進日誌、不進 git | 用 grep 掃描工作區與日誌，找不到金鑰 |
| NFR-012 | 環境 | Python 3.11+ 用 poetry 管理；Node 用 nvm；外部服務用 Docker；不在系統層安裝套件 | 在乾淨的 WSL 依 README 建置成功 |
| NFR-013 | 編碼 | 所有文字檔都用 UTF-8；字幕與圖卡都用繁體中文；不得混入簡體字 | 對成品文字跑 OpenCC 檢查，沒有簡體字 |
| NFR-014 | 測試 | 每個模組都要有可以離線執行的測試（外部服務用錄製回應或 fixture） | CI 不連網也能通過 |
| NFR-015 | 可攜 | 工作區可以整個複製到另一台機器繼續使用（路徑都用相對路徑） | 複製後執行 status 正常 |

## 7. 模組與擁有者

| 模組 | SRS 章節 | 擁有者 | 主要介面 |
|---|---|---|---|
| core（CLI、工作區、資料庫） | 01 | Albert Chiang | `newsroom` CLI、SQLite schema |
| core（Job 協調、排程、通知、預算） | 01 | 楊舒媁 | Job API、排程設定 |
| collect | 02 | Albert Chiang | Source、Candidate、inbox |
| fetch／assets | 03 | Albert Chiang | sources/*.md、assets.json |
| draft | 04 | 周長曉 | drafts/、script.md、carousel.md、caption.md |
| production（輪播、字幕、Reel） | 05 | 周長曉 | shots／captions／manifest.json、builds/ |
| publish／feedback | 06 | 楊舒媁 | approvals、publications、metric_snapshots、reports |
| 介面契約 | 07 | 三人共同，主管仲裁 | JSON Schema、fixtures |
