# AI Media Studio｜Content Pipeline Design（AI 科技短影音內容產線）

**文件類型：** 架構設計文件｜**版次：** v0.2｜**基準日期：** 2026-10-17
**範圍：** 繁中 AI 科技資訊帳號的「情報 → 審核選題 → 補料 → 草稿 → 製作 → 發布 → 回饋」產線｜**狀態：** 設計草案，尚未實作

> 本文件沉澱 2026-10-17 的討論結論，接續 `01_Product_Brief.md` 與 `03_Product_Requirements_Document.md`，沿用其 Project / Job / Asset / Lineage / Review Gate 用語。
> 標記：⚠ 代表未驗證，實作該環節時要先對照官方文件或實測。

---

## 1. 決策摘要

1. **個人使用，人決定內容，機器負責收集與製作。** 選題、文案、錄製、最後發布由人決定；收集、補料、草稿、合成、字幕自動化。
2. **平常走自動（模式 A），必要時人補料（模式 B）。** 補料不滿意或想自己錄 Demo 時，把素材丟進題目資料夾，Claude 依現有素材重出講稿和節奏表。
3. **資料夾＋Markdown 管理一切，不用 Notion。** 少一層 MCP，Claude Code 直接讀寫，速度快；介面用 Obsidian 開同一個資料夾。
4. **X 是一手情報主來源。** 官方網站、GitHub、HN/Reddit、發表會行事曆補足；IG/Threads 只用來做在地熱度檢查與競品監控。
5. **Remotion 是唯一的影片合成層。** 炫的動畫段落交給專門工具產出，最後統一進 Remotion 整合、上字幕、輸出（工具選擇見 §9）。
6. **每種發文都有策略。** 依熱度分級決定發幾則；三種節目各有自己的人、事、時、地、物。
7. **授權先不設限。** 個人使用、不販售，工具可直接用或小改（例如英文改繁中）。但對外發布素材仍有版權風險，見 §12。

---

## 2. 對標結論（@tenten.co 反向分析）

分析 17 則 IG 貼文（2026-09-25 至 10-16），詳細報告另存。

| 問題 | 結論 |
|---|---|
| 跟得上時事嗎？ | 從第一手公開到發文，中位數 1 天；53% 在 1 天內，71% 在 2 天內。Tesla Roadster 直播後約 2 小時就出片 |
| 只挑爆款嗎？ | 76% 的題目發文前已有熱度，第一手獨家 0 則；另有約 24% 是品牌合作 |
| 情報來源？ | ① 官方 X、直播、Newsroom、模型卡 ② X 社群熱潮 ③ 英文媒體與電子報 ④ 品牌 PR ⑤ YouTube/IG 創作者影片（當素材） |
| 做法特徵 | 大事件連發：Roadster 4 小時內 3 支（原始畫面、解說、迷因），一支 102 萬播放；新聞用輪播最快，Reels 較慢 |
| 可切入的空白 | 工程角度的技術拆解在他們的 Reels 上幾乎沒有 |

**目標**：題目在 3 個以上來源同時出現時（跨來源共振）就觸發，6 小時內出片，比對標帳號快約 18 小時。

---

## 3. 整體流程

```
① 自動收集（每 15 分鐘；發表會當天每 5 分鐘）
   X List（官方 API）＋ 你的 X 書籤 ＋ 官方 RSS ＋ changedetection.io ＋ GitHub ＋ HN/Reddit
        ↓
② 寫入 inbox/YYYY-MM-DD.md：每則附原文連結、熱度等級、建議節目類型
        ↓
③ 【人】審核、選題：把 - [ ] 改成 - [x]，寫一句備註
        ↓
④ 自動補料（分層，見 §6）：Demo 影片、圖片、原文全文、官方資料
        ↓  ←──── 模式 B：人把自己的 Demo、截圖、筆記丟進 media/ 或 notes.md
⑤ Claude 依現有素材出草稿：講稿＋節奏表＋圖卡文案＋貼文說明，附來源
        ↓
⑥ 【人】改稿、錄講解片段
        ↓
⑦ 自動製作：字幕（§8）→ 動畫段落（§9）→ Remotion 合成 / HTML 出圖卡
        ↓
⑧ 【人】最後確認 → 發布（v1 手動或原生排程）
        ↓
⑨ 自動回收成效（IG/Threads Insights）→ 每週檢討，調整門檻與策略
```

人工決策點只有四個：**選題、改稿、錄製、最後確認**。

---

## 4. 資料夾結構與檔案格式

```
newsroom/
├─ inbox/
│   └─ 2026-10-17.md          每日候選清單（自動產生，人打勾）
├─ topics/
│   └─ 2026-10-17-roadster/
│       ├─ topic.md            題目卡：來源、熱度、節目類型、發文策略、人的備註
│       ├─ notes.md            人補充的想法、要強調的觀點（模式 B）
│       ├─ sources/            原文全文（Markdown）
│       ├─ media/              自動補料＋人放的素材
│       ├─ media.md            每個素材的來源網址、作者、抓取方式
│       ├─ script.md           講稿＋節奏表（分鏡）
│       ├─ carousel.md         圖卡文案
│       ├─ caption.md          貼文說明與 hashtag
│       └─ out/                成品：影片、圖卡、字幕檔
├─ calendar/
│   └─ events.md               發表會行事曆
├─ watchlist/
│   ├─ x.md                    X 追蹤帳號（對應 X List）
│   ├─ creators.md             IG/Threads 中文創作者（競品監控）
│   ├─ sites.md                官方網站、RSS、changelog
│   └─ forums.md               Reddit、HN、PTT 看板
├─ glossary.md                 專有名詞表（字幕斷詞與校正用）
├─ voice.md                    個人口吻與人設
├─ daily.csv                   每日總表（選用，Excel 可開）
└─ data/intel.db               收集器的 SQLite（去重、首次出現時間、熱度時序）
```

### 4.1 inbox 格式

```markdown
# 2026-10-17 候選

## S 級
- [ ] **Tesla Roadster 二代發表** ｜共振 5 源｜X 官方直播、Electrek、HN 412 分｜建議：AI Today 連發
  - 首次出現 01:42｜在地：中文圈尚未報導
  - 備註：

## A 級
- [x] **Meta Muse Spark Open 816B** ｜共振 3 源｜建議：AI Today 輪播
  - 備註：做 Tool Review，我自己錄 Demo
```

### 4.2 topic.md 前置欄位

```yaml
---
id: 2026-10-17-roadster
program: ai-today          # ai-today | tool-review | ai-engineering
tier: S                    # S | A | B | evergreen
mode: auto                 # auto（模式 A）| manual（模式 B）
first_seen: 2026-10-17T01:42+08:00
burst_plan: [threads, carousel, reel-raw, reel-explain]
status: drafting           # picked | fetching | drafting | review | producing | approved | published
---
```

---

## 5. 情報來源與收集

### 5.1 來源分工

| 角色 | 來源 | 方法 | 備註 |
|---|---|---|---|
| **一手題目** | X List（100–200 個官方帳號、創辦人、研究員、評測機構） | X 官方 API 按量付費，每 15 分鐘讀一次 List | 約每月 $15–50 ⚠；最穩 |
| 一手題目 | 你的 X 書籤 | X API 讀書籤（需使用者授權） | 你滑 X 時順手標記 |
| 一手題目 | 官方 blog、新聞室 | RSS | |
| 一手題目 | 沒有 RSS 的頁面（changelog、定價頁、文件） | **changedetection.io**（自架，Docker） | 頁面變動就通知 |
| 一手題目 | GitHub 新版本 | `releases.atom` | 免費 |
| 開發者訊號 | HN、Reddit、Hugging Face | 官方 API / RSS | 免費 |
| **時間軸** | 發表會 | `calendar/events.md` | 見 §5.3 |
| **在地熱度** | IG 中文創作者 | IG 官方 `business_discovery` | 讀公開創作者帳號的貼文與互動 ⚠ 有呼叫上限 |
| 在地熱度 | Threads 關鍵字 | Threads 官方關鍵字搜尋 API | ⚠ 每 7 天約 500 次 |
| 在地熱度 | PTT、Dcard | 網頁爬取 | |

- **不自寫 IG/Threads 爬蟲**：需要登入，封號風險高；官方 API 已足夠。
- **RSSHub 的 X 路由只當備援**：要用帳號 cookie，常失效。
- Nitter 已停止服務，不採用。

### 5.2 收集器

自寫 Python 小程式＋SQLite，常駐在 WSL（systemd）或 $5 VPS：

1. 每 15 分鐘輪詢所有來源。
2. 網址正規化與去重：展開 t.co 短網址、去掉 utm 參數、arXiv id、GitHub owner/repo。
3. 聚類：同一實體（模型名、產品名）歸為同一題。
4. 記錄每個來源**首次出現的時間**。
5. 計算熱度等級（§7.1），寫入當天的 `inbox/*.md`。
6. 達 S 級時推播手機通知（ntfy）。
7. 07:30 產生早報摘要。

收集與評分都是確定性程式，不需要 LLM；只有候選標題翻成繁中時才用便宜模型。

### 5.3 發表會行事曆

```markdown
| 日期 | 台灣時間 | 活動 | 直播連結 | 預期題材 | 準備狀態 |
|---|---|---|---|---|---|
| 2026-11-xx | 02:00 | OpenAI DevDay | ... | 新模型、API | 模板已備 |
```

| 時間點 | 系統動作 |
|---|---|
| 前一天 | 提醒；預先整理背景資料、上一代規格、模板 |
| 當天 | 收集頻率加快到每 5 分鐘 |
| 結束後 | 自動把相關題目標為 S 級，進入連發模式 |

新活動從官方活動頁與官方 X 帳號的直播預告偵測，寫進 inbox 讓人確認。

---

## 6. 自動補料（分層）

yt-dlp 和 gallery-dl 靠解析網頁，平台改版就可能失效；官方 API 比較穩，但不是每個平台都能下載。所以分層，前一層失敗才往下：

| 層 | 方法 | 適用 |
|---|---|---|
| 1 | **官方 API 直接拿網址** | X（`media.fields=variants` 回傳 mp4 網址）、Reddit（影音分軌，ffmpeg 合併） |
| 2 | 網頁上的直接檔案 | 官方 blog 的 `<video>`、og:video、GitHub README 的 GIF、press kit |
| 3 | yt-dlp / gallery-dl | YouTube（官方 API 不提供下載）、IG、Threads、X 備援 |
| 4 | 自動開瀏覽器播放並錄製 | Playwright 打開頁面讓影片播放、同時錄畫面；前面全失敗時的保底，不需人操作 |
| 5 | 標記「缺素材」 | 在 `topic.md` 標示，等人補（模式 B） |

**穩定性措施**

- 每天早上用 3 則固定測試網址跑一次各層，失效就先通知。
- yt-dlp 每天自動更新。
- `media.md` 記下每個素材的來源網址、作者、抓到的層級；定期統計哪一層最常失敗。

**優先順序**：官方帳號的素材 → press kit → 自己錄的實測 → 第三方創作者的影片。

---

## 7. 發文策略

### 7.1 熱度分級與連發

| 等級 | 判斷條件 | 發文組合 |
|---|---|---|
| **S** | 預告過的大型發表會，或 6 小時內 3 個以上來源共振 | **連發 3–5 則**：① Threads 快訊（30 分鐘內）② 輪播圖卡（2 小時內）③ 原始畫面＋重點 Reel ④ 深入解說 Reel ⑤ 迷因或反應（選用） |
| **A** | 2 個來源，X 互動速度快 | 2 則：輪播＋Reel |
| **B** | 官方發佈，熱度普通 | 1 則：輪播或 Threads |
| **常青** | 工具教學、技術拆解 | 1 則 Reel，排在空檔 |

**熱度訊號權重**（由高到低）：跨來源共振 → X 互動速度（相對該帳號中位數）→ HN/Reddit 速度 → GitHub star 增速、HF 熱門 → 來源權重（官方 > 評測機構 > 媒體 > KOL）→ 在地缺口（中文圈尚未報導加分）。

### 7.2 三種節目

| | AI Today | AI Tool Review | AI Engineering |
|---|---|---|---|
| 目的 | **觸及**：追時事、帶新觀眾 | **信任**：實測、比較、適用情境 | **專業差異化**：拆解背後技術 |
| 長度 | 30–45 秒 | 45–90 秒 | 60–120 秒 |
| 人（追誰） | 官方帳號、創辦人、研究員 | 獨立開發者、實測者 | 研究員、工程師、論文作者 |
| 事（什麼題） | 新模型、新功能、產業八卦 | 新工具、大版本更新 | 背後的技術原理 |
| 時（何時發） | 越快越好，S 級連發 | 推出後 1–3 天，等實測 | 熱潮後 2–7 天，觀眾想知道「為什麼」時 |
| 地（發哪裡） | Threads → IG 輪播 → Reel | Reel 為主 | Reel＋輪播（架構圖） |
| 物（素材） | 官方 Demo（自動補料） | 自己錄的實測（多走模式 B） | 架構圖動畫、論文圖、程式碼畫面 |
| 節奏 | 鉤子 → 重點 → 數字 → 結論 | 問題 → 實測 → 優缺點 → 適合誰 | 現象 → 原理 → 拆解 → 啟發 |
| 自動化程度 | 高 | 中 | 低（人主導，Claude 協助拆解與做圖） |

**一題三波**：同一個 S 級事件，當天發 AI Today 衝流量，3 天後發 Tool Review，一週後發 AI Engineering。

### 7.3 講稿結構（AI Today 範本）

鉤子（疑問或驚嘆）→ 一句話講清楚發生什麼 → 2–3 個數字 → 背景或比較 → 保留語（未經驗證的部分要說）→ 請觀眾留言。

---

## 8. 草稿與字幕

### 8.1 草稿依素材生成

Claude 先盤點 `media/` 有哪些素材（段數、長度、畫面內容），再寫講稿和節奏表，而不是先寫稿再找素材。換掉任何素材後重跑，講稿與節奏會跟著重排。

`script.md` 的節奏表範例：

| 時間 | 畫面（上半） | 講稿 | 字幕強調 |
|---|---|---|---|
| 0–3s | `media/demo-01.mp4` 06–09s | 「Tesla 真的做出會飛的車了？」 | 會飛 |
| 3–15s | `media/official-02.mp4` | … | 0.81 秒 |

產出前檢查：數字和專有名詞都要能對到 `sources/` 的出處，否則標記讓人確認。

### 8.2 中文字幕斷詞

| 步驟 | 工具 | 說明 |
|---|---|---|
| ① 語音轉文字＋每字時間 | **Breeze-ASR-25**（聯發科） | 台灣華語與中英夾雜特別調過，直接輸出繁體 |
| ② 以講稿校正 | 講稿文字＋`glossary.md` | 字幕以講稿原文為準，ASR 只負責對時間，避免錯字上字幕 |
| ③ 斷詞 | **CKIP Transformers**（中研院）＋專有名詞表 | 繁中語料訓練，比結巴準 |
| ④ 分行 | **BudouX**（zh-hant 模型） | 不切斷詞，每行 12–14 字 |
| ⑤ 渲染 | Remotion | 逐詞跳出、關鍵詞變色、數字跳動 |

每個詞的時間 = 第一個字的開始到最後一個字的結束，因此可以做逐詞動畫。

---

## 9. 影片製作分層

原則：**各種吸睛動畫交給專門工具產出，Remotion 只做最後整合與輸出。** Remotion 是唯一合成層，上游工具可以隨時替換。完整工具目錄（約 300 個專案）見 `research/animation_tools_catalog.md`。

### 9.1 兩種接入 Remotion 的方式

| 方式 | 說明 | 適用 |
|---|---|---|
| **直接元件**（優先） | 在 Remotion 裡以 React 元件呈現，由 `useCurrentFrame()` 驅動每一格；改文案馬上預覽，不必另外算圖 | 字幕、2D 動態、轉場、圖表、R3F 3D、Lottie、Rive、程式碼動畫 |
| **外部片段** | 外部工具先算成影片，再用 `<OffthreadVideo>`（透明素材加 `transparent`）或 `<Video>` 疊上 | Blender、Manim、HyperFrames、AI 影片、去背後的主持人 |

**能做成直接元件的，就不要做成外部片段。** 外部片段每改一次都要重算。

### 9.2 分層與首選工具

| 層 | 職責 | 首選 | 備選 / 之後加入 |
|---|---|---|---|
| **L0 素材** | 補料、人錄講解、截圖、腳本與分鏡 | §6 分層補料；`shots.json` 分鏡 | openshorts（從長片截取 Demo 精華） |
| **L1 生成：2D 動態** | 動態字體、轉場、光斑、glitch、背景、圖表、數字跳動 | Remotion 官方套件（transitions、light-leaks、effects、motion-blur、shapes、paths、noise、lottie）；**remotion-bits**；**reactvideoeditor/remotion-templates**（81 個特效）；**paper-design/shaders**（背景）；**GSAP**（以 seek 對齊 frame） | react-bits、magicui、motion-primitives（要改成 frame 驅動）；diffusionstudio/lottie（Claude 生成 Lottie 圖示） |
| **L1 生成：產品 Demo 包裝** | 手機外框、游標、點擊放大、文字揭示 | **shadcn-labs/snapcn**；自寫「點擊座標 JSON＋spring 放大」 | CursorLens（OpenScreen 已封存，改用其分支）；Cap |
| **L1 生成：3D** | 架構圖、資料流、粒子、浮動手機、地球弧線、3D logo | **@remotion/three＋drei＋react-postprocessing**（從 template-three 起步，元件參考 htekdev/explainer-3d）；**three-globe**；手機樣機自寫約 100 行（GLB＋錄影貼圖） | **Blender**（blender-mcp 建場景、`blender -b` 渲染透明 PNG 序列）：繁中 3D 標題、寫實產品特寫；linework-3d（SVG logo 揭示）；TRELLIS.2 / Hunyuan3D-2（圖片轉 GLB）；spark（高斯潑濺飛越） |
| **L1 生成：說明動畫** | 抽象概念、程式碼、架構圖 | **code-hike / template-code-hike**、**shiki-magic-move**（程式碼）；**markdy-com**、**excalidraw-animate**（架構圖） | Manim（Code2Video、manim_skill、manimgx）：attention、向量空間等抽象概念；HyperFrames（HTML/GSAP 動態圖形，輸出透明片段） |
| **L1 生成：AI 影片與圖** | B-roll、開場鉤子、背景圖 | **Higgsfield MCP**（雲端，GPU 未就緒前的主力） | 確認 3090 後：ComfyUI（官方 comfy-mcp）＋ Wan2.2 / LTX-2，或 WanGP |
| **L2 加工** | 去背、2.5D 視差、放大、補幀、補光 | GPU 未就緒前：Higgsfield（remove_background、upscale_video、relight） | 確認 3090 後：BiRefNet / SAM3 / MatAnyone（去背）、DepthFlow（照片轉 2.5D）、SeedVR2 / FlashVSR（放大）、RIFE（16/24fps 補到 30fps） |
| **L3 合成** | 上下分割、字幕、轉場、片頭、配樂 | **Remotion**＋**remotion-dev/skills**；字幕：@remotion/captions＋template-tiktok，樣式參考 remotion-captioneer / remotion-captions-kit | jincheng2026/jc-remotion-skills、video-talkcraft（中文口播＋動畫逐字對時）可參考 |
| **L4 輸出** | 渲染、壓制、規格檢查 | `remotion render`（本機 16 核）→ ffmpeg faststart → ffprobe 檢查 | 雲端 Lambda（個人用量不需要） |

**不採用**：Unity、Unreal（安裝重、headless 錄製設定繁瑣，一個人做短片不划算）；Motion Canvas、Revideo（和 Remotion 重疊）；Spline 即時元件（時間軸不受 frame 控制）。

### 9.3 三種節目的工具組合

| 節目 | 主要畫面 | 用到的層 |
|---|---|---|
| AI Today | 上半官方 Demo＋下半講解；開場鉤子；數字跳動 | L0 補料 → L1 2D 動態＋Demo 包裝 → L3 |
| Tool Review | 自錄實測套手機/筆電外框、點擊放大、優缺點卡片 | L0 自錄 → L1 Demo 包裝＋3D 樣機 → L3 |
| AI Engineering | 架構圖動畫、程式碼變化、3D 資料流、概念動畫 | L1 說明動畫＋3D（R3F、Manim）→ L3 |

### 9.4 層與層之間的交接格式

| 素材 | 格式 | 備註 |
|---|---|---|
| 不透明片段 | MP4 H.264 yuv420p、CRF 12–16、**固定 30fps** | AI 影片多為 16/24fps，先補幀到 30，避免頓挫 |
| 透明片段 | **ProRes 4444 `.mov`**（首選）、WebM VP9 yuva420p、PNG RGBA 序列 | Remotion 要裝 `@mediabunny/prores` 才能用 `<Video>` 解碼 ProRes |
| 3D 模型 | GLB | gltfjsx 轉成 React 元件後可個別控制零件 |
| 向量動畫 | Lottie `.json`、Rive `.riv` | Lottie 裡的文字圖層要先轉成 shape，否則缺字 |
| 音訊 | WAV 48kHz（中間檔）→ AAC | |
| 字幕與組裝 | `captions.json`（逐詞時間）＋ `manifest.json`（每個 shot 的來源、圖層、進出點、是否透明、fps、來源授權） | Claude Code 依 `manifest.json` 組裝 |

題目資料夾內的製作檔，對應 §4 的 `media/` 與 `out/`：

```
topics/<id>/
  media/raw/      補料與自錄原檔（唯讀）
  media/gen/      L1 產出：s03_gpu-chip_wan22_v02.mp4
  media/proc/     L2 產出：s03_..._matte.mov、_rife30.mp4
  manifest.json
  out/            成品
```

命名規則：`{shot 編號}_{描述}_{工具}_{版本}.{副檔名}`。

### 9.5 輸出規格

一份設定涵蓋四個平台：**1080×1920、30fps、H.264 High、yuv420p、BT.709、AAC 48kHz 128–192k、faststart**；約 10 Mbps，60 秒約 60–90 MB。

| 平台 | 長度 | 檔案上限 | 備註 |
|---|---|---|---|
| Instagram Reels（API） | 3 秒–15 分鐘 | 300 MB | ≤25 Mbps |
| YouTube Shorts | ≤3 分鐘 | 無明確上限 | 1080p 建議 8 Mbps |
| TikTok（API） | ≤10 分鐘 | 4 GB | |
| Threads（API） | ≤5 分鐘 | 1 GB | |

**中文字幕版面**：字級 ≥48px 並加描邊（平台二次壓縮後細字會糊）；避開畫面上下約 15% 的平台介面區 ⚠。

### 9.6 中文相關注意

- Remotion 的 `createTikTokStyleCaptions` 以空白分詞，中文要先經 §8.2 的斷詞流程。
- 文字特效的亂碼字元池要換成中文字。
- 繁中字型很大，要做子集化；3D 文字用 drei `Text3D` 時只轉用到的字，或改用 troika-three-text，最穩的是 Blender 擠出。
- Manim 中文要用 `Text(..., font="Noto Sans TC")`，不能用 `Tex`。

### 9.7 GPU 的影響

**目前最大的限制是 WSL 看不到 GPU。** 沒有 GPU 時：Remotion 的 WebGL 會退回 CPU 模擬（3D 場景每格可能要數秒 ⚠）、本機 AI 影片與加工都不能跑。對策：

1. 先確認 3090 是否在這台機器（§13）。
2. 確認前：3D 只做簡單場景；AI 生成與加工全部走 Higgsfield。
3. 確認後：Remotion 加 `--gl=angle` 走 GPU，再依序加入 ComfyUI、Blender。

**Remotion 授權**：官方寫明 4 人以上團隊才需購買 Company License，個人使用免費。

---

## 10. 圖卡（輪播）

- HTML/CSS 模板＋Playwright 截圖，輸出 1080×1350。
- 字型：思源黑體 TC / Noto Sans TC；開啟避頭尾（`line-break: strict`）。
- 文字一律用程式疊上去，**AI 生圖裡不放中文字**，避免錯字；Higgsfield 只出背景和主視覺。
- 加溢版檢查：文字超出版面就退回改稿，不靠字數上限硬擋。
- 可參考：dean9703111/ig-card-generator 的檢查規則、carousel-builder 的「封面當參考圖鎖定整組風格」做法。
- 樣式和 Remotion 共用同一套色彩與字型設定。

---

## 11. 發布與回饋

| 項目 | v1 | 之後 |
|---|---|---|
| 發布 | 人工或各平台原生排程 | Upload-Post / Blotato MCP（不必自己通過 TikTok、YouTube 的 app 審核） |
| 成效回收 | IG / Threads Insights API（自有帳號 Standard Access，免審核） | 同左 |
| 回收時間點 | 發布後 1h、6h、24h、72h、7 天 | |
| 檢討 | 每週一次：調整熱度門檻、鉤子句型、專有名詞表 | |

---

## 12. 風險與注意事項

| 風險 | 說明 | 對策 |
|---|---|---|
| 素材版權 | 個人使用不販售，但發到 IG 就是公開散布，版權風險在發布那一刻 | 片段要短、標註來源、加上自己的解說觀點；優先官方素材 |
| 補料工具失效 | yt-dlp、gallery-dl 會因平台改版暫時失效 | §6 分層備援＋每日測試 |
| X API 價格與端點 | 按量計費細節與 List 端點可用性 ⚠ | 實作前先查；程式設定花費上限 |
| 常駐中斷 | WSL 閒置會被關、Windows 睡眠或更新 | systemd、關閉睡眠、心跳監控；不穩就改 VPS |
| 模仿對標品牌 | 只借形式 | 品牌、配色、口吻、人設都用自己的 |

---

## 13. 環境現況（2026-10-17 檢查）

| 項目 | 狀態 | 待辦 |
|---|---|---|
| GPU | WSL 與 Windows 端都偵測不到 NVIDIA 驅動 | 在 PowerShell 跑 `nvidia-smi`，確認 3090 是否在這台機器；確認後設定 WSL CUDA |
| ffmpeg | 未安裝 | `sudo apt install ffmpeg` |
| Docker | 已安裝，服務未啟動 | 啟用 docker-ce 或 Docker Desktop |
| systemd | 未確認 | `/etc/wsl.conf` 開啟 systemd |
| Node | v24 | — |
| CPU / RAM | 16 核心、23GB | v1 可全部用 CPU 跑（Breeze int8、Remotion 算圖） |

---

## 14. 實作順序

| 階段 | 內容 | 驗收 |
|---|---|---|
| Week 0 | 環境（§13）、建立 `newsroom/` 資料夾、申請 X API 與 Meta app；同時開始人工發片並記錄每步耗時 | 環境檢查全綠；當週人工發出第 1 則 |
| P1 | 收集器＋inbox＋熱度分級＋發表會行事曆 | 連續 72 小時不中斷；inbox 每天自動產出 |
| P2 | 補料分層＋草稿生成（講稿、節奏表、圖卡文案） | 打勾題目能自動產出完整資料夾 |
| P3 | 字幕流程＋Remotion 上下分割模板＋動畫層（§9） | 一支 60 秒 Reel 從素材到成品自動完成 |
| P4 | 圖卡模板＋發布＋成效回收 | 一則輪播與一則 Reel 走完全流程並收回 7 天數據 |
| P5 | 每週檢討、連發模式、一題三波 | S 級事件 2 小時內發出輪播 |

**原則**：每天耗時超過 15 分鐘的步驟優先自動化；實測順序和上表不同時，以數據為準。

---

## 15. Claude Code 指令

| 指令 | 做什麼 |
|---|---|
| `/inbox` | 重新整理或檢視今天的候選清單 |
| `/produce` | 處理 inbox 中所有打勾的題目：建立題目資料夾 → 補料 → 出草稿 |
| `/redraft <topic>` | 模式 B：依目前 `media/` 與 `notes.md` 重出講稿與節奏表 |
| `/reel <topic>` | 字幕 → 動畫段落 → Remotion 合成 → 規格檢查 |
| `/carousel <topic>` | 產出圖卡 |
| `/review <topic>` | 彙整預覽與檢查結果，等人最後確認 |
| `/retro` | 每週檢討成效，提出門檻與策略調整 |

---

## 16. 待決事項

1. 3090 是否在這台機器上，決定字幕與 AI 生成要用 CPU 還是 GPU。
2. X API 實際價格與 List、書籤端點的可用性 ⚠。
3. IG `business_discovery` 與 Threads 關鍵字搜尋的實際額度 ⚠。
4. 第 6 階段之後是否改為自動發布。

---

## 17. 相關研究紀錄

- @tenten.co Reels 反向分析（2026-10-17）：本文 §2 的資料來源。
- 動畫與合成工具研究（2026-10-17）：4 組 agent 搜尋提示詞生成動畫、3D、2D 動態、AI 生成與整合規格，結論見 §9，目錄見 `research/animation_tools_catalog.md`。
- 開源專案整合研究（2026-10-17）：40 多個 repo 的評估與審查。本文已依後續討論調整，主要差異如下：

| 項目 | 研究版建議 | 本文決定 | 原因 |
|---|---|---|---|
| 介面 | — / Notion | 資料夾＋Markdown＋Obsidian | 少一層 MCP，速度快 |
| 影片合成 | ffmpeg 腳本，Remotion 不採用 | Remotion 為唯一合成層 | 個人使用不受授權限制；動畫能力強 |
| 授權篩選 | 嚴格排除 AGPL / 無授權專案 | 個人使用不設限 | 不對外販售 |
| Demo 素材 | Playwright 腳本錄製 | 分層自動補料 | 每個工具寫腳本不合理 |
| 字幕斷詞 | BudouX | CKIP＋BudouX＋專有名詞表 | 繁中斷詞更準 |
