# SRS 03｜原文擷取、分層補料、素材盤點

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | **Albert Chiang** |
| 對應 Epic | E03 補料與素材 |
| 輸入 | topic 的 source_ids |
| 輸出契約 | [IF-004 sources](07_interfaces.md#if-004sourcessource_idmdfetch-產出)、[IF-005 assets.json](07_interfaces.md#if-005assetsjsonfetch-與人工素材盤點產出draftproduction-讀取)、media.md |
| 工具 | 見 [技術選型 §補料](../architecture/tech-selection.md) |

---

## 1. 原則

1. **一個環節不只靠一個工具。** 每一層的方法各管一種情況，前一層失敗才往下一層。
2. **抓不到不是錯誤，是缺口。** 缺口要寫清楚，讓人決定要補錄、換素材，還是改做文字或輪播。
3. **下載成功不等於可以公開使用。** 每個素材都要記錄來源與取得方式，是否使用由人在 review 時決定。
4. 原始檔放在 `media/raw/`，唯讀；衍生檔放在 `media/proc/`。

## 2. 分層補料

| 層 | 方法 | 處理的情況 | Release |
|---|---|---|---|
| L1 | 官方 API 直接取得媒體網址 | X 推文影片（API 回傳的 mp4 variants）、Threads 貼文的 media_url（待驗證）；Reddit 影片會隨 Reddit API 關閉而失效，不再投資 | R4 |
| L2 | 解析網頁上的直接檔案 | 官方 blog 的 `<video>`／`<source>`、`og:video`、`og:image`、GitHub README 的 GIF 與影片、press kit 連結 | R2 |
| L3 | 通用下載器 | YouTube、Vimeo、X 備援、嵌入的影片；圖片集 | R2 |
| L4 | 自動開瀏覽器錄影 | 互動式 Demo（canvas、WebGL、要操作才會動的頁面）。只寫一支通用腳本：開網址 → 等載入 → 慢慢捲動 → 錄 N 秒 | R5 |
| L5 | 標記缺口 | 以上都失敗；寫進 assets.json 的 gaps 與 topic.md 狀態區塊，等人補 | R1 |

R1 時只有原文擷取與 L5；人自己放素材進 `media/raw/` 後，由盤點（FET-FR-007）收進來。

## 3. 功能需求

**FET-FR-001｜原文擷取**（R1｜M｜US-020）
- **輸入**：source_id。
- **處理**：依序嘗試擷取方法（見技術選型：靜態 HTML 抽正文為主，JS 渲染頁面或抽取失敗時改用備援方法）；轉成 Markdown，保留標題、作者、發布時間、正文、圖片網址、表格；寫成 `sources/<source_id>.md`（IF-004）。
  - 推文：存推文全文、作者、時間、互動數、引用的外部連結。
  - GitHub repo：存 README 與最新 release notes。
  - arXiv：存摘要與 PDF 連結；全文擷取列為選用。
  - YouTube：存標題、說明、字幕（如果有）。
- **例外**：全部方法都失敗，或正文少於 200 字時，標為 `E_MISSING_SOURCE`（blocked），並在 notes 提示人可以把原文貼進 `sources/manual-<n>.md`。
- **驗收**：用 20 個真實網址（10 個新聞或官方 blog、5 個 GitHub、3 個推文、2 個 arXiv）測試，≥ 85% 成功擷取正文；失敗的都有清楚的原因。

**FET-FR-002｜補充官方原文**（R1｜S｜US-020）
- **處理**：如果 source 是媒體報導，從文中找出官方來源的連結（官方 blog、新聞稿、GitHub、論文），一併擷取並加入 topic 的 source_ids。
- **驗收**：給一篇引用官方 blog 的科技新聞，topic 自動多一個官方來源。

**FET-FR-003｜L2 網頁直接檔案**（R2｜M｜US-021）
- **處理**：解析 HTML 中的 `<video>`、`<source>`、`og:video`、`twitter:player:stream`、`<img>` 中的 GIF、README 中的 `.mp4`／`.webm`／`.gif` 連結；依檔案大小與尺寸篩掉 logo 與圖示（寬或高 < 400 px 的圖片略過）；下載時遵守 robots 與 rate limit，單檔上限 500 MB。
- **驗收**：給 5 個含影片或 GIF 的官方頁面與 GitHub README，都下載到正確的檔案。

**FET-FR-004｜L3 通用下載器**（R2｜M｜US-021）
- **處理**：透過下載器 adapter（見技術選型）下載影片與圖片；影片優先選 ≤ 1080p、H.264；需要 cookie 的平台從 config 指定的 cookie 檔讀取（cookie 檔不進版本控制）。
- **例外**：下載器版本過舊導致失敗時，在錯誤訊息裡提示更新指令。
- **驗收**：YouTube、X 推文影片、Vimeo 各 1 支都能下載。

**FET-FR-005｜L1 官方 API 媒體**（R4｜S）
- **處理**：X 推文透過 API 取得媒體 variants，選最高位元率的 mp4。Threads 貼文：yt-dlp 和 gallery-dl 都沒有 Threads 抽取器，改用 API 回傳的 media_url 或 og:video。
- **驗收**：同一支推文影片，L1 成功時就不會再呼叫 L3。

**FET-FR-006｜L4 通用瀏覽器錄影**（R5｜C｜US-024）
- **前置**：Spike「瀏覽器錄影畫質」的結論。
- **處理**：headless 瀏覽器開啟網址 → 等待網路閒置 → 依設定捲動（速度、停頓）→ 錄製 N 秒（預設 20 秒），輸出 1080p 30fps 的 MP4。只有在 L1～L3 都沒有取得影片、而且 topic 的格式包含 reel 時才執行。
- **輸出**：`media/raw/rec-<n>.mp4`，`origin=official`、`fetch_layer=4`，usage_note 標記「自動錄影，請確認畫面」。
- **驗收**：對 3 個互動式 Demo 頁面錄影，輸出的影片可以播放、畫面清楚、沒有黑畫面。

**FET-FR-007｜素材盤點**（R1｜M｜US-022、US-023）
- **輸入**：`media/raw/`、`media/gen/`、`media/proc/` 裡的所有檔案。
- **處理**：
  1. 對每個檔案計算 SHA-256，產生 asset_id（同檔案同 ID，搬移或改名後 ID 不變）。
  2. 用 ffprobe 讀取影片與音訊的長度、解析度、fps、是否有音軌；圖片讀尺寸。
  3. 人放進來的檔案（不在下載紀錄裡）標為 `origin=self`。
  4. 檔案被刪除時，從 assets.json 移除，但保留在資料庫並標記 deleted。
  5. 依 topic 的格式判斷缺口：reel 需要旁白錄音（R3 起）、至少一段畫面素材；carousel 需要至少一張主視覺（可以用模板代替）。
  6. 寫出 assets.json（IF-005）與給人看的 media.md。
- **驗收**：放入一段自錄影片與一個 wav，盤點後兩者都出現，origin 為 self，長度正確；刪掉其中一個再盤點，assets.json 同步更新。

**FET-FR-008｜可用片段偵測**（R3｜C）
- **處理**：對影片做場景切換偵測，把結果寫進 `usable_ranges_ms`，讓草稿生成時可以選片段；偵測失敗時整段視為可用。
- **驗收**：一段含 3 個場景的影片，偵測出 3 段。

**FET-FR-009｜media.md**（R1｜M）
- **處理**：給人看的素材摘要表：檔名、類型、長度、來源網址、取得方式（哪一層、哪個工具）、使用註記；最後列出缺口與建議（例如「缺 Demo：建議錄 30 秒操作畫面」）。
- **驗收**：每個 asset 都在表上；缺口有具體建議。

**FET-FR-010｜補料自我測試**（R2｜S｜US-025）
- **處理**：`newsroom fetch --selftest` 用 config 裡的固定測試網址（每一層至少一個），逐一執行各層，輸出每一層成功或失敗；同時檢查下載器是不是最新版本。由 OPS-FR-007 每天排程執行。
- **驗收**：故意放一個失效網址，該層顯示失敗，其他層不受影響。

**FET-FR-011｜補料統計**（R2｜C）
- **處理**：記錄每個素材是從哪一層取得的；`newsroom fetch --stats` 列出最近 30 天各層的成功率。用來決定 L4 是否值得投資。
- **驗收**：統計結果與資料庫裡的紀錄一致。

## 4. 下游會讀的欄位

| 下游 | 讀取 | 用途 |
|---|---|---|
| 草稿（周長曉） | sources/*.md 正文、assets.json（type、duration、usable_ranges、notes） | 依素材寫稿與節奏表、引用來源 |
| 製作（周長曉） | assets.json 的 path、duration、fps、transparent | 產生 shots.json、檢查裁切範圍 |
| 確認（楊舒媁） | assets.json 的 origin、source_url、usage_note | 在 review 列出素材用法的待確認項 |
