# E01-core｜核心、Skill 框架與工作區

**負責人**：Albert Chiang（工具介面、工作區）／周長曉（skill 框架、eval）／楊舒媁（執行紀錄、狀態機）

Skill 框架（格式、RESOLVER、eval）、工具介面、工作區、資料庫、執行紀錄、狀態機。所有模組都依賴這一層。

| 票 | 標題 | 負責人 | Sprint | 估點 |
|---|---|---|---|---|
| [T-001](T-001.md) | 專案骨架與 CI | Albert Chiang | S1 | 1 |
| [T-005](T-005.md) | config 載入與日誌（含金鑰遮罩） | 楊舒媁 | S1 | 1 |
| [T-006](T-006.md) | workspace 模組：路徑、受管區塊、front-matter、原子寫入 | Albert Chiang | S1 | 2 |
| [T-007](T-007.md) | SQLite schema 與 migration | 楊舒媁 | S1 | 2 |
| [T-009](T-009.md) | 執行紀錄 ledger：run_id、Job、指紋、鎖 | 楊舒媁 | S1 | 3 |
| [T-015](T-015.md) | Skill 框架：SKILL.md 格式、RESOLVER、工具介面契約 | 周長曉 | S1 | 2 |
| [T-110](T-110.md) | Skill eval 工具（newsroom eval） | 周長曉 | S1 | 1 |
| [T-008](T-008.md) | init 與 doctor（含工作區 CLAUDE.md 與 skills 連結） | Albert Chiang | S2 | 1 |
| [T-019](T-019.md) | Content 狀態機 | 楊舒媁 | S2 | 1 |
| [T-020](T-020.md) | status 工具與 recover skill | 楊舒媁 | S2 | 1 |
| [T-029](T-029.md) | produce-topic skill：建題 → 補料 → 草稿 | 楊舒媁 | S2 | 2 |
| [T-033](T-033.md) | RESOLVER 口語案例與 R1 skill 路由驗收 | 周長曉 | S2 | 1 |
| [T-018](T-018.md) | 中斷復原與單步重跑 | 楊舒媁 | S3 | 1 |

共 13 張、19 點。
