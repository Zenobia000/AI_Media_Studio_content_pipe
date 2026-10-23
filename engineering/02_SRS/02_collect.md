# SRS 02｜情報收集與 inbox

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 擁有者 | **Albert Chiang** |
| 對應 Epic | E02 情報收集 |
| 輸出契約 | sources、candidates 資料表；[IF-001 inbox](07_interfaces.md#if-001inbox-檔案collect--人--produce) |
| 工具 | 見 [技術選型 §收集](../04_Tech_Selection.md) |

---

## 1. 流程

```
來源（手動網址／RSS／GitHub／HN／Reddit／HF／X／changedetection）
  → Collector adapter：抓取、轉成 RawItem
  → 正規化：網址正規化、去重、寫入 sources（記錄首次出現時間）
  → 聚類：同一事件的 source 歸成同一個 candidate
  → 評分：計算熱度等級與建議發文組合
  → 寫入 inbox 受管區塊
  → S 級事件通知
```

收集與評分都是確定性程式，不使用 LLM。唯一例外是把候選標題翻成繁中（COL-FR-012），而且這一步可以關掉。

## 2. Collector adapter 介面

```python
class Collector(Protocol):
    name: str                       # 例如 "rss"、"github"、"hn"、"x_list"
    def collect(self, since: datetime, cfg: CollectorConfig) -> Iterable[RawItem]: ...

@dataclass
class RawItem:
    url: str
    title: str
    kind: str                       # article | tweet | release | video | paper | forum
    published_at: datetime | None
    author: str | None
    publisher: str | None
    signals: dict[str, float]       # 例如 {"hn_points": 412, "comments": 120}
    raw: dict                       # 原始回應，存進 raw_json
```

每個 collector 都要能單獨啟用或停用、單獨執行（`newsroom collect --source rss`）；任一個失敗不影響其他 collector。

## 3. 功能需求

**COL-FR-001｜手動新增候選**（R1｜M｜US-010）
- **輸入**：`newsroom add-source <url> [--note "..."] [--program] [--formats]`。
- **處理**：網址正規化 → 寫入 sources 與 candidates → 立即更新今天的 inbox。手動加入的候選預設排在最上面，標示「手動」。
- **例外**：網址已經存在時，不重複建立；把備註附加到既有候選上，並告訴使用者它在哪一天的 inbox。
- **驗收**：同一個網址加兩次，資料庫只有一筆；第二次的備註有被保留。

**COL-FR-002｜網址正規化**（R1｜M）
- **處理**：轉小寫 host；去掉 fragment；去掉追蹤參數（`utm_*`、`fbclid`、`gclid`、`ref`、`s`、`t` 等，清單放在 config）；展開短網址（t.co、bit.ly、lnkd.in，最多跟 5 次轉址，逾時 5 秒）；特殊格式轉成標準形式：
  - arXiv：`arxiv.org/abs/<id>`（去掉版本號，另存版本）
  - GitHub：`github.com/<owner>/<repo>`（release、commit 另存子路徑）
  - X：`x.com/<user>/status/<id>`（twitter.com、mobile 子網域、`?s=` 都統一）
  - YouTube：`youtube.com/watch?v=<id>`（youtu.be、shorts 統一）
- **驗收**：準備 30 個測試網址（含上述各種形式），正規化結果全部符合預期。

**COL-FR-003｜去重與首次出現時間**（R1｜M）
- **處理**：以 canonical_url 決定 source_id；已存在時只更新 signals，不改 `first_seen_at`。
- **驗收**：同一篇文章從 RSS 和 HN 各收到一次，sources 只有一筆，`first_seen_at` 是較早的那次。

**COL-FR-004｜RSS／Atom 收集**（R2｜M｜US-012）
- **輸入**：`watchlist/sites.md` 的 RSS 清單（表格：名稱、網址、權重、預設節目）。
- **處理**：使用條件式請求（ETag、Last-Modified）減少流量；每個 feed 逾時 15 秒；記錄每個 feed 上次成功的時間。
- **例外**：某個 feed 連續失敗 3 次時，在 inbox 頁尾列出，並寫入日誌。
- **驗收**：用 10 個真實 feed 執行兩輪，第二輪大部分回應 304；故意放一個壞網址，其他 feed 仍正常收集。

**COL-FR-005｜GitHub release 收集**（R2｜M｜US-012）
- **輸入**：`watchlist/sites.md` 的 GitHub repo 清單。
- **處理**：讀 `releases.atom`；或在有 `GITHUB_TOKEN` 時使用 GitHub API，同時記錄 star 數變化作為熱度訊號。
- **驗收**：新增一個 repo 後，最新的 release 出現在候選裡，並附上 star 數。

**COL-FR-006｜Hacker News 收集**（R2｜M｜US-012）
- **處理**：從 HN 的官方 API 或搜尋 API 抓首頁與 Show HN，篩選含 AI 關鍵字（清單放在 config）或分數超過門檻的項目；記錄分數與留言數的時間序列。
- **驗收**：抓到的項目附帶 points 與 comments；同一個項目第二輪更新 signals，不重複建立。

**COL-FR-007｜Reddit 收集**（R2｜C｜US-012）
- **注意**：研究顯示 Reddit 公開 API 預計 2027-03 前關閉、RSS 2026-11-13 停止（見 PRD D11、Q5）。這條需求降為選用；實作前依 Q5 的決定處理。
- **輸入**：`watchlist/forums.md` 的 subreddit 清單。
- **處理**：如果有取得 API 存取權，用官方 API（OAuth）抓 hot／new，記錄 score 與留言數；沒有就停用。Reddit 上的 AI 討論，改由彙整電子報（例如 AINews）間接涵蓋。
- **驗收**：停用時 inbox 正常產生；啟用時，設定 3 個 subreddit，候選裡出現對應的貼文。

**COL-FR-019｜彙整電子報收集**（R2｜M｜US-012）
- **輸入**：config 中的電子報 feed 清單（例如 latent.space 的 AINews、Import AI、TLDR AI、Simon Willison）。
- **處理**：用 RSS 收集器收；電子報一期通常含多則題目，要把內文的外部連結逐一拆成 source，並記錄「被電子報提及」作為熱度訊號。電子報的發布日期不可靠時，用 guid 去重。
- **驗收**：一期 AINews 被拆成多個 source；連結到同一篇官方 blog 的，和 RSS 收到的歸成同一個候選。

**COL-FR-008｜Hugging Face 熱門**（R2｜C）
- **處理**：抓 HF 的 trending models／papers，記錄 likes 與下載數。
- **驗收**：熱門模型出現在候選，附上 likes。

**COL-FR-009｜X List 與書籤收集**（R4｜S｜US-013）
- **前置**：Spike「X 資料取得方式與成本」的結論（見技術選型）。
- **輸入**：`watchlist/x.md`（List ID、帳號清單、權重）；書籤需要使用者授權（OAuth 2.0 PKCE），token 存在作業系統的 keyring。
- **處理**：依 Spike 結論，透過 X adapter 取得 List 時間軸與書籤；記錄每則推文的互動數（likes、reposts、replies、views）與時間；推文裡的外部連結也要正規化，並建立關聯。
- **例外**：額度不足時 `E_RATE_LIMIT`，記錄可以重試的時間；預算達上限時停用，退回手動網址。
- **驗收**：設定一個測試 List，新推文在下一輪收集時出現；推文裡的外部連結和 RSS 收到的同一篇文章被歸成同一個候選。

**COL-FR-010｜網頁變動監測**（R5｜C｜US-018）
- **處理**：讀取變動監測服務（以 Docker 自架，見技術選型）的 webhook 或 API；每次變動建立一筆 `kind=other` 的 source，標題寫「<頁面名稱> 有更新」，並附上 diff 摘要。
- **驗收**：監測一個測試頁面，修改它之後 15 分鐘內出現在候選。

**COL-FR-011｜聚類**（R4｜S｜US-014）
- **處理**：
  1. 規則式比對：同一個 canonical_url、同一個 GitHub repo、同一個 arXiv id、推文引用的外部連結。
  2. 實體比對：從標題抽出產品名、模型名、公司名（依 `glossary.md` 和 config 裡的實體表），同一個實體在 48 小時內出現的 source 歸成同一個候選。
  3. 語意比對：標題加摘要做多語嵌入（中英混合），向量存在 SQLite；48 小時內餘弦相似度超過門檻（Spike 決定，預設 0.85）的歸為同一事件。
  4. 不確定的情況不合併，保留給人判斷；提供 `newsroom merge <cand_a> <cand_b>` 讓人手動合併。
- **輸出**：candidate 的 `cluster_key`、共振來源數。
- **驗收**：準備 20 筆有標準答案的測試資料（7 個事件），聚類的精確率 ≥ 90%（寧可不合併，也不要合錯）。

**COL-FR-012｜候選標題翻譯**（R2｜C）
- **處理**：用便宜的 LLM 把英文標題翻成繁中，結果快取，只翻一次；透過 DRF 模組的 LLM adapter 呼叫，並納入預算控管。
- **例外**：翻譯失敗時顯示原文，不影響 inbox。
- **驗收**：關掉這個功能，inbox 仍能正常產生。

**COL-FR-013｜熱度分級**（R4｜S｜US-015）
- **處理**：依 05 §7.1 的訊號計算分數，設定都放在 config：
  - 跨來源共振：6 小時內的來源數
  - X 互動速度：相對該帳號過去 30 則的中位數
  - HN／Reddit 速度：每小時分數增加量
  - GitHub star 增速、HF 熱門
  - 來源權重：官方 > 評測機構 > 媒體 > KOL
  - 在地缺口：中文圈尚未報導時加分（COL-FR-016）
  - 發表會：`calendar/events.md` 裡的活動結束後，相關候選直接設為 S
- **輸出**：tier（S／A／B）、分數明細、建議節目與格式（依 05 §7.1 的對照表）。
- **驗收**：分數明細可以重現（同樣的輸入得到同樣的結果）；用過去兩週的真實資料回測，人工標記的 S 級事件有 ≥ 80% 被判為 S 或 A。

**COL-FR-014｜inbox 產生**（R1｜M｜US-011）
- **輸入**：candidates（status=new 或 picked）、今天的日期。
- **處理**：依 IF-001 格式產生；依 tier 分組，組內依分數與首次出現時間排序；R1 還沒有分級時，全部放在「未分級」；重新產生時讀回人的勾選、節目、格式、備註並保留。
- **輸出**：`inbox/YYYY-MM-DD.md`。
- **例外**：受管區塊損壞時，`E_MANAGED_BLOCK`，不覆寫。
- **驗收**：勾選兩則、寫好備註後重新產生 inbox，勾選和備註都還在；新的候選有加進去。

**COL-FR-015｜跨日候選**（R1｜M）
- **處理**：前一天沒有勾選、而且在 48 小時內的候選，帶到今天的 inbox，標示「昨日」；超過 48 小時的自動設為 skipped。勾選過但還沒 produce 的，持續保留。
- **驗收**：模擬三天的資料，候選出現與消失的時間符合規則。

**COL-FR-016｜在地熱度檢查**（R4｜C｜US-019）
- **前置**：Spike「Threads 關鍵字搜尋與 IG business_discovery 額度」的結論。研究顯示 Threads 關鍵字搜尋是每位使用者每 24 小時 2,200 次，但 `threads_keyword_search` 權限通過審核前只能搜自己的貼文，所以要提早送審。
- **處理**：對 S／A 級候選，用官方 API 查詢中文圈是否已經有人報導（關鍵字搜尋、監控 `watchlist/creators.md` 裡的創作者）；結果寫進候選的「在地」欄位；嚴格遵守額度，額度不足時跳過。
- **驗收**：額度用完時，inbox 仍然正常產生，「在地」欄位顯示「未檢查」。

**COL-FR-017｜發表會行事曆**（R4｜S｜US-017）
- **輸入**：`calendar/events.md`（IF 格式見 05 §5.3）。
- **處理**：解析表格寫入 events 資料表；活動前一天在早報提醒，並建立一個準備用的候選（背景資料、上一代規格）；觸發 OPS-FR-004 的頻率切換；活動結束後，含相關關鍵字的候選直接設為 S。
- **擴充**：從官方活動頁、官方 X 帳號的直播預告偵測到的新活動，寫進 inbox 讓人確認，不直接加入行事曆。
- **驗收**：新增一個假活動，前一天的早報有提醒；活動結束後，相關候選被標為 S。

**COL-FR-018｜S 級通知**（R4｜S｜US-016）
- **處理**：候選第一次升到 S 級時，呼叫 OPS-FR-003 通知，內容包含標題、共振來源數、inbox 連結。
- **驗收**：同一個候選只通知一次。

## 4. 建題（produce 的第一步）

**COL-FR-020｜依勾選建立 topic 與 content**（R1｜M｜US-004）
- **輸入**：inbox 中被勾選的候選。
- **處理**：
  1. 用 candidate_id 查是否已經有 topic，有就沿用。
  2. 沒有就建立 topic 資料夾、topic.md、notes.md（寫入 inbox 備註）。
  3. 依「格式」欄位（空白時用建議值）為每個格式建立 content（content.md），已經存在的不重建。
  4. candidate 狀態改成 picked。
- **輸出**：新建或沿用的 topic 與 content 清單。
- **驗收**：同一則候選勾選後執行兩次 produce，topic 與 content 都只有一份；第二次在格式裡多加一個 `reel`，只新增 reel 這一則。

**COL-FR-021｜多個候選合併成一題**（R1｜S）
- **處理**：在 inbox 備註寫 `合併到 cand_xxx`，或執行 `newsroom merge`，produce 時會把這些候選歸到同一個 topic。
- **驗收**：兩個候選合併後只建立一個 topic，source_ids 包含兩邊的來源。
