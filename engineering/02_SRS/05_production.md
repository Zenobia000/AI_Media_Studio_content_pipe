# SRS 05｜製作：輪播、字幕、Reel、動畫層

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | **周長曉** |
| 對應 Epic | E05 輪播、E06 字幕、E07 Reel |
| 輸入契約 | script.md（IF-007）、carousel.md（IF-008）、assets.json（IF-005）、glossary.md |
| 輸出契約 | [IF-010 shots.json](07_interfaces.md#if-010shotsjsonproduction-內部轉換器產出--渲染器讀取)、[IF-011 captions.json](07_interfaces.md#if-011captionsjson字幕流程產出--渲染器讀取)、[IF-012 manifest.json](07_interfaces.md#if-012manifestjson每次-build-一份綁定所有輸入)、`builds/<id>/out/` |
| 工具 | 見 [技術選型 §製作](../04_Tech_Selection.md) |

---

## 1. 結構

```
Python（newsroom.production）                       Node（render/）
  build 轉換器：讀稿件與素材 → shots/captions/manifest ─→ cli.js reel|carousel|check
  字幕流程：對齊、斷詞、分行 → captions.json               Remotion 組件／HTML 模板
  前處理：ffmpeg 正規化素材（30fps、yuv420p）             輸出 out/
  後處理：ffprobe 規格檢查、faststart          ←───────── 回傳 JSON 結果（IF-013）
```

**原則**
- 渲染器只讀固定的輸入檔，不呼叫 LLM、不臨時改稿。
- 任何輸入有問題，都在轉換器階段擋下（`E_INPUT`），不交給渲染器去猜。
- 每次製作都是一個新的 Build；舊 Build 保留，發布紀錄指向它當時的 Build。

## 2. 共通：Build 與重製判定

**REEL-FR-001｜Build 建立**（R2｜M）
- **處理**：建立 `builds/<build_id>/`，寫入 manifest.json，記錄所有輸入檔與素材的雜湊；Job 的 fingerprint 就是這些雜湊的組合。輸入完全相同時，不重新渲染，直接回傳既有 Build。
- **驗收**：沒有任何修改，再執行一次 reel，不會產生新的 Build。

**REEL-FR-002｜過期判定**（R3｜M｜US-062）
- **處理**：每次執行 `status` 或 `review` 時，比對每個 Build 的 manifest 輸入雜湊與現況；不一致就標為 stale；已確認（approved）的 Build 變成 stale 時，撤銷確認（Content 回到 review），並告訴人是哪一個輸入改了。
- **驗收**：確認後修改 script.md 的一個字，status 顯示「需要重製：script.md 已變更」，Content 狀態回到 review。

**REEL-FR-003｜素材前處理**（R3｜M）
- **處理**：用 ffmpeg 把影片素材正規化成固定 30fps、yuv420p、H.264；非 30fps 的 AI 片段要補幀或轉換；輸出到 `media/proc/`，以原始檔雜湊命名，處理過的不重做。
- **驗收**：放入一段 24fps 的素材，處理後是 30fps CFR，原始檔不變。

## 3. 輪播（E05）

**CAR-FR-001｜輪播模板**（R2｜M｜US-040）
- **處理**：HTML／CSS 模板放在 `render/templates/carousel/<name>/`；第一個模板 `news-default` 包含封面、重點列表、數字卡、引用卡、結尾卡五種版型。字型使用思源黑體 TC／Noto Sans TC，開啟避頭尾（`line-break: strict`）；色彩與字型設定和 Reel 共用一份 design tokens。
- **驗收**：5 種版型各渲染一張，中文沒有缺字、行首沒有標點。

**CAR-FR-002｜輪播渲染**（R2｜M｜US-040）
- **輸入**：carousel.md（IF-008）。
- **處理**：解析卡片 → 套模板 → 以 headless 瀏覽器截圖，輸出 1080×1350 JPEG（IG API 只接受 JPEG）到 `out/01.jpg`…；同時輸出一張總覽圖 `out/contact-sheet.jpg` 方便預覽。
- **限制**：最多 20 張；第一張決定整組比例，必須是 4:5。
- **驗收**：一份 8 張的 carousel.md 輸出 8 張 1080×1350 JPEG 與一張總覽圖。

**CAR-FR-003｜溢版檢查**（R2｜M｜US-041）
- **處理**：渲染時檢查每個文字框：`scrollHeight > clientHeight`，或文字邊界超出安全區，就判定為溢版；溢版時整個 Build 標為 failed（`E_SPEC`），訊息指出是第幾張、哪個欄位、超出多少行。
- **驗收**：準備 20 組真實文案（含刻意溢版的），溢版偵測召回率 100%。

**CAR-FR-004｜字型子集化**（R2｜S）
- **處理**：依實際用到的字做字型子集化，降低記憶體與渲染時間；子集化結果依字集雜湊快取。
- **驗收**：子集字型 ≤ 1 MB；每張渲染 ≤ 1 秒。

**CAR-FR-005｜主視覺**（R2｜S）
- **處理**：封面可以使用 assets 裡的圖片，或由 AI 生成背景（只生成背景與主視覺，**不在 AI 圖裡放中文字**，文字一律由模板疊上去）。
- **驗收**：使用 AI 背景時，圖上的中文字都來自模板文字層。

## 4. 字幕（E06）

字幕文字以講稿為準；時間以實際錄音為準。

```
旁白錄音（wav）＋講稿
 ① 強制對齊：把講稿逐字對到錄音 → 每字時間
 ② ASR 比對：辨識錄音內容，和講稿比對，找出「念的和稿子不一樣」的段落
 ③ 斷詞：繁中斷詞 → 依 glossary 把英文術語、產品名、數字加單位強制合成一個詞
 ④ 分行：找出不會切斷詞的換行點 → 套用每行字數（預設 12–14 字）與每頁行數規則
 ⑤ 輸出 captions.json（IF-011）＋ SRT（附帶，給平台上傳或人工檢查）
```

**SUB-FR-001｜旁白錄音前處理**（R3｜M｜US-050）
- **處理**：找出 content 的旁白錄音（assets 中 `type=audio`、檔名或 notes 指定）；轉成 48 kHz WAV；做響度正規化（目標 −14 LUFS，設定可改）；去除頭尾靜音。
- **例外**：找不到錄音時 `E_MISSING_ASSET`（blocked），提示「請錄製旁白並放進 media/raw/」。
- **驗收**：沒有錄音時 Job 是 blocked，而不是 failed。

**SUB-FR-002｜強制對齊**（R3｜M｜US-050）
- **輸入**：處理後的錄音、講稿文字（去掉 Markdown 標記）。
- **處理**：透過 aligner adapter 取得每字的開始與結束時間；錄音超過模型單段上限時，先依靜音切段再對齊；詞的時間 = 第一個字的開始到最後一個字的結束。
- **驗收**：用 3 段人工標註的錄音（約 200 字）量測，平均誤差 ≤ 80 ms，超過 200 ms 的字 ≤ 3%（對應 SPK-SUB-2）。

**SUB-FR-003｜念稿差異偵測**（R3｜S｜US-051）
- **處理**：用 ASR 辨識錄音，開啟 VAD 避免靜音段出現幻聽句子，再和講稿做逐字比對；差異超過門檻的句子列入 review 的待確認項（例如「第 3 句：稿子是『0.81 秒』，錄音聽起來是『0.18 秒』」）。
- **驗收**：故意念錯 2 處，兩處都被列出。

**SUB-FR-004｜斷詞與術語合併**（R3｜M｜US-051）
- **處理**：繁中斷詞後，依 glossary 用最長匹配，把英文術語、產品名（例如「GPT-5」「Claude Opus」）、數字加單位（「3.5 倍」「0.81 秒」）強制合成一個詞。講稿本來就是繁體時，不得再做簡繁轉換。
- **驗收**：30 句測試講稿，術語被切斷 = 0。

**SUB-FR-005｜分行與分頁**（R3｜M）
- **處理**：取得候選換行點後，依規則選擇：每行 ≤ 14 個全形字（英數字算半個）、每頁最多 2 行、不讓單一個字自成一行、句末標點不放行首；強調詞依節奏表的「強調」欄標記。
- **驗收**：30 句測試，斷錯詞或出現孤字的比例 ≤ 5%。

**SUB-FR-006｜captions.json 與 SRT 輸出**（R3｜M）
- **處理**：依 IF-011 輸出；同時輸出 SRT（以行為單位）。
- **驗收**：captions.json 通過 schema 驗證；SRT 能被播放器正確載入。

**SUB-FR-007｜人工修改字幕**（R3｜S｜US-052）
- **處理**：提供 `captions.edit.json`（或 SRT）讓人修改文字與時間；重新產生字幕時，如果錄音與講稿的雜湊沒變，就保留人工修改（`manual_edits=true`）；只要其中一個變了，就提示人工修改可能失效，請人確認。
- **驗收**：修改一行字幕後重新製作，修改仍在。

**SUB-FR-008｜TTS 旁白（選用）**（R6｜C）
- **處理**：創作者不錄音時，可以用 TTS 產生旁白，讀音依 glossary 的「寫法 → 念法」對照修正；產生的音檔一樣走 ①～⑤，不依賴 TTS 自己給的時間。只使用創作者自己的聲音，或明確授權的聲音。
- **驗收**：TTS 旁白可以完整跑完字幕流程。

## 5. Reel（E07）

**REEL-FR-010｜節奏表轉成 shots.json**（R3｜M｜US-060）
- **輸入**：script.md（用 DRF-FR-009 的解析器）、captions.json、assets.json。
- **處理**：
  1. 用字幕時間決定每個 shot 的實際起訖：以 shot 講稿的第一個字和最後一個字在 captions 中的時間為準（不使用節奏表的預估時間）。
  2. 素材路徑換成相對於 build 資料夾的路徑；影片素材使用前處理後的版本。
  3. 檢查裁切範圍不超過素材長度、模板名稱存在、素材檔存在。
- **輸出**：shots.json（IF-010）。
- **驗收**：用 fixture 產生的 shots.json 通過 schema；故意把裁切設得比素材長，回報 `E_INPUT` 並指出是哪個 shot。

**REEL-FR-011｜Reel 模板（9:16）**（R3｜M｜US-060）
- **處理**：第一個模板 `split-top-demo`：上半部是素材（官方 Demo 或模板畫面），下半部是講解字幕區；包含開場鉤子文字、逐詞高亮字幕（關鍵詞變色）、數字卡模板、結尾卡；字幕字級 ≥ 48 px 並加描邊，避開畫面上下約 15% 的平台介面區。中文字幕要自己處理分頁，不能直接使用以空白分詞的現成函式。
- **驗收**：用 fixture 渲染一支 45 秒的 Reel；逐格檢查 10 個詞，高亮與聲音的落差 ≤ 2 格（30fps 約 67 ms）；沒有缺字、第一格字型沒有閃爍。

**REEL-FR-012｜渲染**（R3｜M｜US-060）
- **處理**：Python 以 IF-013 呼叫 Node 渲染；輸出規格：1080×1920、30fps CFR、H.264 High、yuv420p、BT.709、AAC 48 kHz 128–192k；完成後用 ffmpeg 做 faststart（moov 前移）。進度回報給 Job。
- **例外**：渲染失敗時保留 shots 與 captions，可以只重跑渲染。
- **驗收**：60 秒 Reel 在 CPU 上 ≤ 10 分鐘（NFR-008）；輸出可以在手機上播放。

**REEL-FR-013｜規格檢查**（R3｜M｜US-061）
- **處理**：用 ffprobe 檢查：codec＝h264、pix_fmt＝yuv420p、30/1 fps、color_primaries＝bt709、音訊 48000 Hz、moov 在前、長度在 3 秒到該平台上限之間、檔案 ≤ 300 MB；任何一項不符就 `E_SPEC`。
- **驗收**：故意輸出 25fps 的檔案，檢查失敗並指出 fps 不符。

**REEL-FR-014｜預覽**（R3｜S）
- **處理**：`--preview` 輸出低解析度（540×960）版本與每 5 秒一張的縮圖總覽，供 review 快速檢視。
- **驗收**：預覽版渲染時間 ≤ 正式版的 1/3。

**REEL-FR-020｜2D 動態元件庫**（R5｜S｜US-063）
- **處理**：建立以 frame 驅動的元件：數字跳動、關鍵詞強調、轉場（至少 3 種）、光斑與雜訊背景、Lottie 圖示；元件 props 可以從 shots.json 的 `template.props` 設定。
- **驗收**：每個元件都有一支 5 秒的範例渲染，並放在元件目錄的 README。

**REEL-FR-021｜Demo 包裝**（R5｜S｜US-064）
- **處理**：自錄的螢幕錄影可以套上裝置外框（手機、筆電、瀏覽器）、游標軌跡、點擊放大；點擊座標寫在 `media/raw/<rec>.clicks.json`（時間、x、y），以 spring 動畫放大。
- **驗收**：一段 20 秒的錄影加上 3 個點擊座標，輸出有外框與 3 次放大。

**REEL-FR-022｜程式碼與架構圖動畫**（R5｜S｜US-065）
- **處理**：程式碼片段之間的轉換動畫（逐 token 移動）；架構圖逐步顯示（節點、箭頭依序出現）。輸入是 Markdown 程式碼區塊與簡單的圖描述（JSON 或 Mermaid 子集）。
- **驗收**：兩段程式碼之間的轉換動畫正確；6 個節點的架構圖依序出現。

**REEL-FR-023｜外部片段接入**（R5｜S）
- **處理**：外部工具（Manim、Blender、AI 影片、HTML 動畫工具）產出的片段放進 `media/gen/`，命名規則 `{shot}_{描述}_{工具}_{版本}.{ext}`；不透明片段用 MP4，透明片段用 ProRes 4444 `.mov` 或 WebM VP9（yuva420p）；shots.json 以 `transparent: true` 疊加。
- **驗收**：一段透明背景的片段正確疊在畫面上，邊緣沒有黑邊。

**REEL-FR-024｜3D 段落**（R6｜C｜US-066）
- **前置**：GPU 確認與 3D 渲染 Spike 的結論。
- **處理**：使用 React Three Fiber 製作架構圖、資料流、浮動手機等 3D 元件；沒有 GPU 時只允許簡單場景。
- **驗收**：10 秒 3D 場景每格 ≤ 0.5 秒（以 Spike 結論的環境量測）。

**REEL-FR-025｜AI 影片段落**（R6｜C｜US-066）
- **處理**：透過 AI 影片 adapter（第一個實作是雲端服務，GPU 確認後可加本機方案）產生 B-roll 或開場鉤子；結果放進 media/gen，origin=generated；納入預算控管；畫面中不得出現 AI 生成的中文字。
- **驗收**：產生 1 段 5 秒片段，自動補幀到 30fps，可以放進 Reel。

## 6. 驗證實驗（Spike）

| Spike | 內容 | 過關條件 |
|---|---|---|
| SPK-PRD-1 合成層 | 同一支 60 秒 AI Today 腳本，用 Remotion 與 HyperFrames 各做一版（POC 不比較授權，PRD D14） | 60 秒渲染 ≤ 3 分鐘（CPU）；中文無缺字；改文案後 ≤ 1 次重渲染就正確 |
| SPK-CAR-1 輪播渲染 | 20 組真實繁中文案，用 headless 瀏覽器渲染 | 溢版召回率 100%；每張 ≤ 1 秒；字型 ≤ 1 MB；行首無標點 |
| SPK-SUB-1 ASR 辨識率 | 10 段自錄 Reel 音檔，比較 2–3 個 ASR 方案 | CER ≤ 8%；術語錯誤 ≤ 2 個／段；輸出為繁體 |
| SPK-SUB-2 對齊準度 | 3 段人工標註錄音，比較 2–3 個對齊方案 | 平均誤差 ≤ 80 ms；> 200 ms 的字 ≤ 3% |
| SPK-SUB-3 速度 | 16 核 CPU（與 GPU，若有）跑完整字幕流程 | 60 秒錄音 ≤ 120 秒 |
| SPK-SUB-4 斷詞分行 | 30 句講稿比較斷詞方案＋分行規則 | 術語被切斷 = 0；斷詞 F1 ≥ 0.95；斷錯或孤字 ≤ 5% |
| SPK-REEL-1 平台壓縮 | 3 支成品上傳到 IG／Threads 私人測試帳號 | ffprobe 斷言全過；平台壓縮後字幕仍清楚 |
| SPK-REEL-2 3D 效能 | 10 秒 R3F 場景，比較不同 GL 後端 | 每格 ≤ 0.5 秒 |
| SPK-REEL-3 AI B-roll | 10 個科技 B-roll 提示 | 可用率 ≥ 60%；單支可用片段成本在預算內 |
| SPK-TTS-1 TTS（R6 前） | 同一份 45 秒講稿，比較 3–4 個 TTS | 讀音錯誤 ≤ 1；MOS ≥ 3.5 |
