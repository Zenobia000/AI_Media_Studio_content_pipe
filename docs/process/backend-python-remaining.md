# 後端改成 Python：還沒做的部分與原因

日期 2026-11-04。總計畫在 [backend-python-plan.md](backend-python-plan.md)，這份只列**還沒做的**，並說明每一項為什麼還沒做、做的話要什麼。

驗證方式一律是：同一個資料庫、同一個請求，Node 版與 Python 版各答一次，比對狀態碼、重要標頭和 JSON（含 ETag）。資料來源是 `AIHOT/scripts/seed-parity.ts` 造的固定資料（不需要模型金鑰）。

## 已經完成（可以直接切換）

| 範圍 | 內容 |
|---|---|
| API 骨架 | 請求編號、重定向表、OAuth 探測、404／405／錯誤格式（problem+json）、`/api/health` |
| 公開 API v1（全部） | `items`、`hot-topics`、`stories/:id`、`dailies`（含 latest、依日期）、`weeklies`、`monthlies`、`selected/snapshot`、`selected/changes`，以及 `/api/v1/*` 的 404、405、CORS 預檢 |
| 網站專用 API | `meta`、`contact`、`stats`、`changelog`、`items/availability`、`timeline`、`pool`（含搜尋與相關度排序）、`search/suggestions`、`hot`、事件頁與 `followups`、閱讀群組、主題目錄與主題頁、圖片代理 `/api/img-proxy` 與分享圖 `/og/*`（簽名、快取鍵、標頭、404 與 Node 版逐字一致；圖片只比格式與尺寸）、文章頁與 Markdown 匯出、回饋提交 `POST /api/site/feedback`（限流、封禁、去重；轉送到飛書留給 worker 的重送工作）、Agent 回答（`/api/v1/agent/*`）、MCP（`/api/mcp`，手寫 JSON-RPC，對照官方 SDK 的回應）、RSS（摘要、全文、全部、日報／週報／月報、分類）、`sitemap.xml`、`sitemaps/*`、`llms.txt`、報告索引／最新頁／導覽／月份／詳細頁 |
| 靜態檔 | `robots.txt`、`security.txt`、`manifest.webmanifest`、`openapi-v1.json`、各種圖示、聯絡 QR、IndexNow 金鑰檔 |
| 底層 | 時間、文字、ID、游標、設定與憑證、出站網路防護與請求、快取、稽核紀錄、簽名的圖片代理網址 |

## A. 做得了、也驗證得了的部分：已全部完成

網站讀取端的頁面與 API 都已有 Python 版（見上面的完成表）。

> 文章內文清理（sanitize-html、turndown、cheerio）是照 TypeScript 的設定重寫的，用 138 組由 Node 版產生的輸入對照過，但輸入很髒的網頁（壞掉的標籤、極少見的實體）可能還會有出入。

> 站點地圖沒有算內容指紋（`revision`，給變更通知用）和落地到磁碟的備份，這兩個屬於通知器，歸 B 區。

> 擷取推送 `POST /api/ingest/items` 從 A 區移到 B 區：它會寫入文章並排進處理佇列（`jobs/content`），佇列是 B 區的工作佇列。

> 圖片的差異：轉檔縮放用 Pillow，分享圖用 Pillow 重新排版，輸出與 Node 版（sharp、satori）的像素不同；SVG 原樣放行（Pillow 畫不了向量）；GIF 動圖轉 WebP 與 AVIF 變體要靠背景工作，這裡不排佇列，等待轉檔的圖用 `no-store` 回傳；分享圖字型沿用 Node 版的 Noto Sans SC 子集，子集沒有的字（繁體字）兩邊都會顯示成方框，要補字型由站台放進 `assets/og-fonts`。

## B 區已完成的部分

| 項目 | 驗證方式 |
|---|---|
| 工作佇列與排程（取代 pg-boss）：去重、延遲、優先序、重試與退避、逾時、cron 與漏跑補跑、優雅關機 | 行為測試（`tests/integration/test_job_queue.py`）。用自己的資料表（migration 0123），不與 pg-boss 互通：切換是整個引擎一起換 |
| 發布流程（`publish`）：公開投影、席位、同步帳本、搜尋列 | 同一份資料庫複製兩份，改輸入（判斷、人工覆寫、信源授權與角色、事實歸屬）後，一份交給 Node、一份交給 Python 重新發布，逐列比對七種情境 |
| 熱度排行與每小時快照 | 同上，五種情境、三個時間點 |
| 文章入庫（`upsertMaterial`：身份、版本、時間線規則、原發信源歸屬、搜尋用的重複版本判斷） | 同一批 20 筆報告交給 Node 與 Python 各存進自己的資料庫複本，逐列比對文章、版本、發現紀錄、發布列、稽核紀錄 |
| 收集器的日期解析（`sources/dates`：各種日期寫法、相對時間「3 小時前」） | 由 Node 對 269 組輸入產生答案，Python 逐一比對；JavaScript 的 `Date.parse` 是自己用規則重寫的，只涵蓋這些場合讀到的寫法 |
| 後臺登入與守門（密碼登入、工作階段、CSRF、登出、飛書登入的狀態驗證）、後臺的回饋處理（列表、狀態、封禁、刪除）、設定（聯絡 QR、通知目標、預算）、稽核紀錄 | Node 與 Python 各連自己的資料庫複本，送同樣的請求、比對回應，最後比對兩份資料庫的結果。飛書登入的真實流程沒驗證（要真實飛書） |
| RSS／Atom／RDF 收集器（含壞掉的 XML、ETag／304、強制重讀） | 本機架一個假網站放八種餵送，Node 的 `fetchRss` 與 Python 各讀一次，逐筆比對候選文章與快取驗證資訊。解析器自己寫，照 fast-xml-parser 的寬容行為 |
| JSON 清單收集器（API、`__NEXT_DATA__`、Next.js flight、`window` 變數、POST 本文）、清單過濾（前綴、分類、雜訊詞、發布時間窗）、設定鍵檢查 | 假網站兩邊各讀一次比對候選文章；過濾與設定鍵檢查由 Node 產生答案逐項比對。經 Jina 讀取的清單要付費收據機制，尚未移植；超過西元 9999 年的時間（單位填錯）Python 當成未知 |
| 網頁列表收集器（選擇器、Markdown、Docusaurus／Intercom 更新日誌、mimo 首頁）的列表讀取 | 假網站放六種頁面，Node 與 Python 各讀一次，逐筆比對。選擇器用 soupsieve、HTML 用 html5lib，對應 cheerio／parse5。詳情頁補抓（`fetchDetail`）要等 `content/extract.ts` 移植；經 Jina 的列表尚未移植 |
| 信源健康度（`sources/health`：連續失敗、反覆讀不出、缺時間、反覆修訂、詳情失敗、各信源自己的發文節奏與報告文字） | 一份資料庫放進合成的抓取紀錄、文章與修訂，Node 與 Python 在同一時刻各讀一次，比對分組與報告文字 |

## B. 做得了，但驗證不了（需要金鑰、外部服務或真實資料）

這些我可以寫，但寫出來的東西**沒有辦法證明和 TypeScript 版行為一致**，所以我沒有動。

| 項目 | 規模 | 為什麼驗證不了 |
|---|---|---|
| 模型精選、評分、寫作、翻譯（`editorial/`、`providers/`） | 約 3,000 行 | 要呼叫付費的模型服務；沒有金鑰。可以用測試裡的假回應，但那只證明程式會跑，不證明提示詞與結果和原版一致 |
| 事件聚類與熱度排行計算（`events/`） | 約 2,000 行 | 聚類靠嵌入向量與模型判斷（同事件、相關事件）；排行計算本身是純 SQL 可以比對，但輸入（聚類結果）要有模型 |
| 信源收集器（`sources/`） | 約 2,700 行 | RSS、網頁清單、JSON、X、公眾號。要連真實網站；網站內容每天在變，無法逐條比對。可以用錄下來的回應測試 |
| 文章入庫與原文擷取（`content/`） | 約 1,500 行 | 取代 `collect/materials.py` 的精簡版；Readability 清理的輸出要和 Node 版一致，需要一批真實網頁當樣本 |
| 日報／週報／月報產生（`reports/`） | 約 800 行 | 產生內容要呼叫模型寫稿 |
| 通知（`notify/`）、營運警報（`operations/`） | 約 2,200 行 | 要飛書／Webhook 憑證；備份與心跳要 `pg_dump` 與排程環境 |
| 後臺與後臺登入（`admin/`、`routes/admin*.ts`） | 約 1,900 行 | 登入有密碼與飛書 OAuth；後臺大多是寫入操作，要在拋棄式資料庫上逐項測 |
| 工作佇列的處理常式與 worker 主程式（`jobs/content.ts` 等、`apps/worker`） | 約 700 行 | 佇列本身（`engine/jobs/queue.py`）已完成；各工作的處理常式要等它們呼叫的模型步驟與收集器 |

## C. 不建議照搬

- **Playwright 瀏覽器端的測試（`apps/web/tests`）**：是網站前端的測試，不屬於後端。
- **`scripts/eval-*.ts`（精選／關係／故事摘要的評測）**：要金鑰與金標資料，且是模型評測用的，不是產品功能。

## 要切換的時候

網站前端（`apps/web`）依 `API_BASE_URL` 向 API 取資料，所以前端不用改。**切換條件**：A 區全部完成（網站才不會缺頁）。B 區沒做完之前，TypeScript 的 worker 還是要繼續跑，負責收集、聚類、精選、發布與日報，Python 的 API 讀同一個資料庫。這是「API 先換、引擎後換」的分段切換，不是一次全換。

## 沒有驗證到的（目前已完成的部分也一樣）

- 出站請求經過代理時，連線沒有固定在檢查過的位址（Node 版有），見 `src/newsroom/engine/lib/http_fetch.py` 開頭。
- 全部對照都用同一份造出來的資料；沒有用過真實的大量資料、也沒有壓力測試（連線池大小、搜尋並行限制在高流量下的行為）。
- Python 版的伺服器沒有在 Docker 裡跑過、沒有掛在網站前端後面實際瀏覽過頁面。
