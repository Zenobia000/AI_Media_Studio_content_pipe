# AI Media Studio 內容產線｜工程文件

這個資料夾把 [05 產線設計](../05_Content_Pipeline_Design.md) 與 [06 MVP 交接](../06_Content_Pipeline_MVP_Handoff.md) 轉成可以分工執行的工程文件：需求、規格、架構、技術選型、票與接力圖。05、06、07 原檔沒有修改；兩份文件衝突時以 06 為準（[PRD 決策紀錄 D1](01_PRD.md#11-決策紀錄)）。

## 閱讀順序

| # | 文件 | 視角 | 誰一定要讀 |
|---|---|---|---|
| 1 | [01_PRD.md](01_PRD.md) | PM：做什麼、為誰、怎樣算成功、決策紀錄、待決事項 | 全體 |
| 2 | [02_SRS/](02_SRS/00_overview.md) | SA：共通規則與每個功能的輸入、處理、輸出、例外、驗收 | 全體讀 00、07；各自讀自己的模組 |
| 3 | [03_Architecture.md](03_Architecture.md) | 架構：技術堆疊、系統架構圖、軟體架構圖、資料流、ERD、ADR | 全體 |
| 4 | [04_Tech_Selection.md](04_Tech_Selection.md) | 每個環節的工具組合、GitHub 連結、實務案例、Spike | 全體；各自細讀自己的環節 |
| 5 | [05_Team_DAG_Sprint.md](05_Team_DAG_Sprint.md) | 分工、RACI、交棒點、關鍵路徑、DAG、Sprint 計畫、協作規則 | 全體 |
| 6 | [06_Branching_Strategy.md](06_Branching_Strategy.md) | main／dev／功能分支、Sprint 合併與 tag、每個 Sprint 要開的分支 | 全體 |
| 7 | [tickets/README.md](tickets/README.md) | 107 張票的總表與負載 | 每天 |

## SRS 章節

| 章節 | 內容 | 擁有者 |
|---|---|---|
| [00 總覽](02_SRS/00_overview.md) | 名詞、ID、需求編號、共通規則、錯誤碼、NFR | 全體 |
| [01 核心](02_SRS/01_core.md) | CLI、工作區、資料庫、Job、排程、通知、預算 | Albert Chiang／楊舒媁 |
| [02 收集](02_SRS/02_collect.md) | 收集器、正規化、聚類、熱度、inbox、建題 | Albert Chiang |
| [03 補料](02_SRS/03_fetch_assets.md) | 原文、分層補料、素材盤點 | Albert Chiang |
| [04 草稿](02_SRS/04_draft.md) | 事實抽取、寫稿、引用檢查、版本 | 周長曉 |
| [05 製作](02_SRS/05_production.md) | 輪播、字幕、Reel、動畫層 | 周長曉 |
| [06 發布與回饋](02_SRS/06_publish_feedback.md) | 確認、發布、成效、週報 | 楊舒媁 |
| [07 介面契約](02_SRS/07_interfaces.md) | 模組之間的 JSON Schema（交棒依據） | 全體 |

## 編號怎麼對應

```
PRD  US-xxx（使用者故事）
  └─ SRS  <模組>-FR-nnn（功能需求）、NFR-nnn、IF-nnn（介面契約）
        └─ Ticket  T-nnn（front-matter 的 implements 列出它實作的需求）
              └─ Spike  SPK-xxx（技術選型中需要實測的項目，各自是一張票）
```

`tools/build_dag.py` 會檢查每條 FR 都至少有一張票實作。

## 工具

| 指令 | 作用 |
|---|---|
| `python tools/build_dag.py` | 驗證票（依賴、循環、Sprint 順序、需求覆蓋、負載），產生 `tickets/README.md` 與 `diagrams/dag*.png／svg` |
| `python tools/draw_architecture.py` | 重新產生系統架構圖、軟體架構圖、資料流、ERD |

兩支都需要本機安裝 Graphviz（`dot`）。

## 需要主管先處理的事

1. **2026-10-30 前**：送 Reddit Data API 申請，步驟見 [runbooks/reddit-api-access.md](runbooks/reddit-api-access.md)（PRD Q5；Reddit 2026-10-31 起不再受理新的公開 API 申請）。
2. 其他決策點見 [分工與 DAG §6](05_Team_DAG_Sprint.md#6-主管要盯的決策點)。
