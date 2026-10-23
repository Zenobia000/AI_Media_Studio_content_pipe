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
