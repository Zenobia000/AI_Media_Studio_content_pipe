# SRS 06｜確認、發布、成效與回饋

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | **楊舒媁** |
| 對應 Epic | E08 確認與發布、E09 成效與回饋 |
| 輸入契約 | builds/（IF-012 manifest）、caption.md（IF-009）、citations.json、assets.json |
| 輸出契約 | approvals、publications、metric_snapshots 資料表；[IF-016 publication.md](07_interfaces.md#if-016publication-與-publicationmd)；reports/weekly.md |
| 工具 | 見 [技術選型 §發布](../architecture/tech-selection.md) |

---

## 1. 原則

1. **看過不等於確認。** 確認是一個明確的指令，而且綁定某一個 Build（或某一版文字）。
2. **確認之後輸入有任何變動，確認就失效**（REEL-FR-002）。
3. **只有已確認的版本可以發布**；自動發布也一樣。
4. 每個平台各自一筆 Publication；同一個 Reel 發到 IG 和 YouTube，就是兩筆，成效分開記錄。
5. 外部發布失敗不會影響 Content 的確認狀態，只影響那一筆 Publication。

## 2. 確認（E08）

**PUB-FR-001｜review 預覽**（R1｜M｜US-070）
- **輸入**：`newsroom review <content_ref>`。
- **處理**：產生 `contents/<id>/review.md`（受管區塊），並在終端機輸出摘要：
  - 成品：Threads 文字全文；輪播總覽圖與每張圖路徑；Reel 預覽與縮圖總覽。
  - 來源：每個 source 的標題與網址。
  - **待確認項**（全部處理完才能確認）：
    - citations.json 中 `unverified` 的說法（DRF-FR-004）
    - glossary 禁用詞與用語問題（DRF-FR-005）
    - 念稿差異（SUB-FR-003）
    - 素材用法：每個用到的 asset 的 origin 與來源（第三方素材特別標示）
    - Build 是否 stale、規格檢查結果
  - 下一步指令。
- **驗收**：一則有 1 個未驗證數字、1 個第三方素材的 Reel，review 列出這兩項。

**PUB-FR-002｜處理待確認項**（R1｜M｜US-070）
- **處理**：待確認項可以用三種方式處理：(a) 回去改稿或換素材後重新製作；(b) 在 review.md 的待確認項打勾，並寫一句處理說明（例如「已查證，官方 X 貼文有提到」）；(c) 執行 `newsroom review <ref> --accept <item_id> --reason "..."`。所有處理紀錄都存進 approvals 資料表的明細。
- **驗收**：還有未處理的待確認項時，approve 被拒絕，並列出剩下哪幾項。

**PUB-FR-003｜確認**（R1｜M｜US-070）
- **輸入**：`newsroom approve <content_ref> [--build <build_id>]`；不指定時使用最新的成功 Build；Threads 則是目前 caption.md 的雜湊。
- **處理**：檢查狀態（必須是 ready）、Build 不是 stale、待確認項都已處理 → 建立 approval → Content 進入 approved。
- **驗收**：對 stale 的 Build 執行 approve 被拒絕；成功確認後，status 顯示 approved 與綁定的 build_id。

**PUB-FR-004｜撤銷確認**（R1｜M）
- **處理**：`newsroom unapprove <ref>`，或輸入變更導致 stale 時自動撤銷；approval 記錄 `revoked_at`，Content 回到 review。已經發布的 Publication 仍然指向原本的 Build。
- **驗收**：撤銷後再確認，會產生一筆新的 approval；舊的保留。

**PUB-FR-005｜發布包**（R1｜M｜US-071）
- **處理**：確認後產生 `contents/<id>/publish/`：成品檔（mp4、jpg）、`caption-<platform>.txt`（依平台調整：Threads 串文拆段、IG hashtag 放最後、YouTube 標題＋說明）、`checklist.md`（發布前檢查：封面、比例、字幕、來源標註）。
- **驗收**：一則 Reel 確認後，發布包裡有 mp4 與 IG、Threads、YouTube 三份 caption。

## 3. 發布

**PUB-FR-010｜手動發布紀錄**（R1｜M｜US-072）
- **輸入**：`newsroom mark-published <content_ref> --platform <p> --url <url> [--at <time>]`。
- **處理**：檢查 Content 已確認；建立（或更新 pending／scheduled 的）Publication 為 published；第一次有 Publication 被設為 published 時，Content 進入 published；更新 publication.md。
- **例外**：網址格式和平台不符時警告（例如 platform=instagram，但網址是 threads.net）。
- **驗收**：同一則 Reel 分別記錄 IG 與 YouTube，產生兩筆 Publication；Content 只在第一筆時改狀態。

**PUB-FR-011｜排程紀錄（原生排程）**（R1｜S）
- **輸入**：`newsroom schedule <ref> --platform <p> --at <time>`。
- **處理**：建立 scheduled 狀態的 Publication，代表「人已經在平台上設好原生排程」；到了預定時間後 1 小時仍沒有填回網址，就在 status 與早報提醒。
- **驗收**：排程時間過後，status 出現提醒。

**PUB-FR-012｜媒體暫存公開網址**（R6｜M）
- **背景**：IG 與 Threads 的發布 API 要求媒體是公開可存取的 URL。
- **處理**：發布前，把成品上傳到物件儲存並產生有時效的網址（預設 24 小時）；發布成功後刪除。儲存服務透過 adapter 接入。
- **驗收**：發布後該檔案的網址失效；儲存空間裡沒有殘留檔案。

**PUB-FR-013｜自動發布**（R6｜S｜US-073）
- **前置**：Spike「官方 API 與聚合服務發布」的結論。
- **處理**：
  1. publisher adapter 介面：`publish(media, caption, platform, options) -> PublishResult`；第一批實作是 IG 官方 API（Reels、4:5 輪播）、Threads 官方 API（文字、圖片、影片），YouTube／TikTok 依 Spike 結果決定走官方 API 或聚合服務。
  2. 排程器在預定時間取出 scheduled 的 Publication，**再次檢查 Content 仍然是 approved 而且 Build 沒有 stale**，才執行發布。
  3. 輪詢媒體處理狀態，直到完成或逾時（預設 10 分鐘）。
  4. 成功時寫回網址、外部 ID、實際發布時間；失敗時 Publication 設為 failed，依錯誤類型決定是否重試（最多 3 次，指數退避），並通知。
- **安全**：config 有一個總開關 `auto_publish.enabled`，預設關閉；另外有每日發文上限。
- **驗收**：用私人測試帳號自動發布 IG Reel、IG 輪播、Threads 影片各 5 次，成功率 100%（含重試）；在預定時間前修改稿件，發布被擋下並通知。

**PUB-FR-014｜連發模式**（R5｜S｜US-074）
- **處理**：候選被判為 S 級並且被選題時，依 05 §7.1 自動建立連發計畫：threads（目標 30 分鐘內）、carousel（2 小時內）、reel-raw、reel-explain、meme（選用）；每則 content 有目標發布時間，status 依剩餘時間排序並提醒。
- **驗收**：S 級選題後自動建立 4 則 content，每則都有目標時間；超過目標時間時 status 標紅。

**PUB-FR-015｜一題三波**（R6｜C｜US-075）
- **處理**：S 級 topic 發布第一波後，自動建立第二波（tool-review，3 天後）與第三波（ai-engineering，7 天後）的提醒；人確認後才建立 content（wave=2、3），沿用 topic 的來源與素材。
- **驗收**：第一波發布 3 天後，早報出現第二波提醒。

## 4. 成效與回饋（E09）

**FB-FR-001｜手填成效**（R2｜M｜US-080）
- **輸入**：`newsroom metrics add <publication_id> --views 1234 --likes 56 ...`，或在 `reports/metrics-input.md` 的受管表格填寫後執行 `newsroom metrics import`。
- **處理**：建立 MetricSnapshot（source=manual）；沒填的指標存 null。
- **驗收**：只填 views，其他指標都是 null，不是 0。

**FB-FR-002｜自動回收成效**（R4｜S｜US-081）
- **處理**：排程器在每則 Publication 發布後的 1h、6h、24h、72h、7 天，呼叫 metrics adapter：
  - IG：使用 `views` 等目前有效的指標（已棄用的 `plays`、`impressions` 不可使用）；跨發到 FB 的 Reel 另外記錄。
  - Threads：views、likes、replies、reposts、quotes、shares。
  - YouTube（R6）：YouTube Analytics API。
  每次一筆 append-only 的 snapshot，記錄指標版本（`metric_version`）；同一個時間點失敗時，最多重試到下一個時間點前。
- **驗收**：10 則已發布貼文在 5 個時間點都有 snapshot；views 單調不減（遞減時記錄為平台修正）。

**FB-FR-003｜週報**（R2｜M｜US-082）
- **輸入**：`newsroom retro [--week 2026-W43]`。
- **處理**：彙整本週的 Publication 與最新 snapshot，產出 `reports/weekly.md`：
  - 本週發了什麼（依節目、格式、平台）
  - 表現排名（以 24h、7d views 為主，互動率為輔）
  - 速度：各則從首次出現到發布的時間
  - 依節目與格式比較的平均值
  - 用 LLM 產生 3–5 條建議（下週選題方向、格式、鉤子句型），**標明是建議，由人決定**
- **驗收**：有 5 則 Publication 的週報，數字與資料庫一致；建議段落有標示。

**FB-FR-004｜門檻與名詞表調整建議**（R6｜C｜US-083）
- **處理**：週報分析「被判為 S 卻表現普通」「判為 B 卻表現好」的題目，提出熱度權重調整建議；從字幕人工修改紀錄中找出常被修正的詞，提出 glossary 新增建議。以 diff 形式呈現，人執行 `newsroom retro --apply <id>` 才會套用。
- **驗收**：建議以 diff 呈現；沒有執行 apply 前，config 與 glossary 都不變。

**FB-FR-005｜每步耗時統計**（R2｜S）
- **處理**：從 Job 時間戳與 Content 狀態轉換時間，統計每則內容在每個階段花了多少時間（機器時間與等待人的時間分開），寫進週報。對應 PRD §5 的成功指標。
- **驗收**：週報有每個階段的耗時中位數。

## 5. 驗證實驗（Spike）

| Spike | 內容 | 過關條件 |
|---|---|---|
| SPK-PUB-1 官方 API 發布 | 私人測試帳號，從 Python 發 IG Reel、IG 4:5 輪播、Threads 影片＋文字各 5 次 | 成功率 100%（含重試）；記錄處理時間與錯誤碼 |
| SPK-PUB-2 聚合服務 | 用聚合服務免費方案發 YouTube Shorts 與 TikTok 各 2 次 | 影片為公開可見（未稽核的官方 API 會強制私人） |
| SPK-PUB-3 暫存網址 | 物件儲存＋有時效網址，給 IG／Threads API 抓取 | IG／Threads 都能成功抓取；過期後無法存取 |
| SPK-FB-1 成效回收 | 10 則貼文，5 個時間點 | 每個時間點都寫入；無缺值 |
