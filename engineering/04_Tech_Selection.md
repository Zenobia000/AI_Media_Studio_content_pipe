# 技術選型｜每個環節的工具組合

| 項目 | 內容 |
|---|---|
| 版本 | v1.0｜2026-10-23 |
| 研究方式 | 從 2025 下半年到 2026 年的實務分享（Reddit、Hacker News、dev.to、Medium、n8n 社群、個人部落格、GitHub 專案）回推大家實際使用的組合，再挑主流工具 |
| 資料查核 | GitHub 星數、授權、最後更新時間都在 2026-10-23 用 GitHub API 實際查詢；價格與額度凡是沒有對到官方文件的，都標「未核實」 |
| 性質 | **本文全部是建議**。每個標記「需 Spike」的項目，都對應到一張實驗票，驗證後才定案 |

---

## 0. 選型原則與標記

1. **主流優先。** 社群大、文件完整、近期仍在維護、有 Discord 或論壇可以發問。卡住時找得到人求救，比功能多一點更重要。
2. **組合拳。** 沒有一個工具能處理一整個環節。每個環節都列出「主力組合＋備援」，並寫清楚每個工具各自負責哪種情況。
3. **可替換。** 外部工具一律包成 adapter（SRS 00 NFR-009）。工具換代時，只換 adapter，不動流程。
4. **先驗證再定案。** 價格、額度、品質，都用我們自己的資料實測。

| 標記 | 意義 |
|---|---|
| **採用** | 有足夠證據，直接用；有問題再調整 |
| **需 Spike** | 方向正確，但有關鍵數字（成本、品質、速度）需要實測；對應一張 Spike 票 |
| **備援** | 主力失效時的替代方案，先不實作，adapter 介面要能接 |
| **備選** | 有特定需求時再考慮 |
| **不採用** | 明確不建議，附原因 |

---

## 1. 收集與補料（Albert Chiang）

### 1.1 X（Twitter）題目發現

| 工具 | 組合中的角色 | 連結 | 授權 | 最後更新 | 社群 | 求救管道 | 標記 |
|---|---|---|---|---|---|---|---|
| X API v2（按量計費） | 讀 List 時間軸（`since_id`）、讀自己的書籤、`media.fields=variants` 取得影片 mp4 | [docs.x.com 價目](https://docs.x.com/x-api/getting-started/pricing) | 商業 | 2026 持續調價 | 官方 | devcommunity.x.com | **需 Spike** |
| latent.space/feed（AINews） | 每天彙整整個 X 的 AI 討論，補 List 漏掉的題目，也讓 List 可以縮小 | [feed](https://www.latent.space/feed) | — | 2026-10-22 | 大 | — | **採用** |
| twitterapi.io | 降成本的第三方 API（非官方，第三方報價每千則約 0.15 美元） | [比較文](https://www.socialcrawl.dev/blog/best-twitter-x-data-apis-2026) | 商業 | 未核實 | 中 | 廠商 | 備援 |
| Apify X List actors | 第三方爬取服務，n8n 容易呼叫；多為個人維護 | [例](https://apify.com/scrapesmith/x-twitter-list-scraper) | 商業 | 未核實 | Apify 社群大 | Apify Discord | 備援 |
| twscrape | 用自己帳號 cookie，有封號風險 | [vladkens/twscrape](https://github.com/vladkens/twscrape) | MIT | 2026-08 | 2.9k★ | GitHub | 不採用（上線） |
| RSSHub `/twitter` | 需要 auth_token；公開實例已不能用 | [DIYgod/RSSHub](https://github.com/DIYgod/RSSHub) | AGPL-3.0 | 2026-10 | 46.6k★ | GitHub、Telegram | 不採用（上線） |

**成本重估**：官方按「回傳的貼文數」計費，每則 0.005 美元；同一則在同一個 UTC 日內重複讀不再計費；讀自己的書籤每則 0.001 美元。一個 150 人的 List，每天約 600–1,500 則（含回覆與轉推，未實測），等於**每月約 90–225 美元**，比 05 文件估的 15–50 美元高。

**建議做法**：核心 50 個帳號走官方 API，其餘靠 AINews 補，書籤一定接（便宜而且是創作者自己挑的）。

**實務案例**
- [frankzch/ai-news-brief](https://github.com/frankzch/ai-news-brief)：370 多個來源，其中約 310 個是 X 帳號。它用 Playwright 持久登入搜尋 X，不走官方 API，等於自己扛封號風險。
- [TechCrunch 2026-09-15](https://techcrunch.com/2026/09/15/nitter-and-xcancel-are-dead-again-after-xs-latest-legal-actions)：Nitter、XCancel 在 X 的法律行動後再度下線。

**坑**：List 時間軸包含回覆與轉推，會放大計費筆數；過了 UTC 0 點，同一則貼文會重新計費；用 cookie 的方案隨時可能失效或封號。

**Spike → SPK-COL-1**：一個 150 人的 List 每 15 分鐘輪詢，跑 7 天，記錄每日則數與帳單。過關條件：推估月費 ≤ 60 美元；超過就縮成核心 50 人。

### 1.2 Threads／IG 在地熱度

| 工具 | 角色 | 連結 | 標記 |
|---|---|---|---|
| Threads API 關鍵字搜尋 | 中文關鍵字熱度（`search_type=TOP/RECENT`）；**每位使用者每 24 小時 2,200 次**；沒有結果的查詢不計 | [官方文件](https://developers.facebook.com/documentation/threads/keyword-search) | **需 Spike** |
| IG Graph `business_discovery` | 讀指定創作者帳號的貼文與互動數，用來監控競品 | [官方文件](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery) | **採用** |

**修正 05 文件**：Threads 搜尋的額度是「每 24 小時 2,200 次」，不是「7 天 500 次」。但**權限通過審核前只能搜自己的貼文**，所以要提早送審。

**坑**：敏感關鍵字直接回空陣列；business_discovery 拿不到個人帳號；IG 速率限制的類型，各廠商說法互相矛盾。

**Spike → SPK-COL-2**：送審 `threads_keyword_search` 並記錄審核時間；通過後，30 個中文關鍵字每小時查一次，跑 3 天。過關條件：S 級題目發出後 2 小時內搜得到。

### 1.3 RSS、GitHub、Hacker News、Hugging Face、電子報、Reddit

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| feedparser | 解析 RSS／Atom（事實標準） | [kurtmckee/feedparser](https://github.com/kurtmckee/feedparser) | BSD 系 | 2026-10 | 2.4k★ | **採用** |
| httpx（ETag／If-Modified-Since） | 條件式請求，省流量、避免被擋 | [encode/httpx](https://github.com/encode/httpx) | BSD-3 | — | 大 | **採用** |
| GitHub `releases.atom`／`gh api` | 追新版本與 star 數 | — | — | — | — | **採用** |
| hnrss.org | HN 熱門（`/newest?points=N`），快取略慢幾分鐘 | [hnrss/hnrss](https://github.com/hnrss/hnrss) | — | 服務在線 | 620★ | **採用** |
| HN 官方 API／Algolia | hnrss 失效時直接查 | [HackerNews/API](https://github.com/HackerNews/API) | MIT | — | 13k★ | 備援 |
| HF `/api/daily_papers` | 熱門論文 JSON | — | — | — | — | **採用** |
| 彙整電子報 feed | latent.space/feed（AINews）、importai.substack.com/feed、tldr.tech/api/rss/ai、bensbites.com/feed、therundown.ai/feed、simonwillison.net/atom/everything/ | — | — | 2026-10 仍在更新 | — | **採用** |
| RSS-Bridge | 為沒有 RSS 的網站產生 feed | [RSS-Bridge/rss-bridge](https://github.com/RSS-Bridge/rss-bridge) | Unlicense | 2026-10 | 9.3k★ | 備援 |
| Miniflux／FreshRSS | 想要現成閱讀器介面時用 | [miniflux/v2](https://github.com/miniflux/v2)、[FreshRSS](https://github.com/FreshRSS/FreshRSS) | Apache-2.0／AGPL-3.0 | 2026-10 | 9.8k★／16.4k★ | 備選 |
| PRAW（Reddit） | Reddit API 客戶端 | [praw-dev/praw](https://github.com/praw-dev/praw) | BSD-2 | 2026-10 | 4.3k★ | **淘汰中** |

**Reddit（需要主管決定，PRD Q5）**：研究引述 2026-09-30 r/redditdev 與 r/modnews 的公告（[PPC Land 整理](https://ppc.land/reddit-to-halt-public-data-api-access-in-march-2027-as-14-000-apps-register/)），內容如下：

- 2026-10-31 起，不再受理新的公開 API 申請。
- RSS 在 2026-11-13 停止；當天實測 `/.rss` 已經回 403。
- 公開 API 在 2027-03 前關閉。

建議 Reddit 降為選用，改由電子報間接涵蓋 Reddit 上的討論。

**smol.ai AINews 換位置了**：`news.smol.ai/rss.xml` 最後一期停在 2026-09-09，新一期改發在 `latent.space/feed`。

**實務案例**
- [ai-news-brief](https://github.com/frankzch/ai-news-brief)：94 個以上 RSS、HN、HF、GitHub Trending，全部自寫 Python fetcher。
- [AI News Pipeline 教學](https://learn.aguidetocloud.com/playground/ai-news-pipeline/)：34 個 RSS，放在排程上跑，去重用 MD5 加上 Jaccard 相似度。

**坑**：部分電子報 feed 的日期混亂（例如混入 2018 年的舊日期），要用 guid 去重；社群維護的 HF feed 會隨 HF 改版失效。

**Spike → SPK-COL-3**：40 個 feed 跑 7 天，統計失效率與延遲，同時比對 AINews 和 X List 的題目重疊率。過關條件：失效率 < 5%。

### 1.4 沒有 RSS 的頁面：變動監測

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| changedetection.io（Docker） | 監測 changelog、定價頁、文件頁；可用 CSS／XPath 篩選 | [dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Apache-2.0 | 2026-10-12 | 35.2k★ | **採用** |
| sockpuppetbrowser | 搭配上面處理需要跑 JS 的頁面 | [dgtlmoon/sockpuppetbrowser](https://github.com/dgtlmoon/sockpuppetbrowser) | Apache-2.0 | 2026-09 | 145★ | **採用（只用在需要 JS 的頁面）** |
| Apprise | 通知轉發，`ntfy://` 等管道 | [caronc/apprise](https://github.com/caronc/apprise) | BSD-2 | 2026-10 | 17.6k★ | **採用** |
| urlwatch | 命令列版本，直接放進 cron | [thp/urlwatch](https://github.com/thp/urlwatch) | BSD 系 | 2026-07 | 3.1k★ | 備援 |

**實務案例**：[moddedbear](https://moddedbear.com/how-i-watch-for-website-updates/) 用 changedetection.io 搭配 ntfy。**坑**：頁面上的時間戳、隨機 ID 會造成假警報，一定要設篩選器。

**Spike → SPK-COL-4**：監測 20 個頁面 14 天。過關條件：假警報 ≤ 1 次／頁／週；真實變動 30 分鐘內收到通知。

### 1.5 文章全文擷取

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| trafilatura | 主力：本地抽取正文與 metadata | [adbar/trafilatura](https://github.com/adbar/trafilatura) | Apache-2.0 | 2026-10 | 7k★ | **採用** |
| curl_cffi | 模擬瀏覽器 TLS 指紋，先通過反爬，再交給 trafilatura | [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi) | MIT | 未核實 | — | **採用** |
| Jina Reader（r.jina.ai） | 備援 1：處理需要跑 JS 的頁面；免費 token 限非商業用途 | [jina-ai/reader](https://github.com/jina-ai/reader) | Apache-2.0（程式碼） | 2026-05 | 12k★ | **採用（備援）** |
| Crawl4AI | 備援 2：自架的 JS 渲染＋抽取 | [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | Apache-2.0 | 2026-10 | 86k★ | **需 Spike** |
| Firecrawl | 託管服務最完整；自架需要 AGPL、Redis、Chromium | [firecrawl/firecrawl](https://github.com/firecrawl/firecrawl) | AGPL-3.0 | 2026-06 | 194k★ | 備選 |

**實務案例**：ai-news-brief 用三層退路：trafilatura → curl_cffi → 瀏覽器；學術評測（[SIGIR 2023](https://webis.de/downloads/publications/slides/bevendorff_2023b.pdf)）認為 Readability 與 trafilatura 最穩定。**坑**：中文網站的日期常抽錯；各家評測數字無法互比。

**Spike → SPK-FET-1**：50 篇真實來源人工標註正文。過關條件：trafilatura 單獨 ≥ 85%，加上 Jina 補到 ≥ 95%。

### 1.6 影片、圖片下載與瀏覽器錄影

| 層 | 工具 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| L1 | X API `variants`；Threads API `media_url`（未核實） | — | — | — | — | **需 Spike**（跟 SPK-COL-1 一起測） |
| L2 | selectolax／trafilatura 解析 `<video>`、`og:video` | [rushter/selectolax](https://github.com/rushter/selectolax) | MIT | — | — | **採用** |
| L3 | yt-dlp＋Deno（2025.11 起 YouTube 需要外部 JS runtime） | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)、[yt-dlp/ejs](https://github.com/yt-dlp/ejs) | Unlicense | 2026-10-21 | 200k★ | **採用** |
| L3 | gallery-dl（圖片與圖集；主力開發已移到 Codeberg） | [mikf/gallery-dl](https://github.com/mikf/gallery-dl) | GPL-2.0 | 2026-10-17 | 20k★ | **採用（只用在圖片）** |
| L4 | Playwright `page.screencast`（1.59 新增；1.64 可設 fps、VP9、1920×1080） | [release notes](https://playwright.dev/docs/release-notes) | Apache-2.0 | 2026-10-14 | 98k★ | **需 Spike** |
| L4 | shot-scraper video（用 YAML 描述操作步驟，輸出 mp4） | [simonw/shot-scraper](https://github.com/simonw/shot-scraper) | Apache-2.0 | 2026-10 | 2.6k★ | 備援 |
| L4 | 有頭 Chromium＋Xvfb＋ffmpeg x11grab，或 OBS（需要高畫質或聲音時） | [obsproject/obs-studio](https://github.com/obsproject/obs-studio) | GPL-2.0 | 2026-10 | 78k★ | 備援 |

**實務案例**
- [Simon Willison 2026-06](https://simonwillison.net/2026/Jun/30/shot-scraper-video/)：改用 Playwright 新的 screencast；早期版本開頭有白幀、寬度固定。
- [alexwlchan 2026-06](https://alexwlchan.net/2026/record-browser-video/)：放棄 `recordVideo`，原因是只錄 1x 像素密度、沒有聲音。

**坑**：yt-dlp 沒有 Deno 時，YouTube 下載會直接 403；yt-dlp 和 gallery-dl **都沒有 Threads 抽取器**；screencast 不錄聲音。

**Spike → SPK-FET-2**：3 個互動 Demo，比較 screencast（1080p、60fps、2x DPR）與 Xvfb＋x11grab。過關條件：轉成直式後小字可以閱讀；沒有超過 0.5 秒的卡頓。

### 1.7 網址正規化、去重、聚類、推播

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| courlan | 網址清洗、去追蹤參數（trafilatura 作者的套件） | [adbar/courlan](https://github.com/adbar/courlan) | Apache-2.0 | 2026-09 | 180★ | **採用** |
| tldextract | 網域解析 | [john-kurkowski/tldextract](https://github.com/john-kurkowski/tldextract) | BSD-3 | 2026-10 | 2k★ | **採用** |
| RapidFuzz | 標題模糊比對 | [rapidfuzz/RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | MIT | 2026-10 | 4.2k★ | **採用** |
| sentence-transformers＋bge-m3 | 中英混合的語意嵌入 | [sentence-transformers](https://github.com/huggingface/sentence-transformers)、[FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) | Apache／MIT | 2026-10 | 19k★／12k★ | **採用（門檻需 Spike）** |
| sqlite-vec | 向量直接存在 SQLite，不必另外架向量資料庫 | [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) | Apache-2.0 | 2026-05 | 8.2k★ | **採用** |
| datasketch（MinHash） | 大量近似重複的快速篩選 | [ekzhu/datasketch](https://github.com/ekzhu/datasketch) | MIT | 2026-10 | 3k★ | 備選 |
| ntfy（Docker 或公共伺服器） | 手機推播；一個 POST 就能推 | [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) | Apache-2.0 | 2026-10-23 | 35k★ | **採用** |
| Pushover／Telegram bot | iOS 送達率較穩的備援 | — | — | — | — | 備援 |

**實務案例**：[ai-news-brief](https://github.com/frankzch/ai-news-brief) 先用 SimHash 快篩，再用向量的餘弦相似度判斷（24 小時內的門檻較嚴）；[ai-news-aggregator](https://github.com/Santosh-S321/ai-news-aggregator) 以餘弦相似度 ≥ 0.88 判為重複。**坑**：「GPT-5.6」和「GPT 5.6」要先正規化；英文專用的嵌入模型處理中英混合會失準。

**Spike → SPK-COL-5**：人工標註 300 則同題分組，調整門檻（0.80–0.90）。過關條件：同題召回率 ≥ 90%，誤合併 ≤ 5%。同時用 iOS 和 Android 測 ntfy 的送達延遲。

---

## 2. 草稿與字幕（周長曉）

### 2.1 LLM 草稿

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| Anthropic Python SDK | 事實抽取（Citations）、結構化草稿（structured output）、prompt caching、Batches | [anthropics/anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python) | Apache-2.0 | 2026-10-22 | 4k★ | **採用** |
| Pydantic | 定義草稿、節奏表、圖卡、caption 的 schema | [pydantic/pydantic](https://github.com/pydantic/pydantic) | MIT | — | 大 | **採用** |
| Claude Code／Claude Agent SDK | slash command 入口、互動式改稿 | [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) | MIT | 2026-10-22 | 8.3k★ | **採用** |
| zhtw-mcp | 檢查台灣用語與標點（例如「軟件」改成「軟體」） | [sysprog21/zhtw-mcp](https://github.com/sysprog21/zhtw-mcp) | MIT | 2026-10-11 | 494★ | **需 Spike** |
| Instructor | 換 LLM 供應商時的統一結構化輸出層 | [567-labs/instructor](https://github.com/567-labs/instructor) | MIT | 2026-10-20 | 14k★ | 備援 |
| Pydantic AI | 日後真的需要多步驟 agent 時 | [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | MIT | 2026-10-23 | 20.8k★ | 備選 |
| LangChain／LangGraph／DSPy | 單人、檔案導向的產線用不到，多一層抽象 | — | MIT | — | 大 | 不採用 |

**為什麼拆兩次呼叫**：Claude 的 Citations 和 structured output 不能在同一個請求同時開啟（會回 400）。所以先抽事實，再依事實寫稿，最後用程式比對數字（SRS 04 §1）。

**實務案例**
- [longtermwiki 的 page-creator pipeline](https://www.longtermwiki.com/internal/reports/page-creator-pipeline)：研究 → `facts.json` → 改寫，規則是「不在 facts.json 的事實不准寫」，和我們的設計幾乎一樣。
- [claude-skill-social-post](https://github.com/Hao0321/claude-skill-social-post)：先讀 20 篇過去的貼文，抽出 style profile，再寫 Threads／IG 貼文。可參考它控制口吻的方式。

**坑**：
- 新模型不支援 assistant prefill，也不能強制 `tool_choice`，要固定格式只能靠 structured output。
- 中文數字（「八成」「1.5 億」）要先正規化才能比對。
- few-shot 一律用繁體，否則用詞會偏中國。
- prompt caching 是比對前綴，所以固定內容要放在最前面。

**Spike → SPK-DRF-1／2／3**：引用準確度、口吻盲測、成本，條件見 SRS 04 §4。

### 2.2 繁中語音轉文字與逐字時間

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| Qwen3-ForcedAligner-0.6B | **主力對齊**：把已知講稿對到錄音，取得每字時間（單段 ≤ 5 分鐘） | [HF 模型卡](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) | Apache-2.0 | 2026-01 | 約 40 萬下載 | **需 Spike** |
| Breeze-ASR-25（聯發科） | **主力辨識**：台灣華語＋中英夾雜，直接輸出繁體；用來比對有沒有念錯，也處理沒有稿子的素材 | [HF](https://huggingface.co/MediaTek-Research/Breeze-ASR-25)、[GitHub](https://github.com/mtkresearch/Breeze-ASR-25) | Apache-2.0 | 2025-07（之後沒更新） | 2.5 萬下載 | **需 Spike** |
| faster-whisper | Breeze 的推論引擎；CPU 可以跑 int8；`word_timestamps`、`vad_filter` | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | 2026-10-16 | 26k★ | **採用** |
| WhisperX | 備援對齊（社群最大）；中文對齊模型是簡體，要先轉簡體對齊再貼回繁體 | [m-bain/whisperX](https://github.com/m-bain/whisperX) | BSD-2 | 2026-10-22 | 24.8k★ | 備援 |
| Qwen3-ASR-1.7B | 備援辨識：辨識率較高，但輸出簡體，要接 OpenCC | [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | Apache-2.0 | 2026-06 | 3.8k★ | 備援 |
| Montreal Forced Aligner | 老牌對齊工具；安裝要用 conda | [MontrealCorpusTools/Montreal-Forced-Aligner](https://github.com/MontrealCorpusTools/Montreal-Forced-Aligner) | MIT | 2026-10 | 1.9k★ | 備選 |
| stable-ts | — | [jianfch/stable-ts](https://github.com/jianfch/stable-ts) | — | **已封存** | — | 不採用 |
| ctc-forced-aligner 預設模型 | 模型授權 CC-BY-NC-4.0，不能商用 | [MahmoudAshraf97/ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner) | 程式 BSD-2／模型 NC | 2026-09 | 571★ | 不採用 |

**為什麼改用強制對齊**：05 文件原本讓 ASR 負責每字時間。但自錄旁白有講稿，用對齊把講稿貼上時間，比 ASR 自己猜更準，也不會把辨識錯字帶進字幕。

**第三方評測**（[TEA-ASR-1 模型卡](https://huggingface.co/JacobLinCool/TEA-ASR-1)，MER 越低越好）：

| 測試集 | Breeze | Qwen3-ASR | Whisper-large-v3 |
|---|---|---|---|
| CommonVoice zh-TW | 8.03 | 3.90 | 10.17 |
| 台灣課程錄音 | **7.50** | 10.12 | — |

Breeze 在台灣口音的錄音上表現較好。

**實務案例**：[claude-shorts](https://github.com/AgriciDaniel/claude-shorts)：Claude Code＋faster-whisper＋Remotion＋FFmpeg，Windows 上透過 WSL2 執行，跟我們的架構幾乎一樣。

**坑**：
- Whisper 系列在靜音段會出現「謝謝收看」之類的幻聽句子，一定要開 VAD。
- Breeze 社群轉的 CT2 格式下載量很少，建議用官方轉換器自己轉。
- Qwen3-ForcedAligner 能不能在 CPU 上跑，還沒有驗證。

**Spike → SPK-SUB-1／2／3**：辨識率、對齊準度、速度，條件見 SRS 05 §6。

### 2.3 繁中斷詞與字幕分行

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| CKIP Transformers | 繁中斷詞 | [ckiplab/ckip-transformers](https://github.com/ckiplab/ckip-transformers) | **GPL-3.0** | 2023-04（停滯但堪用） | 785★ | **需 Spike** |
| BudouX（zh-hant） | 找出不會切斷詞的換行點；Python 與 JS 都有 | [google/budoux](https://github.com/google/budoux) | Apache-2.0 | 2026-10-22 | 1.8k★ | **採用** |
| glossary 強制合併（自寫） | 斷詞後用最長匹配，把術語、產品名、數字加單位合成一個詞 | — | — | — | — | **採用** |
| Jieba（繁體詞典） | CKIP 太慢或沒有 GPU 時的備援 | [fxsjy/jieba](https://github.com/fxsjy/jieba) | MIT | 2024-08 | 35k★ | 備援 |
| OpenCC | 簡轉繁，只用在 ASR 輸出簡體時 | [BYVoid/OpenCC](https://github.com/BYVoid/OpenCC) | Apache-2.0 | 2026-10-23 | 10k★ | **採用（只在需要時）** |

**坑**：
- 講稿本來就是繁體時，**絕對不要再跑 OpenCC**（`s2twp` 會改用詞）。
- BudouX 只給片語邊界，每行字數的規則要自己寫；它的 HTML 模式會插入零寬空白，要清掉。
- CKIP 是 GPL-3.0，內部使用沒有問題。

**Spike → SPK-SUB-4**。

### 2.4 字幕格式與 Remotion 接法

| 工具 | 角色 | 連結 | 標記 |
|---|---|---|---|
| `@remotion/captions` 的 `Caption` 型別 | captions.json 的欄位對齊 Remotion 型別；**中文分頁自己寫**，因為 `createTikTokStyleCaptions` 是靠空白分詞 | [docs](https://www.remotion.dev/docs/captions/create-tiktok-style-captions) | **採用** |
| remotion-dev/template-tiktok | 官方範本，可參考逐詞字幕的做法 | [repo](https://github.com/remotion-dev/template-tiktok) | 參考 |
| pysubs2 | Python 端輸出 SRT／ASS | [tkarabela/pysubs2](https://github.com/tkarabela/pysubs2) | **採用** |

**坑**：中文字型要事先載入，否則第一格會出現方框；要設 `white-space: pre`。

### 2.5 TTS（選用，R6）

| 工具 | 角色 | 連結 | 授權 | 標記 |
|---|---|---|---|---|
| BreezyVoice（聯發科） | 台灣華語，可用注音指定破音字，CPU 可以跑 | [mtkresearch/BreezyVoice](https://github.com/mtkresearch/BreezyVoice) | Apache-2.0（2025-06 後少更新） | **需 Spike** |
| Azure Speech zh-TW 神經語音 | 正式的付費服務，可以用 SSML 調讀音 | — | 商用 | **需 Spike** |
| CosyVoice 3／Qwen3-TTS | 社群大，但台灣口音未驗證 | [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice)、[QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | Apache-2.0 | 備選 |
| edge-tts | 非官方，服務條款有疑慮 | [rany2/edge-tts](https://github.com/rany2/edge-tts) | — | 不採用 |

**Spike → SPK-TTS-1**。只使用創作者自己的聲音。

---

## 3. 剪輯與製作（周長曉）

### 3.1 程式化影片合成

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| Remotion | 主力合成：字幕、2D、圖表、最終輸出（React，以 frame 驅動） | [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | Remotion License（POC 不處理，見 §6） | 2026-10-22 | 66.4k★ | **採用** |
| remotion-dev/skills | 給 Claude Code 用的 Remotion 技能 | [repo](https://github.com/remotion-dev/skills) | — | 2026-10-22 | 5.5k★ | **採用** |
| HyperFrames（HeyGen） | 技術備援，也是商用時的授權備援：HTML／GSAP 寫動畫，headless Chrome＋ffmpeg 輸出；官方有 Remotion 轉換技能 | [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) | Apache-2.0 | 2026-10-23 | 70k★ | **需 Spike** |
| ffmpeg | 素材正規化（30fps、yuv420p）、拼接、faststart | [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | LGPL／GPL | 2026-10 | 65.6k★ | **採用** |
| MoviePy | 純 Python 的簡單拼接 | [Zulko/moviepy](https://github.com/Zulko/moviepy) | MIT | 2026-08 | 15k★ | 備選 |
| Motion Canvas／Revideo | 和 Remotion 功能重疊 | [motion-canvas](https://github.com/motion-canvas/motion-canvas)、[midrender/revideo](https://github.com/midrender/revideo) | MIT | 2026-07 | 19.4k★／4.1k★ | 備選 |
| editly | 已停滯 | [mifi/editly](https://github.com/mifi/editly) | MIT | 2025-05 | 5.5k★ | 不採用 |

**實務案例**
- [cutback 2026-10-17](https://cutback.video/blog/claude-for-video-editing-in-2026-what-works-what-breaks-and-the-real-pipeline)：Claude 適合做圖層與動態圖形，真人素材的剪點不可靠（11 個剪點都切在句中）。這支持我們「依字幕時間決定剪點」的設計。
- [dev.to FreeFaceless](https://dev.to/nils44344/how-i-built-a-free-self-hosted-pipeline-that-auto-generates-faceless-youtube-shorts-3je4)：純 Python＋ffmpeg 的短影音產線；Windows 防毒軟體攔截 TLS 時，要用 `truststore` 解決。

**Spike → SPK-PRD-1**：見 SRS 05 §6。

### 3.2 輪播出圖（1080×1350）

| 工具 | 角色 | 連結 | 授權 | 最後更新 | 社群 | 標記 |
|---|---|---|---|---|---|---|
| Playwright（Python） | 主力：HTML／CSS 模板截圖、溢版檢查 | [microsoft/playwright](https://github.com/microsoft/playwright) | Apache-2.0 | 2026-10-22 | 98.3k★ | **採用** |
| fontTools（pyftsubset） | 繁中字型子集化 | [fonttools/fonttools](https://github.com/fonttools/fonttools) | MIT | 2026-10 | 5.3k★ | **採用** |
| Remotion `renderStill` | 想讓圖卡與影片共用同一套元件時 | [template-still](https://github.com/remotion-dev/template-still) | Remotion License | 2026-10 | — | 備選 |
| Satori | 不是完整的 CSS 實作，沒有 `line-break`，做不到避頭尾 | [vercel/satori](https://github.com/vercel/satori) | MPL-2.0 | 2026-10 | 14.1k★ | 不採用 |
| Bannerbear／Placid | SaaS 出圖 | — | 商用 | — | — | 備選 |
| ig-card-generator | 參考它的檢查規則 | [dean9703111/ig-card-generator](https://github.com/dean9703111/ig-card-generator) | MIT | 2026-07 | 14★ | 參考 |

**實務案例**：[n8n 社群的輪播自動化](https://community.n8n.io/t/i-automated-instagram-carousel-creation-with-n8n-one-telegram-message-in-6-finished-slides-out/278336) 用 HTML 模板＋headless 瀏覽器渲染。踩到兩個坑：`browser.close()` 會卡死（要加逾時強制結束）；同時載入多個字重檔會隨機卡住（改用單一可變字型）。

**坑**：IG API 只接受 JPEG；一組最多 20 張，第一張的比例決定整組。

**Spike → SPK-CAR-1**。

### 3.3 動畫與素材

| 類別 | 主力 | 連結 | 授權 | 標記 |
|---|---|---|---|---|
| 2D 動態 | Remotion 官方套件＋GSAP（以 seek 對齊 frame） | [greensock/GSAP](https://github.com/greensock/GSAP)（29.6k★） | GSAP 標準授權 | **採用** |
| Lottie | @remotion/lottie；新版播放器 dotlottie-web | [LottieFiles/dotlottie-web](https://github.com/LottieFiles/dotlottie-web) | MIT | **採用** |
| Demo 包裝 | snapcn（Remotion 元件：裝置外框、終端機、文字揭示） | [shadcn-labs/snapcn](https://github.com/shadcn-labs/snapcn)（505★） | MIT | **採用** |
| 程式碼動畫 | code-hike＋template-code-hike；`@shikijs/magic-move`（原 shiki-magic-move 已封存） | [code-hike/codehike](https://github.com/code-hike/codehike)、[template-code-hike](https://github.com/remotion-dev/template-code-hike)、[shikijs/shiki](https://github.com/shikijs/shiki) | MIT | **採用** |
| 3D | @remotion/three＋React Three Fiber | [pmndrs/react-three-fiber](https://github.com/pmndrs/react-three-fiber)（33.2k★）、[template-three](https://github.com/remotion-dev/template-three) | MIT | **需 Spike**（沒有 GPU 時很慢） |
| 說明動畫 | Manim Community（輸出外部片段） | [ManimCommunity/manim](https://github.com/ManimCommunity/manim)（41.8k★） | MIT | 備選 |
| 錄影游標放大 | Cap；CursorLens 的活躍分支（openscreen 已封存） | [CapSoftware/Cap](https://github.com/CapSoftware/Cap)、[blueberrycongee/CursorLens](https://github.com/blueberrycongee/CursorLens) | 混合 | 備選 |
| AI B-roll（雲端） | Higgsfield（已有 MCP），底層可選 Kling 3.0／Veo 3.1 | — | 商用 | **需 Spike** |
| AI B-roll（本機） | ComfyUI＋Wan2.2／LTX-2（確認 GPU 後） | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI)、[Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2)、[Lightricks/LTX-2](https://github.com/Lightricks/LTX-2) | GPL-3.0／Apache-2.0 | 備選 |

**坑**：繁中 3D 文字要子集化；Lottie 的文字圖層要先轉成 shape；AI 影片多為 16 或 24fps，要轉成 30fps。**Spike → SPK-REEL-2、SPK-REEL-3**。

### 3.4 規格檢查與編碼

| 工具 | 角色 | 標記 |
|---|---|---|
| ffprobe（`-print_format json`）＋自寫 Python 檢查器 | 斷言 codec、pix_fmt、fps、色彩、音訊、moov 位置、檔案大小 | **採用** |
| ffmpeg-python | 2024-08 後停滯；直接用 subprocess 呼叫即可 | 不採用 |

統一輸出：1080×1920、30fps CFR、H.264 High、yuv420p、BT.709、AAC 48 kHz、`+faststart`。**坑**：AI 素材是 VFR（可變幀率）時，混進來會讓音畫不同步。**Spike → SPK-REEL-1**。

---

## 4. 發布與回饋（楊舒媁）

### 4.1 發布

| 管道 | 角色 | 關鍵事實 | 標記 |
|---|---|---|---|
| 平台原生排程 | R1–R5 的人工發布路線 | Threads App 可以排程到 75 天後 | **採用（R1）** |
| IG 官方 API（Instagram Login） | 自動發布 Reels、輪播 | 需要專業帳號＋`instagram_business_content_publish`；**媒體必須是公開網址**；每日上限官方文件同時出現 100 和 50 兩個數字（互相矛盾） | **採用（R6）** |
| Threads 官方 API | 自動發布文字、圖片、影片 | 每 24 小時 250 則；輪播算 1 則；媒體也需要公開網址 | **採用（R6）** |
| YouTube Data API | 自動發布 Shorts | **未通過稽核的專案，上傳的影片一律設為私人** | **需 Spike** |
| TikTok Content Posting API | 自動發布 | **未通過稽核只能私人可見**；稽核約 2–6 週（第三方說法） | 備選 |
| Upload-Post | 聚合服務，YouTube／TikTok 不必自己通過稽核 | 免費 2 個 profile、每月 10 次；付費約每月 16–24 美元起（第三方資料）；找不到官方 GitHub | **需 Spike** |
| Blotato | 聚合＋生成；有 n8n node 與 skills | 每月 29 美元 | 備選 |
| Postiz（自架） | 開源排程器 | [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app)（37.6k★，AGPL-3.0）；v2.12 起需要 Temporal；**自架仍然要自己的平台 App 與稽核** | 備選 |
| Ayrshare／Buffer API／Mixpost | 其他聚合方案 | 價格說法不一致；Buffer 新 API 是 beta | 備選 |

**實務案例**：[n8n 輪播案例](https://community.n8n.io/t/i-automated-instagram-carousel-creation-with-n8n-one-telegram-message-in-6-finished-slides-out/278336) 先在 Telegram 預覽，人工核准後才發布；[Buffer 2026-07 事故](https://pulsetic.com/status/buffer/incidents/5668/)：Meta API 出問題時，聚合服務一樣發不出去。**坑**：聚合服務的比較文多半是競品自己寫的。

**Spike → SPK-PUB-1、SPK-PUB-2**。

### 4.2 媒體暫存公開網址

| 工具 | 角色 | 標記 |
|---|---|---|
| 物件儲存＋有時效的預簽網址（例如 Cloudflare R2；S3 相容） | IG／Threads API 要求媒體是公開網址 | **需 Spike（SPK-PUB-3）** |

### 4.3 成效回收

| 平台 | 做法 | 坑 | 標記 |
|---|---|---|---|
| IG Insights | 以 `views` 為主要指標 | `plays`、`impressions` 等已經棄用：查 2024-07 之後發布的貼文會直接報錯（[changelog](https://developers.facebook.com/docs/instagram-platform/changelog)）；網路上的舊教學還在用 `plays` | **採用** |
| Threads Insights | views、likes、replies、reposts、quotes、shares；需要 `threads_manage_insights` | 不指定時間範圍時，預設只回 2 天的資料 | **採用** |
| YouTube Analytics API | `reports.query` | Shorts 的觀看數計算方式曾經改變（未核實） | **需 Spike** |

儲存方式：每次回收寫一筆 append-only 的 snapshot，並記錄指標版本（`metric_version`），因為新舊指標不能直接比較。**Spike → SPK-FB-1**。

---

## 5. 協調、排程與基礎設施（楊舒媁／Albert Chiang）

| 工具 | 角色 | 連結 | 授權 | 標記 |
|---|---|---|---|---|
| Python 3.11+＋poetry | 主程式 | — | — | **採用** |
| Typer | CLI 框架 | [fastapi/typer](https://github.com/fastapi/typer) | MIT | **採用** |
| SQLite（WAL）＋自寫 migration | 執行紀錄的主資料 | — | — | **採用** |
| Pydantic | 設定與 schema 驗證 | — | MIT | **採用** |
| jsonschema | 07 介面契約的驗證 | [python-jsonschema/jsonschema](https://github.com/python-jsonschema/jsonschema) | MIT | **採用** |
| systemd timer（WSL）／cron | 排程 | — | — | **採用** |
| Node（nvm）＋TypeScript | 渲染端 | — | — | **採用** |
| Docker | changedetection.io、ntfy（自架時） | — | — | **採用** |
| pytest＋錄製的 HTTP 回應（例如 vcrpy／respx） | 離線測試（NFR-014） | [kevin1024/vcrpy](https://github.com/kevin1024/vcrpy)、[lundberg/respx](https://github.com/lundberg/respx) | MIT／BSD | **採用** |
| n8n（自架） | 只在「通知＋核准 UI」成為痛點時才加，且不持有流程狀態 | [n8n-io/n8n](https://github.com/n8n-io/n8n)（208k★，Sustainable Use License） | fair-code | 備選 |
| Temporal／Prefect／Dagster | 單人使用太重 | — | — | 不採用 |

n8n 和自寫 Python 的比較見 [架構文件 ADR-02](03_Architecture.md#adr-02協調器自寫-python不用-n8n-當主幹)。

---

## 6. Remotion 授權（POC 階段不處理，轉商用前再評估）

**2026-10-23 決定**：目前是內部 POC，不對外販售；授權問題留到商用前再評估（PRD D14）。以下事實留作轉商用時的參考。

| 事實 | 出處 |
|---|---|
| 免費條件是營利組織「最多 3 名員工」 | [LICENSE.md](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md) |
| 4 人以上的合作或公司需要 Company License | [remotion.pro/license](https://www.remotion.pro/license) |
| 用程式自動呼叫渲染屬於 **Automator** 方案：每次渲染 0.01 美元，每月最低 100 美元；做自動化的開發者不需要另外買 Seat | 同上 |
| Creator 方案每人每月 25 美元，只適用於沒有建立自動化的手動製作 | 同上 |
| 官方預告 Remotion 5.0 授權會再調整 | 同上 |

轉商用時的兩條路：付費（Automator，每月約 100 美元起），或改用 HyperFrames（Apache-2.0，官方提供從 Remotion 轉換的技能，宣稱能轉約 80% 的內容）。IF-013 已把渲染器隔離，換掉不影響 Python 端。

---

## 7. Spike 總表

每個 Spike 都是一張票，結論寫在 `docs/spikes/`。

| Spike | 主題 | 票 | 擁有者 | Sprint |
|---|---|---|---|---|
| SPK-OPS-1 | 環境：GPU、WSL systemd、Docker、ffmpeg | [T-004](tickets/T-004.md) | 楊舒媁 | S1 |
| SPK-FET-1 | 全文擷取 | [T-012](tickets/T-012.md) | Albert Chiang | S1 |
| SPK-PRD-1 | Remotion 技術驗證（HyperFrames 對照） | [T-014](tickets/T-014.md) | 周長曉 | S1 |
| SPK-DRF-1／2／3 | 引用準確度、口吻、成本 | [T-028](tickets/T-028.md) | 周長曉 | S3 |
| SPK-SUB-1～4 | ASR、對齊、速度、斷詞分行 | [T-046](tickets/T-046.md) | 周長曉 | S3 |
| SPK-CAR-1 | 輪播渲染與溢版 | [T-043](tickets/T-043.md)（併入驗收） | 周長曉 | S3 |
| SPK-COL-2 | Meta App 與 Threads 搜尋權限（**提早送審**） | [T-041](tickets/T-041.md) | 楊舒媁 | S3 |
| SPK-COL-3 | feed 可靠度＋AINews 重疊率 | [T-040](tickets/T-040.md) | Albert Chiang | S4 |
| SPK-COL-1 | X API 成本＋L1 媒體 | [T-059](tickets/T-059.md) | Albert Chiang | S4 |
| SPK-COL-5 | 聚類門檻＋推播送達 | [T-060](tickets/T-060.md) | Albert Chiang | S4 |
| SPK-FB-1 | 成效回收 | [T-081](tickets/T-081.md) | 楊舒媁 | S4 |
| SPK-REEL-1 | 平台壓縮後的字幕清晰度 | [T-071](tickets/T-071.md) | 周長曉 | S5 |
| SPK-PUB-1／2／3 | 官方 API 發布、聚合服務、暫存網址 | [T-085](tickets/T-085.md) | 楊舒媁 | S5 |
| SPK-COL-4 | 變動監測 | [T-105](tickets/T-105.md)（併入） | Albert Chiang | S6 |
| SPK-REEL-2／3 | 3D 效能、AI B-roll | [T-097](tickets/T-097.md) | 周長曉 | S6 |
| SPK-FET-2 | 瀏覽器錄影畫質 | [T-106](tickets/T-106.md)（併入） | Albert Chiang | S7 |
| SPK-TTS-1 | TTS | [T-100](tickets/T-100.md)（併入） | 周長曉 | S7 |

---

## 8. 對 05 文件的修正與失效連結

| 05 文件寫的 | 研究結果 |
|---|---|
| Remotion「4 人以上才需要購買」 | 我們剛好 4 人，落在付費範圍；POC 階段不處理（§6） |
| X API 每月 15–50 美元 | 依官方按量計費推估，每月約 90–225 美元（§1.1） |
| Threads 關鍵字搜尋每 7 天約 500 次 | 官方文件是每 24 小時 2,200 次，但要通過審核（§1.2） |
| Reddit 官方 API／RSS | 即將關閉（§1.3） |
| Breeze-ASR 負責每字時間 | 改由強制對齊負責，ASR 用來比對（§2.2） |
| `createTikTokStyleCaptions` 搭配斷詞 | 這個函式靠空白分詞，中文分頁要自己寫（§2.4） |
| HTML／CSS 模板＋Playwright 截圖 | 維持，研究結果支持（§3.2） |
| shiki-magic-move | 已封存，改用 `@shikijs/magic-move` |
| OpenScreen | 已封存 |
| comfyanonymous/ComfyUI | 已轉址到 Comfy-Org/ComfyUI |
| redotvideo/revideo | 已轉址到 midrender/revideo |
| news.smol.ai/rss.xml | 停在 2026-09-09，改用 latent.space/feed |
| zedeus/nitter | 已封存，服務已下線 |
| stable-ts | 已封存 |
| ffmpeg-python、editly、ShortGPT | 停滯 |
