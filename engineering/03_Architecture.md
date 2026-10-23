# 架構｜技術堆疊、系統架構、軟體架構

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 上游 | [PRD](01_PRD.md)、[SRS](02_SRS/00_overview.md) |
| 圖檔原始碼 | `tools/draw_architecture.py` → `diagrams/*.dot／.png／.svg`（修改後重新執行即可） |

---

## 1. 架構總覽

一句話：**單機、單使用者的模組化單體。** Python 負責流程與資料，Node 負責影音與出圖；外部工具一律經過 adapter；模組之間只透過檔案與 JSON Schema 交接。

不採用：微服務、訊息佇列、帳號系統、Web 後台、雲端工作流平台。原因是只有一個使用者、一台機器，這些東西只會增加三個人的協作成本。

## 2. 技術堆疊

| 層 | 技術 | 用途 | 選型依據 |
|---|---|---|---|
| 介面 | Markdown＋Obsidian | 人讀寫 inbox、稿件、筆記 | PRD D5 |
| 入口 | Typer CLI；Claude Code slash command | 所有操作都是 CLI 指令；slash command 只是呼叫 CLI | SRS 01 §2 |
| 流程 | Python 3.11+、poetry | 協調器、領域模組 | 技術選型 §5 |
| 資料 | SQLite（WAL）＋sqlite-vec | 執行紀錄、候選、向量 | ADR-03 |
| 契約 | JSON Schema（draft 2020-12）＋Pydantic | 模組交接與設定驗證 | SRS 07 |
| 排程 | systemd timer（WSL）／cron | 收集、早報、成效回收、自我測試 | SRS 01 OPS |
| 收集 | feedparser、httpx、courlan、RapidFuzz、bge-m3 | 收集、正規化、聚類 | 技術選型 §1 |
| 補料 | trafilatura＋curl_cffi、Jina Reader、yt-dlp＋Deno、gallery-dl、Playwright | 原文、素材、錄影 | 技術選型 §1.5–1.6 |
| LLM | Anthropic SDK（Citations、structured output、prompt caching） | 事實抽取、寫稿、週報建議 | 技術選型 §2.1 |
| 字幕 | Qwen3-ForcedAligner、Breeze-ASR-25＋faster-whisper、CKIP、BudouX、pysubs2 | 對齊、比對、斷詞、分行 | 技術選型 §2.2–2.4 |
| 渲染 | Node（nvm）＋TypeScript＋Remotion（或 HyperFrames，見 ADR-04）；Playwright（輪播） | Reel、輪播 | 技術選型 §3 |
| 媒體 | ffmpeg／ffprobe | 前處理、faststart、規格檢查 | 技術選型 §3.4 |
| 發布 | Meta Graph API（IG、Threads）；聚合服務（R6） | 發布、成效 | 技術選型 §4 |
| 通知 | ntfy（＋Apprise） | 推播 | 技術選型 §1.7 |
| 容器 | Docker | changedetection.io、ntfy | 全域規範：外部服務一律用 Docker |
| 測試 | pytest、vcrpy／respx、jsonschema、Vitest（Node） | 離線測試、契約測試 | NFR-014 |

## 3. 系統架構圖（部署）

![系統架構圖](diagrams/system-architecture.png)

| 元件 | 執行位置 | 說明 |
|---|---|---|
| newsroom CLI | WSL2 | 所有流程的入口；由 Claude Code、systemd timer 或人直接呼叫 |
| render/ | WSL2（Node） | 由 CLI 以 subprocess 呼叫；只讀 manifest 指定的檔案 |
| 本機模型 | WSL2 | ASR、對齊、斷詞、嵌入；沒有 GPU 時以 CPU 執行 |
| newsroom/ 工作區 | WSL 檔案系統 | Obsidian 透過 `\\wsl$\` 路徑開啟同一個資料夾 |
| Docker 服務 | WSL2 | changedetection.io（R5）、ntfy（選用自架） |
| 外部服務 | 網路 | 都經過 adapter；每個都能單獨停用 |

**部署注意**
- WSL 要開啟 systemd（`/etc/wsl.conf`），並關閉 Windows 自動睡眠，否則排程會中斷（SRS OPS-FR-001、OPS-FR-002）。
- Obsidian 在 Windows 端，透過 `\\wsl$\` 開啟 WSL 內的工作區，比反過來（WSL 讀寫 `/mnt/c`）快。
- 金鑰放在 WSL 使用者的環境變數，或作業系統 keyring，不進工作區。

## 4. 軟體架構圖（分層與模組）

![軟體架構圖](diagrams/software-architecture.png)

**依賴規則**（違反就不能合併）

1. 依賴方向由上往下：入口 → 流程 → 領域 → core／adapter。
2. **領域模組之間不能互相 import。** collect、fetch、draft、production、publish、feedback 彼此只透過檔案（07 介面契約）與資料庫交接，這樣三個人才能各自開發。
3. 領域模組呼叫外部工具，一律經過 adapter 介面；adapter 的實作可以替換。
4. 唯一的共用程式碼在 `core`（workspace、db、config、log、schemas）。
5. 所有會花錢或花時間的工作，都包成 Step（IF-015）由協調器執行，才能統一處理冪等、鎖、重跑與預算。

### 4.1 程式碼目錄

```text
repo/
  pyproject.toml
  src/newsroom/
    cli.py                         # Typer entry; thin
    core/                          # Albert: workspace, db, config, log, schemas loader
      workspace.py  blocks.py  frontmatter.py  db.py  migrations/  config.py  log.py
    orchestrator/                  # 楊舒媁: job, lock, fingerprint, state machine, pipelines
      jobs.py  locks.py  state.py  pipelines.py  step.py
    ops/                           # 楊舒媁: scheduler, notify, budget, heartbeat
    collect/                       # Albert
      collectors/{manual,rss,github,hn,hf,newsletter,x,changedetect}.py
      normalize.py  cluster.py  score.py  inbox.py  topics.py  calendar.py
    fetch/                         # Albert
      extract.py  layers/{l1_api,l2_html,l3_dl,l4_record}.py  assets.py  selftest.py
    draft/                         # 周長曉
      llm/{base,anthropic}.py  facts.py  generate.py  check.py  versions.py  script_parser.py
      templates/programs/*.md
    production/                    # 周長曉
      build.py  preprocess.py  spec.py  carousel.py
      subtitle/{align,asr,segment,linebreak,export}.py
      reel.py
    publish/                       # 楊舒媁
      review.py  approve.py  package.py  publishers/{base,instagram,threads,aggregator}.py  storage.py
    feedback/                      # 楊舒媁
      metrics/{base,manual,instagram,threads}.py  retro.py
  render/                          # 周長曉 (Node + TypeScript)
    src/cli.ts  src/reel/  src/carousel/  src/components/  src/tokens/
  schemas/*.schema.json            # 07 介面契約（三人共同）
  tests/
    fixtures/workspace/            # IF-018
    contract/                      # schema 驗證測試
    unit/  integration/
  .claude/commands/*.md            # slash commands
```

## 5. 資料流

![資料流](diagrams/data-flow.png)

黃色橢圓是人工決策點，也是三人之間交棒時「等人」的地方：

| 交棒點 | 上游 → 下游 | 交棒物 | 契約 |
|---|---|---|---|
| H1 選題 | collect（Albert）→ 人 → 建題（Albert） | inbox 勾選 | IF-001 |
| H2 補料完成 | fetch（Albert）→ draft（周長曉） | sources/*.md、assets.json | IF-004、IF-005 |
| H3 稿件選定 | draft（周長曉）→ 人 → production（周長曉） | selected revision、script.md | IF-006、IF-007 |
| H4 成品完成 | production（周長曉）→ review（楊舒媁） | builds/<id>、manifest.json | IF-010～012 |
| H5 確認 | 人 → publish（楊舒媁） | approval | SRS 06 PUB-FR-003 |
| H6 發布 | publish → feedback（楊舒媁） | publications | IF-016、IF-017 |

## 6. 資料模型（ERD）

![ERD](diagrams/erd.png)

欄位細節見 [SRS 01 §6](02_SRS/01_core.md#6-資料表第一版)。顏色代表該資料表的負責人；寫入資料表一律透過負責人的 repository 函式，其他人只讀。

## 7. 橫切關注點

| 關注點 | 做法 | 對應 |
|---|---|---|
| 冪等 | Job 指紋（輸入雜湊）相同而且已經成功，就不重跑 | CORE-FR-010、NFR-003 |
| 並行 | 同一個物件一次只允許一個 Job（SQLite 鎖＋心跳） | CORE-FR-011 |
| 失敗復原 | 啟動時清理中斷的 Job；單步重跑 | CORE-FR-012、013 |
| 人工稿保護 | 受管區塊；草稿版本另存；select 時備份 | CORE-FR-004、DRF-FR-006 |
| 版本綁定 | manifest 記錄所有輸入雜湊；輸入改變時 Build 標為過期，確認失效 | REEL-FR-002 |
| 成本 | 每次付費呼叫前檢查預算；記錄實際花費 | OPS-FR-006 |
| 機密 | 只放環境變數或 keyring；日誌遮罩 | CORE-FR-008 |
| 可觀察 | JSONL 日誌；`newsroom status`；早報 | CORE-FR-015、OPS-FR-005 |

## 8. 架構決策紀錄（ADR）

### ADR-01｜模組化單體，不拆服務
- **決定**：一個 Python 套件加一個 Node 專案，在同一台機器執行。
- **原因**：一個使用者、一台機器；三個人的協作邊界靠模組與契約，不靠網路服務。
- **代價**：之後要搬到雲端時，需要再拆；目前沒有這個需求。

### ADR-02｜協調器：自寫 Python，不用 n8n 當主幹
- **研究證據**（技術選型 §5）：

| 面向 | n8n | 自寫 Python |
|---|---|---|
| 測試與版本控制 | workflow 是 JSON，難以對節點寫單元測試 | pytest＋git |
| 呼叫本機工具 | 2.0 起 Execute Command 節點預設關閉；在 Docker 內呼叫 WSL 上的 Node、GPU 不方便 | subprocess 直接呼叫 |
| 有狀態邏輯（去重、聚類、分層補料） | RSS Trigger 會重送舊項目，要自己寫 Code 節點；分支畫成圖後很亂 | SQLite 與程式邏輯天然適合 |
| 狀態歸屬 | n8n 自己有資料庫，會和我們的 SQLite＋Markdown 變成兩套真相 | 單一真相 |
| 審核與通知介面 | 強項（Telegram／Slack 核准幾乎零成本） | 要自己做 |

- **決定**：Python 協調器＋SQLite 是主幹。社群的完整新聞產線（例如 ai-news-brief）也是用 Python 做主幹。
- **保留彈性**：如果「手機上一鍵核准」真的成為痛點，可以加 n8n 只負責通知與核准按鈕，結果寫回同一個 SQLite，**n8n 不持有任何流程狀態**。

### ADR-03｜Markdown 是人工內容的主資料，SQLite 是執行紀錄的主資料
- **原因**：人用 Obsidian 編輯 Markdown 最自然；執行紀錄需要查詢與交易。兩邊不各存一份可編輯的資料，避免不一致（PRD D4）。
- **規則**：程式只寫受管區塊，或寫自己產生的檔案；Markdown 裡的狀態摘要只是給人看的。

### ADR-04｜影片合成層：Remotion，HyperFrames 為技術備援
- **決定**：採用 Remotion。POC 階段不考慮授權（PRD D14）。
- **隔離**：渲染器透過 IF-013 的 CLI 介面隔離，Python 端不知道底層是哪一種；SPK-PRD-1 若發現 Remotion 有技術問題（速度、中文字型），或轉商用時授權成本不划算，可以換成 HyperFrames 而不動 Python 端。

### ADR-05｜契約先行
- **決定**：Sprint 1 第一週先寫 `schemas/*.schema.json` 與 fixtures；每個模組的輸出都要通過 schema 驗證才算完成。
- **原因**：讓下游不必等上游做完，拿 fixture 就能開工（見 [分工與 DAG](05_Team_DAG_Sprint.md)）。

### ADR-06｜外部工具一律經過 adapter
- **原因**：工具汰換很快（研究時就發現 6 個封存或改名的專案）。adapter 讓「換工具」只影響一個檔案。
- **規則**：adapter 介面定義在領域模組裡（例如 `draft/llm/base.py`），實作與介面同一個資料夾；呼叫端只依賴介面。

### ADR-07｜字幕：講稿為準，用強制對齊取得時間
- **原因**：自錄旁白有講稿，對齊比 ASR 辨識更準，也不會把辨識錯字帶進字幕。ASR 只用來比對念稿差異（PRD D12）。

### ADR-08｜草稿：抽事實與寫稿分開
- **原因**：Claude API 的 Citations 與結構化輸出不能同時啟用；拆開之後，事實可以快取，數字也能用程式比對（PRD D13）。
