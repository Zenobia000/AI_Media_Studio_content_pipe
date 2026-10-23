# 分支策略

| 項目 | 內容 |
|---|---|
| 模型 | `main`／`dev`／短命功能分支，三層 |
| 節奏 | 配合兩週一個 Sprint：功能分支每天合進 `dev`，Sprint 結束時 `dev` 合進 `main` 並打 tag |
| 狀態 | 初步方向（2026-10-23），S1 Retro 後依實際情況調整 |
| 相關 | [分工與 DAG §7 協作規則](team-dag-sprint.md#7-協作規則) |

---

## 1. 三層分支

```
main  ──●────────────────●────────────────●───────▶   每個 Sprint 一個 tag（v0.1.0、v0.2.0 …）
         \              / \              /
dev       ●──●──●──●──●    ●──●──●──●──●  ─────────▶   整合線：隨時可以跑 fixture 與測試
            \  /  \  /       \  /
feat/t-xxx   ●●    ●●         ●●                       一張票一條分支，1–3 天內合回 dev
```

| 分支 | 用途 | 誰可以合進來 | 從哪裡開 | 生命週期 |
|---|---|---|---|---|
| `main` | 每個 Sprint 驗收過的版本。主管與創作者實際用的就是這一版 | 只接受 `dev` 的 Sprint 合併 PR，以及緊急修正 | — | 永久 |
| `dev` | 整合線。所有功能分支合到這裡，CI 必須一直是綠的 | 功能分支的 PR | `main` | 永久 |
| `<type>/t-xxx-<描述>` | 一張票一條分支 | 票的負責人 | `dev` | 票的工期，1–3 天；合併後刪除 |
| `fix/<描述>` | `main` 上的緊急修正 | 任何人 | `main` | 修完合回 `main`，再同步到 `dev` |

**不設 release 分支。** 只有三個人、一台機器，Sprint 結束當天直接把 `dev` 合進 `main` 就好；多一層 release 分支只會多一次同步、多一個出錯的地方。真的需要時（例如 Sprint 結束前一天還在修整合問題），再當場開 `release/sN`。

---

## 2. 分支命名

格式：`<type>/t-<票號>-<2–4 個英文字>`，全小寫，用連字號。

| type | 用在哪種票 | 範例 |
|---|---|---|
| `feat` | feature 票；不是驗收用的 integration 票（例如 T-029 產線串接） | `feat/t-021-inbox` |
| `spike` | spike 票（只放實驗程式與 `docs/spikes/` 結論，結論合進 `dev`，實驗程式可以不合） | `spike/t-014-remotion` |
| `chore` | 骨架、設定、CI | `chore/t-001-skeleton` |
| `docs` | 只改文件；Sprint 驗收票（產出 `docs/releases/Rn.md`） | `docs/t-034-r1-acceptance` |
| `fix` | 修 bug；有票就帶票號 | `fix/t-021-crossday-dup`、`fix/inbox-crash` |
| `refactor`／`test` | 不改行為的重構、只補測試 | `test/t-010-url-cases` |

票號就是分支的主鍵：從分支名稱看得出是哪張票，從票也找得到分支。

---

## 3. 日常流程（一張票）

1. **開工**：

   ```
   git switch dev && git pull
   git switch -c feat/t-021-inbox
   ```

   在票的 front-matter 把 `status` 改成 `doing`。
2. **開發**：用 Conventional Commits 小步提交，每天至少 push 一次。
3. **開 PR 到 `dev`**：
   - PR 標題：`feat(collect): generate daily inbox [T-021]`
   - PR 描述：逐條勾選這張票的驗收標準。
   - 票的 `status` 改成 `review`。
4. **Review**：至少一位其他成員核准，而且 CI 要綠。改到 `schemas/` 的 PR 要三人都核准（見第 6 節）。
5. **合併**：用 **Squash merge** 合進 `dev`，一張票在 `dev` 上就是一個 commit。合併後刪除分支，票的 `status` 改成 `done`。

**分支活超過 3 天就有問題**：代表票切太大，或卡住了。站會上決定拆票，或先合一個用開關（config flag）關掉的版本。

**跟上 `dev`**：只有自己一個人在用的分支，用 `git rebase dev`；別人也在用的分支，改用 `git merge dev`。不要強制覆寫別人也在用的分支。

---

## 4. Sprint 節奏與分支

| 時間 | 分支上做什麼 |
|---|---|
| 第 1 天（Planning） | 確認本 Sprint 要開的分支（第 7 節）。同 Sprint 交棒的票（DAG 上標 ⚡ 的）排好合併順序 |
| 每天 | 功能分支合進 `dev`。交棒物一合進 `dev`，下游就從 `dev` 拉，不要從上游的功能分支拉 |
| 倒數第 2 天 | **`dev` 凍結**：只收修正和驗收票，新功能分支留到下個 Sprint |
| 最後一天上午（Demo） | 在 `dev` 上跑整合驗收票，產出 `docs/releases/Rn.md` |
| 最後一天 | 開 PR：`dev` → `main`，標題 `release: S1`。用 **Merge commit**（不要 squash），保留每張票的 commit。合併後在 `main` 打 tag |
| 合併後 | 沒合進去的功能分支，留到下個 Sprint 繼續；回 Planning 重估 |

**Tag**：`v0.<Sprint 編號>.0`。有對應 Release 的 Sprint，tag 訊息寫上 Release：

| Sprint | Tag | Release |
|---|---|---|
| S1 | `v0.1.0` | — |
| S2 | `v0.2.0` | R1 |
| S3 | `v0.3.0` | R2 |
| S4 | `v0.4.0` | — |
| S5 | `v0.5.0` | R3＋R4 |
| S6 | `v0.6.0` | R5 |
| S7 | `v1.0.0` | R6（交接） |

**緊急修正**：從 `main` 開 `fix/...` → PR 合回 `main` → 打修正 tag（`v0.2.1`）→ 再開 PR 把 `main` 合回 `dev`。

---

## 5. 合併方式

| 合併方向 | 方式 | 原因 |
|---|---|---|
| 功能分支 → `dev` | Squash merge | 一張票一個 commit，`dev` 的歷史就是票的清單 |
| `dev` → `main` | Merge commit | 保留 Sprint 內每張票的 commit；`main` 上的合併點就是 Sprint 的邊界 |
| `main` → `dev`（緊急修正後同步） | Merge commit | 避免改寫 `dev` 的歷史 |

Squash 時，commit 訊息用 PR 標題（Conventional Commits 格式）。GitHub 會自動在後面加 `(#N)`，**不要自己手寫**。

---

## 6. 分支保護（2026-10-23 已設定）

| 規則 | `main` | `dev` |
|---|---|---|
| 必須透過 PR 合併 | 是 | 是 |
| 至少核准人數 | 1 | 1 |
| 有新 push 時，舊的核准失效 | 是 | 是 |
| 對話（review comment）全部解決才能合併 | 是 | 是 |
| 禁止強制覆寫（force push） | 是 | 是 |
| 禁止刪除分支 | 是 | 是 |
| 必須通過 CI | T-001 的 CI 上線後加 | T-001 的 CI 上線後加 |
| 管理員可以略過 | 可以（主管緊急用，不是日常流程） | 可以 |

**預設分支是 `dev`**（2026-10-23 設定）：clone 下來就在 `dev`，開 PR 時預設合進 `dev`。

**還沒做到，靠約定的部分**：
- **`main` 只收 `dev` 和 `fix/`**：GitHub 免費方案的分支保護擋不住 PR 從哪條分支來，要靠 reviewer 把關。
- **改 `schemas/` 要三人核准**：GitHub 只能要求「CODEOWNERS 之一」核准，沒辦法要求「全部人」，所以靠 PR 描述勾選三人核准。T-001 會加上 `.github/CODEOWNERS`，讓改到 `schemas/` 的 PR 自動指派三人當 reviewer。

---

## 7. 每個 Sprint 要開的分支（初步）

由票自動對應：一張票一條分支，`…` 由負責人開分支時補上 2–4 個英文字。

**數量**：每個 Sprint 15–17 條，平均每人每週 2–3 條。

**排序**：表格依負責人排；同一個人的票，照 DAG 的依賴順序做。

**特別注意**：
- **S1 前兩條要先合**：`feat/t-002`（schema）和 `feat/t-003`（fixtures）是三人一起做的，第一週就要合進 `dev`，其他人才能拿 fixture 開工。
- **⚡ 同 Sprint 交棒**：在 [分工與 DAG §3](team-dag-sprint.md#3-接力方式) 有列。上游要先合進 `dev`，下游才從 `dev` 接。

### S0（開始前，主管）

| 分支 | 內容 |
|---|---|
| `docs/engineering-plan` | 這套工程文件。PR 合進 `dev`，S1 結束時隨 `dev` 一起進 `main` |

### S1（15 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `chore/t-001-…` | [T-001](../tickets/T-001.md) 專案骨架與 CI |
| Albert Chiang | `feat/t-006-…` | [T-006](../tickets/T-006.md) workspace 模組：路徑、受管區塊、front-matter、原子寫入 |
| Albert Chiang | `feat/t-010-…` | [T-010](../tickets/T-010.md) 網址正規化與去重 |
| Albert Chiang | `feat/t-011-…` | [T-011](../tickets/T-011.md) add-source：手動新增候選 |
| Albert Chiang | `spike/t-012-…` | [T-012](../tickets/T-012.md) SPK-FET-1 全文擷取方案驗證 |
| 周長曉 | `chore/t-013-…` | [T-013](../tickets/T-013.md) render/ Node 專案骨架與 IF-013 CLI 殼 |
| 周長曉 | `spike/t-014-…` | [T-014](../tickets/T-014.md) SPK-PRD-1 合成層驗證：Remotion（HyperFrames 對照） |
| 周長曉 | `feat/t-015-…` | [T-015](../tickets/T-015.md) LLM adapter 與成本紀錄 |
| 周長曉 | `feat/t-016-…` | [T-016](../tickets/T-016.md) 事實抽取（facts.json） |
| 楊舒媁 | `spike/t-004-…` | [T-004](../tickets/T-004.md) SPK-OPS-1 環境確認：GPU、WSL systemd、Docker、ffmpeg |
| 楊舒媁 | `feat/t-005-…` | [T-005](../tickets/T-005.md) config 載入與日誌（含金鑰遮罩） |
| 楊舒媁 | `feat/t-007-…` | [T-007](../tickets/T-007.md) SQLite schema 與 migration |
| 楊舒媁 | `feat/t-009-…` | [T-009](../tickets/T-009.md) Step 介面與 Job 協調器（指紋、鎖） |
| 全體 | `feat/t-002-…` | [T-002](../tickets/T-002.md) 介面契約 JSON Schema（IF-001～017）（改 schema，三人 review） |
| 全體 | `feat/t-003-…` | [T-003](../tickets/T-003.md) Fixture 假工作區（IF-018）（改 schema，三人 review） |

### S2（17 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `feat/t-008-…` | [T-008](../tickets/T-008.md) newsroom init 與 doctor |
| Albert Chiang | `feat/t-021-…` | [T-021](../tickets/T-021.md) inbox 產生與跨日規則 |
| Albert Chiang | `feat/t-022-…` | [T-022](../tickets/T-022.md) 建題：依勾選建立 topic 與 content |
| Albert Chiang | `feat/t-023-…` | [T-023](../tickets/T-023.md) 原文擷取與官方原文補充 |
| Albert Chiang | `feat/t-024-…` | [T-024](../tickets/T-024.md) 素材盤點與 media.md（含 L5 缺口） |
| 周長曉 | `feat/t-017-…` | [T-017](../tickets/T-017.md) 節目模板與依素材生成草稿 |
| 周長曉 | `feat/t-025-…` | [T-025](../tickets/T-025.md) 引用檢查（程式比對數字與專有名詞） |
| 周長曉 | `feat/t-026-…` | [T-026](../tickets/T-026.md) 草稿版本、select、redraft |
| 周長曉 | `feat/t-027-…` | [T-027](../tickets/T-027.md) Threads 文字輸出（串文拆段） |
| 周長曉 | `chore/t-033-…` | [T-033](../tickets/T-033.md) Claude Code slash commands（R1） |
| 楊舒媁 | `feat/t-019-…` | [T-019](../tickets/T-019.md) Content 狀態機 |
| 楊舒媁 | `feat/t-020-…` | [T-020](../tickets/T-020.md) status 指令與狀態摘要 |
| 楊舒媁 | `feat/t-029-…` | [T-029](../tickets/T-029.md) produce 管線串接 |
| 楊舒媁 | `feat/t-030-…` | [T-030](../tickets/T-030.md) review 預覽與待確認項處理 |
| 楊舒媁 | `feat/t-031-…` | [T-031](../tickets/T-031.md) approve 與 unapprove |
| 楊舒媁 | `feat/t-032-…` | [T-032](../tickets/T-032.md) mark-published 與排程紀錄 |
| 全體 | `docs/t-034-…` | [T-034](../tickets/T-034.md) S2 整合驗收：一則真實 Threads 走完（R1） |

### S3（18 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `feat/t-035-…` | [T-035](../tickets/T-035.md) RSS／Atom 收集器 |
| Albert Chiang | `feat/t-036-…` | [T-036](../tickets/T-036.md) GitHub、Hacker News、Hugging Face 收集器 |
| Albert Chiang | `feat/t-037-…` | [T-037](../tickets/T-037.md) 彙整電子報收集（拆出每則連結） |
| Albert Chiang | `feat/t-038-…` | [T-038](../tickets/T-038.md) 補料 L2：網頁上的直接檔案 |
| Albert Chiang | `feat/t-039-…` | [T-039](../tickets/T-039.md) 補料 L3：yt-dlp 與 gallery-dl |
| 周長曉 | `spike/t-028-…` | [T-028](../tickets/T-028.md) SPK-DRF-1／2／3：引用準確度、口吻、成本 |
| 周長曉 | `feat/t-042-…` | [T-042](../tickets/T-042.md) 輪播模板與 design tokens |
| 周長曉 | `feat/t-043-…` | [T-043](../tickets/T-043.md) 輪播渲染與溢版檢查（含 SPK-CAR-1 驗收） |
| 周長曉 | `spike/t-046-…` | [T-046](../tickets/T-046.md) SPK-SUB-1～4：ASR、對齊、速度、斷詞分行 |
| 周長曉 | `feat/t-047-…` | [T-047](../tickets/T-047.md) Build 建立與 manifest（含冪等） |
| 楊舒媁 | `feat/t-018-…` | [T-018](../tickets/T-018.md) 中斷復原與單步重跑 |
| 楊舒媁 | `spike/t-041-…` | [T-041](../tickets/T-041.md) SPK-COL-2（一）Meta App 設定與 Threads 搜尋權限送審 |
| 楊舒媁 | `feat/t-048-…` | [T-048](../tickets/T-048.md) 排程器：安裝與收集排程 |
| 楊舒媁 | `feat/t-049-…` | [T-049](../tickets/T-049.md) 心跳與健康檢查 |
| 楊舒媁 | `feat/t-050-…` | [T-050](../tickets/T-050.md) 手填成效 |
| 楊舒媁 | `feat/t-051-…` | [T-051](../tickets/T-051.md) 週報 retro |
| 楊舒媁 | `feat/t-053-…` | [T-053](../tickets/T-053.md) 發布包 |
| 全體 | `docs/t-055-…` | [T-055](../tickets/T-055.md) S3 整合驗收（R2） |

### S4（20 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `spike/t-040-…` | [T-040](../tickets/T-040.md) SPK-COL-3 feed 可靠度與 AINews 重疊率 |
| Albert Chiang | `feat/t-056-…` | [T-056](../tickets/T-056.md) 補料自我測試與統計 |
| Albert Chiang | `feat/t-057-…` | [T-057](../tickets/T-057.md) 素材前處理（ffmpeg 正規化） |
| Albert Chiang | `feat/t-058-…` | [T-058](../tickets/T-058.md) 可用片段偵測 |
| Albert Chiang | `spike/t-059-…` | [T-059](../tickets/T-059.md) SPK-COL-1 X API 成本與 L1 媒體 |
| Albert Chiang | `spike/t-060-…` | [T-060](../tickets/T-060.md) SPK-COL-5 聚類門檻與推播送達 |
| Albert Chiang | `feat/t-061-…` | [T-061](../tickets/T-061.md) Reddit 來源處理（依 PRD Q5 決定） |
| 周長曉 | `feat/t-044-…` | [T-044](../tickets/T-044.md) 輪播字型子集化與主視覺 |
| 周長曉 | `feat/t-062-…` | [T-062](../tickets/T-062.md) 字幕：強制對齊（含旁白取得） |
| 周長曉 | `feat/t-063-…` | [T-063](../tickets/T-063.md) 字幕：念稿差異偵測 |
| 周長曉 | `feat/t-064-…` | [T-064](../tickets/T-064.md) 字幕：斷詞、術語合併、分行、輸出 |
| 周長曉 | `feat/t-065-…` | [T-065](../tickets/T-065.md) 節奏表解析器 |
| 周長曉 | `feat/t-066-…` | [T-066](../tickets/T-066.md) Reel：節奏表轉成 shots.json |
| 楊舒媁 | `feat/t-052-…` | [T-052](../tickets/T-052.md) 每步耗時統計 |
| 楊舒媁 | `feat/t-076-…` | [T-076](../tickets/T-076.md) 過期判定與確認自動失效 |
| 楊舒媁 | `feat/t-077-…` | [T-077](../tickets/T-077.md) 成品規格檢查（ffprobe） |
| 楊舒媁 | `feat/t-078-…` | [T-078](../tickets/T-078.md) 預算控管 |
| 楊舒媁 | `feat/t-079-…` | [T-079](../tickets/T-079.md) 通知 adapter（ntfy） |
| 楊舒媁 | `feat/t-080-…` | [T-080](../tickets/T-080.md) 早報 |
| 楊舒媁 | `spike/t-081-…` | [T-081](../tickets/T-081.md) SPK-FB-1 成效回收實測 |

### S5（13 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `feat/t-072-…` | [T-072](../tickets/T-072.md) X 收集器：List 與書籤 |
| Albert Chiang | `feat/t-074-…` | [T-074](../tickets/T-074.md) 聚類：規則、實體、語意 |
| Albert Chiang | `feat/t-075-…` | [T-075](../tickets/T-075.md) 熱度分級與建議組合 |
| 周長曉 | `feat/t-067-…` | [T-067](../tickets/T-067.md) Reel 模板 split-top-demo |
| 周長曉 | `feat/t-068-…` | [T-068](../tickets/T-068.md) Reel 渲染與輸出 |
| 周長曉 | `feat/t-069-…` | [T-069](../tickets/T-069.md) Reel 低解析度預覽 |
| 周長曉 | `feat/t-070-…` | [T-070](../tickets/T-070.md) 人工修改字幕 |
| 周長曉 | `spike/t-071-…` | [T-071](../tickets/T-071.md) SPK-REEL-1 平台壓縮後的字幕清晰度 |
| 楊舒媁 | `feat/t-082-…` | [T-082](../tickets/T-082.md) 補料自我測試排程 |
| 楊舒媁 | `feat/t-083-…` | [T-083](../tickets/T-083.md) 自動成效回收（IG、Threads） |
| 楊舒媁 | `spike/t-085-…` | [T-085](../tickets/T-085.md) SPK-PUB-1／2／3 發布方案實測 |
| 楊舒媁 | `feat/t-086-…` | [T-086](../tickets/T-086.md) 媒體暫存公開網址 |
| 全體 | `docs/t-107-…` | [T-107](../tickets/T-107.md) S5 整合驗收（R3＋R4） |

### S6（13 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `feat/t-073-…` | [T-073](../tickets/T-073.md) 補料 L1：官方 API 媒體 |
| Albert Chiang | `feat/t-101-…` | [T-101](../tickets/T-101.md) 候選標題翻譯 |
| Albert Chiang | `feat/t-102-…` | [T-102](../tickets/T-102.md) 發表會行事曆 |
| Albert Chiang | `feat/t-103-…` | [T-103](../tickets/T-103.md) S 級通知 |
| Albert Chiang | `feat/t-105-…` | [T-105](../tickets/T-105.md) 網頁變動監測（含 SPK-COL-4） |
| 周長曉 | `feat/t-093-…` | [T-093](../tickets/T-093.md) 2D 動態元件庫 |
| 周長曉 | `feat/t-094-…` | [T-094](../tickets/T-094.md) Demo 包裝：外框、游標、點擊放大 |
| 周長曉 | `feat/t-095-…` | [T-095](../tickets/T-095.md) 程式碼與架構圖動畫 |
| 周長曉 | `spike/t-097-…` | [T-097](../tickets/T-097.md) SPK-REEL-2／3：3D 效能與 AI B-roll |
| 楊舒媁 | `feat/t-084-…` | [T-084](../tickets/T-084.md) 收集頻率切換（發表會） |
| 楊舒媁 | `feat/t-087-…` | [T-087](../tickets/T-087.md) 連發模式 |
| 楊舒媁 | `feat/t-088-…` | [T-088](../tickets/T-088.md) Publisher adapter：IG 與 Threads 官方 API |
| 全體 | `docs/t-108-…` | [T-108](../tickets/T-108.md) S6 整合驗收（R5） |

### S7（11 條）

| 負責人 | 分支 | 票 |
|---|---|---|
| Albert Chiang | `feat/t-104-…` | [T-104](../tickets/T-104.md) 在地熱度檢查（Threads 搜尋、IG 競品） |
| Albert Chiang | `feat/t-106-…` | [T-106](../tickets/T-106.md) 補料 L4：通用瀏覽器錄影（含 SPK-FET-2） |
| 周長曉 | `feat/t-096-…` | [T-096](../tickets/T-096.md) 外部片段接入（透明片段） |
| 周長曉 | `feat/t-098-…` | [T-098](../tickets/T-098.md) 3D 段落 |
| 周長曉 | `feat/t-099-…` | [T-099](../tickets/T-099.md) AI 影片段落 |
| 周長曉 | `feat/t-100-…` | [T-100](../tickets/T-100.md) TTS 旁白（含 SPK-TTS-1） |
| 楊舒媁 | `feat/t-089-…` | [T-089](../tickets/T-089.md) 自動發布排程與安全閘 |
| 楊舒媁 | `feat/t-090-…` | [T-090](../tickets/T-090.md) 聚合服務 publisher（YouTube、TikTok） |
| 楊舒媁 | `feat/t-091-…` | [T-091](../tickets/T-091.md) 一題三波提醒 |
| 楊舒媁 | `feat/t-092-…` | [T-092](../tickets/T-092.md) 熱度門檻與名詞表調整建議 |
| 全體 | `docs/t-109-…` | [T-109](../tickets/T-109.md) S7 整合驗收（R6）與交接 |

---

分支清單是從票的 front-matter 產生的。票的 Sprint 或負責人改了，以 [tickets/README.md](../tickets/README.md) 為準。
