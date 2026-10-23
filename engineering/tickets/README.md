# Tickets 總表

> 這份檔案由 `python engineering/tools/build_dag.py` 產生，請勿手動修改。要改票，請改 `T-*.md` 的 front-matter 後重新執行。

共 107 張票、179 點（1 點＝1 人天；`全體` 的票每人各計一次）。

## 每人每 Sprint 負載

| 負責人 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | 合計 |
|---|---|---|---|---|---|---|---|---|
| Albert Chiang | 10 | 10 | 10 | 10 | 10 | 8 | 6 | 64 |
| 周長曉 | 9 | 10 | 10 | 10 | 9 | 10 | 9 | 67 |
| 楊舒媁 | 10 | 10 | 10 | 10 | 9 | 7 | 8 | 64 |

容量假設：每人每 Sprint 10 點；超過時標 ⚠。

## 關鍵路徑

總長 31 點：[T-001](T-001.md) → [T-002](T-002.md) → [T-007](T-007.md) → [T-010](T-010.md) → [T-011](T-011.md) → [T-021](T-021.md) → [T-022](T-022.md) → [T-024](T-024.md) → [T-057](T-057.md) → [T-062](T-062.md) → [T-064](T-064.md) → [T-066](T-066.md) → [T-067](T-067.md) → [T-093](T-093.md) → [T-098](T-098.md) → [T-109](T-109.md)

關鍵路徑上的票延誤，整個專案就會延誤；每日站會優先檢查這條路徑。

## 全部票

### S1

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-001](T-001.md) | 專案骨架與 CI | Albert Chiang | 1 | chore | — | T-002、T-005、T-006、T-012 | todo |
| [T-002](T-002.md) | 介面契約 JSON Schema（IF-001～017） | 全體 | 2 | feature | T-001 | T-003、T-007、T-013、T-047 | todo |
| [T-003](T-003.md) | Fixture 假工作區（IF-018） | 全體 | 1 | feature | T-002 | T-016 | todo |
| [T-004](T-004.md) | SPK-OPS-1 環境確認：GPU、WSL systemd、Docker、ffmpeg | 楊舒媁 | 1 | spike | — | T-014、T-046、T-097 | todo |
| [T-005](T-005.md) | config 載入與日誌（含金鑰遮罩） | 楊舒媁 | 1 | feature | T-001 | T-008、T-015 | todo |
| [T-006](T-006.md) | workspace 模組：路徑、受管區塊、front-matter、原子寫入 | Albert Chiang | 2 | feature | T-001 | T-008、T-011、T-021、T-026 | todo |
| [T-007](T-007.md) | SQLite schema 與 migration | 楊舒媁 | 2 | feature | T-002 | T-008、T-009、T-010、T-019 | todo |
| [T-009](T-009.md) | Step 介面與 Job 協調器（指紋、鎖） | 楊舒媁 | 3 | feature | T-007 | T-018、T-020、T-029、T-048、T-078 | todo |
| [T-010](T-010.md) | 網址正規化與去重 | Albert Chiang | 2 | feature | T-007 | T-011、T-035、T-060 | todo |
| [T-011](T-011.md) | add-source：手動新增候選 | Albert Chiang | 1 | feature | T-010、T-006 | T-021 | todo |
| [T-012](T-012.md) | SPK-FET-1 全文擷取方案驗證 | Albert Chiang | 1 | spike | T-001 | T-023 | todo |
| [T-013](T-013.md) | render/ Node 專案骨架與 IF-013 CLI 殼 | 周長曉 | 1 | chore | T-002 | T-014、T-042、T-047 | todo |
| [T-014](T-014.md) | SPK-PRD-1 合成層比較：Remotion 與 HyperFrames（含授權） | 周長曉 | 2 | spike | T-004、T-013 | T-042、T-067 | todo |
| [T-015](T-015.md) | LLM adapter 與成本紀錄 | 周長曉 | 1 | feature | T-005 | T-016、T-051、T-078、T-101 | todo |
| [T-016](T-016.md) | 事實抽取（facts.json） | 周長曉 | 2 | feature | T-015、T-003 | T-017 | todo |

### S2

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-008](T-008.md) | newsroom init 與 doctor | Albert Chiang | 1 | feature | T-005、T-006、T-007 | — | todo |
| [T-017](T-017.md) | 節目模板與依素材生成草稿 | 周長曉 | 3 | feature | T-016 | T-025、T-026、T-027、T-029 | todo |
| [T-019](T-019.md) | Content 狀態機 | 楊舒媁 | 1 | feature | T-007 | T-020、T-022、T-030、T-052 | todo |
| [T-020](T-020.md) | status 指令與狀態摘要 | 楊舒媁 | 1 | feature | T-009、T-019 | — | todo |
| [T-021](T-021.md) | inbox 產生與跨日規則 | Albert Chiang | 2 | feature | T-011、T-006 | T-022、T-080、T-101 | todo |
| [T-022](T-022.md) | 建題：依勾選建立 topic 與 content | Albert Chiang | 2 | feature | T-021、T-019 | T-023、T-024、T-029、T-087 | todo |
| [T-023](T-023.md) | 原文擷取與官方原文補充 | Albert Chiang | 2 | feature | T-012、T-022 | T-029 | todo |
| [T-024](T-024.md) | 素材盤點與 media.md（含 L5 缺口） | Albert Chiang | 2 | feature | T-022 | T-029、T-038、T-039、T-057、T-058 | todo |
| [T-025](T-025.md) | 引用檢查（程式比對數字與專有名詞） | 周長曉 | 2 | feature | T-017 | T-028、T-030 | todo |
| [T-026](T-026.md) | 草稿版本、select、redraft | 周長曉 | 2 | feature | T-017、T-006 | T-033、T-043、T-065 | todo |
| [T-027](T-027.md) | Threads 文字輸出（串文拆段） | 周長曉 | 1 | feature | T-017 | T-034 | todo |
| [T-029](T-029.md) | produce 管線串接 | 楊舒媁 | 2 | integration | T-009、T-022、T-023、T-024、T-017 | T-034 | todo |
| [T-030](T-030.md) | review 預覽與待確認項處理 | 楊舒媁 | 2 | feature | T-019、T-025 | T-031 | todo |
| [T-031](T-031.md) | approve 與 unapprove | 楊舒媁 | 2 | feature | T-030 | T-032、T-053、T-076 | todo |
| [T-032](T-032.md) | mark-published 與排程紀錄 | 楊舒媁 | 1 | feature | T-031 | T-034、T-050 | todo |
| [T-033](T-033.md) | Claude Code slash commands（R1） | 周長曉 | 1 | chore | T-026 | T-034 | todo |
| [T-034](T-034.md) | S2 整合驗收：一則真實 Threads 走完（R1） | 全體 | 1 | integration | T-029、T-027、T-032、T-033 | — | todo |

### S3

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-018](T-018.md) | 中斷復原與單步重跑 | 楊舒媁 | 1 | feature | T-009 | — | todo |
| [T-028](T-028.md) | SPK-DRF-1／2／3：引用準確度、口吻、成本 | 周長曉 | 1 | spike | T-025 | — | todo |
| [T-035](T-035.md) | RSS／Atom 收集器 | Albert Chiang | 2 | feature | T-010 | T-036、T-037、T-105 | todo |
| [T-036](T-036.md) | GitHub、Hacker News、Hugging Face 收集器 | Albert Chiang | 2 | feature | T-035 | T-055、T-075 | todo |
| [T-037](T-037.md) | 彙整電子報收集（拆出每則連結） | Albert Chiang | 1 | feature | T-035 | T-040、T-055 | todo |
| [T-038](T-038.md) | 補料 L2：網頁上的直接檔案 | Albert Chiang | 2 | feature | T-024 | T-055、T-056、T-073 | todo |
| [T-039](T-039.md) | 補料 L3：yt-dlp 與 gallery-dl | Albert Chiang | 2 | feature | T-024 | T-055、T-056、T-106 | todo |
| [T-041](T-041.md) | SPK-COL-2（一）Meta App 設定與 Threads 搜尋權限送審 | 楊舒媁 | 1 | spike | — | T-081、T-085、T-104 | todo |
| [T-042](T-042.md) | 輪播模板與 design tokens | 周長曉 | 2 | feature | T-013、T-014 | T-043 | todo |
| [T-043](T-043.md) | 輪播渲染與溢版檢查（含 SPK-CAR-1 驗收） | 周長曉 | 2 | feature | T-042、T-026、T-047 | T-044、T-055 | todo |
| [T-046](T-046.md) | SPK-SUB-1～4：ASR、對齊、速度、斷詞分行 | 周長曉 | 3 | spike | T-004 | T-062 | todo |
| [T-047](T-047.md) | Build 建立與 manifest（含冪等） | 周長曉 | 1 | feature | T-002、T-013 | T-043、T-066、T-076、T-077 | todo |
| [T-048](T-048.md) | 排程器：安裝與收集排程 | 楊舒媁 | 2 | feature | T-009 | T-049、T-055、T-079、T-082、T-084 | todo |
| [T-049](T-049.md) | 心跳與健康檢查 | 楊舒媁 | 1 | feature | T-048 | — | todo |
| [T-050](T-050.md) | 手填成效 | 楊舒媁 | 1 | feature | T-032 | T-051、T-083 | todo |
| [T-051](T-051.md) | 週報 retro | 楊舒媁 | 2 | feature | T-050、T-015 | T-052、T-055、T-092 | todo |
| [T-053](T-053.md) | 發布包 | 楊舒媁 | 1 | feature | T-031 | T-055 | todo |
| [T-055](T-055.md) | S3 整合驗收（R2） | 全體 | 1 | integration | T-036、T-037、T-038、T-039、T-043、T-048、T-051、T-053 | — | todo |

### S4

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-040](T-040.md) | SPK-COL-3 feed 可靠度與 AINews 重疊率 | Albert Chiang | 1 | spike | T-037 | — | todo |
| [T-044](T-044.md) | 輪播字型子集化與主視覺 | 周長曉 | 1 | feature | T-043 | — | todo |
| [T-052](T-052.md) | 每步耗時統計 | 楊舒媁 | 1 | feature | T-051、T-019 | — | todo |
| [T-056](T-056.md) | 補料自我測試與統計 | Albert Chiang | 2 | feature | T-038、T-039 | T-082 | todo |
| [T-057](T-057.md) | 素材前處理（ffmpeg 正規化） | Albert Chiang | 1 | feature | T-024 | T-062、T-066、T-096 | todo |
| [T-058](T-058.md) | 可用片段偵測 | Albert Chiang | 1 | feature | T-024 | — | todo |
| [T-059](T-059.md) | SPK-COL-1 X API 成本與 L1 媒體 | Albert Chiang | 2 | spike | — | T-072 | todo |
| [T-060](T-060.md) | SPK-COL-5 聚類門檻與推播送達 | Albert Chiang | 2 | spike | T-010 | T-074 | todo |
| [T-061](T-061.md) | Reddit 來源處理（依 PRD Q5 決定） | Albert Chiang | 1 | feature | — | — | todo |
| [T-062](T-062.md) | 字幕：強制對齊（含旁白取得） | 周長曉 | 2 | feature | T-046、T-057 | T-063、T-064 | todo |
| [T-063](T-063.md) | 字幕：念稿差異偵測 | 周長曉 | 1 | feature | T-062 | — | todo |
| [T-064](T-064.md) | 字幕：斷詞、術語合併、分行、輸出 | 周長曉 | 3 | feature | T-062 | T-066、T-070、T-100 | todo |
| [T-065](T-065.md) | 節奏表解析器 | 周長曉 | 1 | feature | T-026 | T-066 | todo |
| [T-066](T-066.md) | Reel：節奏表轉成 shots.json | 周長曉 | 2 | feature | T-064、T-065、T-047、T-057 | T-067 | todo |
| [T-076](T-076.md) | 過期判定與確認自動失效 | 楊舒媁 | 1 | feature | T-047、T-031 | T-089 | todo |
| [T-077](T-077.md) | 成品規格檢查（ffprobe） | 楊舒媁 | 2 | feature | T-047 | T-071 | todo |
| [T-078](T-078.md) | 預算控管 | 楊舒媁 | 2 | feature | T-009、T-015 | T-072、T-099、T-101 | todo |
| [T-079](T-079.md) | 通知 adapter（ntfy） | 楊舒媁 | 1 | feature | T-048 | T-080、T-082、T-103 | todo |
| [T-080](T-080.md) | 早報 | 楊舒媁 | 2 | feature | T-079、T-021 | T-107 | todo |
| [T-081](T-081.md) | SPK-FB-1 成效回收實測 | 楊舒媁 | 1 | spike | T-041 | T-083 | todo |

### S5

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-067](T-067.md) | Reel 模板 split-top-demo | 周長曉 | 3 | feature | T-014、T-066 | T-068、T-093、T-094、T-095 | todo |
| [T-068](T-068.md) | Reel 渲染與輸出 | 周長曉 | 2 | feature | T-067 | T-069、T-071、T-096、T-107 | todo |
| [T-069](T-069.md) | Reel 低解析度預覽 | 周長曉 | 1 | feature | T-068 | — | todo |
| [T-070](T-070.md) | 人工修改字幕 | 周長曉 | 1 | feature | T-064 | T-107 | todo |
| [T-071](T-071.md) | SPK-REEL-1 平台壓縮後的字幕清晰度 | 周長曉 | 1 | spike | T-068、T-077 | — | todo |
| [T-072](T-072.md) | X 收集器：List 與書籤 | Albert Chiang | 3 | feature | T-059、T-078 | T-073、T-107 | todo |
| [T-074](T-074.md) | 聚類：規則、實體、語意 | Albert Chiang | 3 | feature | T-060 | T-075 | todo |
| [T-075](T-075.md) | 熱度分級與建議組合 | Albert Chiang | 3 | feature | T-074、T-036 | T-087、T-092、T-102、T-103、T-104、T-107 | todo |
| [T-082](T-082.md) | 補料自我測試排程 | 楊舒媁 | 1 | feature | T-056、T-048、T-079 | — | todo |
| [T-083](T-083.md) | 自動成效回收（IG、Threads） | 楊舒媁 | 3 | feature | T-081、T-050 | T-107 | todo |
| [T-085](T-085.md) | SPK-PUB-1／2／3 發布方案實測 | 楊舒媁 | 3 | spike | T-041 | T-086、T-088 | todo |
| [T-086](T-086.md) | 媒體暫存公開網址 | 楊舒媁 | 1 | feature | T-085 | T-088 | todo |
| [T-107](T-107.md) | S5 整合驗收（R3＋R4） | 全體 | 1 | integration | T-068、T-070、T-075、T-072、T-083、T-080 | — | todo |

### S6

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-073](T-073.md) | 補料 L1：官方 API 媒體 | Albert Chiang | 1 | feature | T-072、T-038 | — | todo |
| [T-084](T-084.md) | 收集頻率切換（發表會） | 楊舒媁 | 1 | feature | T-048、T-102 | — | todo |
| [T-087](T-087.md) | 連發模式 | 楊舒媁 | 2 | feature | T-075、T-022 | T-091、T-108 | todo |
| [T-088](T-088.md) | Publisher adapter：IG 與 Threads 官方 API | 楊舒媁 | 3 | feature | T-085、T-086 | T-089、T-090、T-108 | todo |
| [T-093](T-093.md) | 2D 動態元件庫 | 周長曉 | 3 | feature | T-067 | T-098、T-108 | todo |
| [T-094](T-094.md) | Demo 包裝：外框、游標、點擊放大 | 周長曉 | 2 | feature | T-067 | T-108 | todo |
| [T-095](T-095.md) | 程式碼與架構圖動畫 | 周長曉 | 2 | feature | T-067 | T-108 | todo |
| [T-097](T-097.md) | SPK-REEL-2／3：3D 效能與 AI B-roll | 周長曉 | 2 | spike | T-004 | T-098、T-099 | todo |
| [T-101](T-101.md) | 候選標題翻譯 | Albert Chiang | 1 | feature | T-021、T-015、T-078 | — | todo |
| [T-102](T-102.md) | 發表會行事曆 | Albert Chiang | 2 | feature | T-075 | T-084、T-108 | todo |
| [T-103](T-103.md) | S 級通知 | Albert Chiang | 1 | feature | T-075、T-079 | — | todo |
| [T-105](T-105.md) | 網頁變動監測（含 SPK-COL-4） | Albert Chiang | 2 | feature | T-035 | T-108 | todo |
| [T-108](T-108.md) | S6 整合驗收（R5） | 全體 | 1 | integration | T-093、T-094、T-095、T-105、T-087、T-088、T-102 | — | todo |

### S7

| 票 | 標題 | 負責人 | 點 | 類型 | 前置票 | 交棒給 | 狀態 |
|---|---|---|---|---|---|---|---|
| [T-089](T-089.md) | 自動發布排程與安全閘 | 楊舒媁 | 2 | feature | T-088、T-076 | T-109 | todo |
| [T-090](T-090.md) | 聚合服務 publisher（YouTube、TikTok） | 楊舒媁 | 2 | feature | T-088 | T-109 | todo |
| [T-091](T-091.md) | 一題三波提醒 | 楊舒媁 | 1 | feature | T-087 | T-109 | todo |
| [T-092](T-092.md) | 熱度門檻與名詞表調整建議 | 楊舒媁 | 2 | feature | T-051、T-075 | T-109 | todo |
| [T-096](T-096.md) | 外部片段接入（透明片段） | 周長曉 | 1 | feature | T-068、T-057 | T-099 | todo |
| [T-098](T-098.md) | 3D 段落 | 周長曉 | 2 | feature | T-097、T-093 | T-109 | todo |
| [T-099](T-099.md) | AI 影片段落 | 周長曉 | 2 | feature | T-097、T-096、T-078 | T-109 | todo |
| [T-100](T-100.md) | TTS 旁白（含 SPK-TTS-1） | 周長曉 | 3 | feature | T-064 | T-109 | todo |
| [T-104](T-104.md) | 在地熱度檢查（Threads 搜尋、IG 競品） | Albert Chiang | 2 | feature | T-041、T-075 | T-109 | todo |
| [T-106](T-106.md) | 補料 L4：通用瀏覽器錄影（含 SPK-FET-2） | Albert Chiang | 3 | feature | T-039 | T-109 | todo |
| [T-109](T-109.md) | S7 整合驗收（R6）與交接 | 全體 | 1 | integration | T-089、T-090、T-091、T-092、T-098、T-099、T-100、T-104、T-106 | — | todo |

## Epic

- [E00-contract](E00-contract.md)
- [E01-core](E01-core.md)
- [E02-collect](E02-collect.md)
- [E03-fetch](E03-fetch.md)
- [E04-draft](E04-draft.md)
- [E05-carousel](E05-carousel.md)
- [E06-subtitle](E06-subtitle.md)
- [E07-reel](E07-reel.md)
- [E08-publish](E08-publish.md)
- [E09-feedback](E09-feedback.md)
- [E10-ops](E10-ops.md)
