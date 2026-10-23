---
id: T-000
title: 一句話描述這張票要交付什麼
epic: E00-contract
owner: Albert Chiang        # Albert Chiang | 周長曉 | 楊舒媁 | 全體
sprint: S1
points: 1                   # 人天，1–3；超過 3 就拆票
depends_on: []              # 前置票 ID；DAG 由這個欄位產生
implements: []              # SRS 需求編號，例如 COL-FR-001
type: feature               # feature | spike | integration | chore
status: todo                # todo | doing | review | done
---

# T-000｜標題

| 負責人 | Sprint | 估點（人天） | 類型 | Epic |
|---|---|---|---|---|
| | | | | |

**前置票**：

**實作需求**：

## 背景

為什麼要做這張票、它在接力中的位置（接誰的棒、交棒給誰）。

## 要做的事

- [ ] …

## 交付物

- 檔案、模組、文件；如果是交棒物，寫明對應的介面契約（IF-xxx）

## 驗收標準

至少 3 條，用 Given／When／Then，而且至少一條是失敗情境。

1. Given …，When …，Then …

## 測試方式

## 不在這張票的範圍

---

修改 front-matter 後，執行 `python engineering/tools/build_dag.py` 更新總表與 DAG；驗證失敗（依賴不存在、循環、前置票排在後面的 Sprint、需求沒有票實作）時會以結束碼 1 結束。
