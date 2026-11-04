# MediaHOT

AI Media Studio 內容產線的**素材收集**部分：每天從一批 AI 與科技信源收資料，聚成事件、排出熱度，整理成每日 inbox；創作者勾選題目後，自動建題、擷取原文、盤點素材，交給後面的寫稿與製作。

這是 Albert Chiang 負責的模組；**收集、補料、inbox 的程式現在是 Python**（`../src/newsroom/`，見下方〈Python 的部分〉），這個資料夾保留 AIHOT 的網站與後端。**目前做到哪、哪些沒驗證、跟規格差在哪：看 [PROGRESS.md](PROGRESS.md)。** 整個專案的需求、介面契約與分工見外層的 [../README.md](../README.md) 與 [../docs/](../docs/)。

## 基於 AIHOT

MediaHOT 是 [AIHOT](https://github.com/KKKKhazix/AIHOT) 開源引擎（MIT 授權，Copyright (c) 2026 數字生命卡茲克）的修改版。信源收集、去重、事件歸組、評分精選、熱度榜、網站與後臺都來自 AIHOT；原作者的版權宣告保留在 [LICENSE](LICENSE) 與 [NOTICE](NOTICE)。AIHOT 的名稱與 Logo 不在授權內，這個專案不使用它們。

## 我們改了什麼

| 專案 | 內容 |
|---|---|
| 站名與設定 | 站名 MediaHOT、繁體中文（zh-TW）、爬蟲標識 `MediaHOTBot`（`site/site.ts`） |
| 信源 | 33 個：官方 AI blog、英文科技媒體、TLDR AI 與 AINews 電子報、Hacker News、Hugging Face 論文與熱門模型、8 個 GitHub release、iThome 與科技新報（`industry/sources.json`） |
| 精選標準 | 讀者設定成「經營 Threads／IG 的 AI 內容創作者」，加入開源工具與產業事件的加分、一般資安新聞的壓分（`industry/prompts/selection-score.md`） |
| 分類 | 六個分類的顯示名稱改成繁體（`industry/taxonomy.ts`） |
| **inbox 與建題** | 已改寫成 Python（`../src/newsroom/`）：每日 inbox、手動新增候選、跨日規則、建題與合併、原文擷取、素材盤點。詳見下表 |

提示詞與標籤詞表的其餘部分仍是簡體，門檻（`industry/selection.ts`）還沒有用我們標註的樣本校準。

## Python 的部分（收集、補料、inbox）

原本在 `modules/inbox` 的 TypeScript 已全部改寫成 Python，放在外層的 [../src/newsroom/](../src/newsroom/)，直接讀寫這個專案的 PostgreSQL（同一個資料庫、同一張 `schema_migrations`）。AIHOT 的網站、API、worker 照舊，只是不再載入 inbox 模組。改寫的對照與不同之處見 [../docs/process/python-port.md](../docs/process/python-port.md)。

安裝與使用（在外層 repo 根目錄）：

```bash
python -m pip install -e ".[dev]"      # 需要 Python 3.11 以上；補料還需要 ffmpeg，選用 yt-dlp、gallery-dl
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # AIHOT 的 migration 已套用過的資料庫
newsroom migrate                        # 建立 inbox／補料用的資料表
newsroom init ./newsroom                # 建立工作區；再設定 NEWSROOM_HOME
newsroom doctor                         # 檢查環境
newsroom run-scheduler                  # 常駐排程；--once 把此刻到期的跑一輪
```

用 Docker：`docker compose -f AIHOT/docker-compose.yml -f deploy/newsroom.yml up -d --build`（`deploy/newsroom.yml` 加上 `newsroom` 排程服務，與網站共用資料庫和 `data` 資料卷）。

`newsroom <工具>`，加 `--json` 時輸出依整個專案的工具介面（IF-020），結束碼：`0` 成功、`1` 失敗、`2` 輸入有誤、`3` 被阻擋（要人補資料）。

| 工具 | 做什麼 | 對應票 |
|---|---|---|
| `add-source <url> [--note --program --formats --title]` | 手動新增候選；同網址（任何寫法）只留一筆並附加備註 | T-011 |
| `backtest-heat <labels.json>` | 回測熱度分級：人工標記的 S 級事件，有多少被判為 S 或 A（目標 ≥ 80%） | T-075 |
| `write-inbox`；（排程）`inbox-write` | 寫 `inbox/YYYY-MM-DD.md`：熱點事件、手動新增、前幾天帶過來的；排程每 30 分鐘一次 | T-021 |
| `open-topic <cand_id>... [--program --formats --angle]` | 依勾選建立 topic 與各格式的 content，不重建已有的 | T-022 |
| `merge-candidates <保留> <併入>` | 把兩個候選合成一題 | T-022 |
| `fetch-topic <topic_id> [--force]` | 擷取每個來源的原文，並補上報導連到的官方來源 | T-023 |
| `fetch-media <topic_id> [--layers 1,2,3,4]` | 下載媒體：L1 平臺 API（X 貼文取最高位元率 mp4、Threads 取 og:video）→ L2 網頁直接給的影片、GIF、主視覺圖（遵守 robots.txt、每頁最多 10 個、單檔 500 MB）→ L3 yt-dlp、gallery-dl → L4 用瀏覽器錄 20 秒（1080p 30fps，題目有 reel 且前面都沒拿到影片才錄；需要 Playwright 的 Chromium，頁面沒載入或畫面大半是黑的就記成缺口，不產生空影片）。前一層拿到影片的來源，後面的層不再處理 | T-038、T-039、T-073、T-106 |
| `preprocess-topic <topic_id> [--video-only 或 --audio-only] [--lufs -14]` | 素材前處理：影片與 GIF 轉 30fps CFR yuv420p H.264；旁白轉 48kHz WAV、去頭尾靜音、響度 −14 LUFS；輸出到 `media/proc/`，已處理的不重做 | T-057 |
| `fetch-selftest`、`fetch-stats [--days 30]` | 補料自我測試（每層固定網址、檢查 yt-dlp 版本，每天 06:40 排程）、最近 30 天各層成功率 | T-056 |
| `assets-topic <topic_id> [--voiceover]` | 盤點 `media/` 的素材，寫 `assets.json` 與 `media.md`，列出缺口；新影片會做場景偵測，寫進 `usable_ranges_ms` | T-024、T-058 |
| `init [path]`、`doctor`、`migrate` | 建立工作區（只補缺、不覆蓋）、檢查環境並給修正建議、建立資料表 | T-008 |
| `collect-signals`；（排程）`signals-snapshot`、`newsletter-split` | 每小時記 HN 分數、GitHub star、HF likes 的時間序列；把電子報每一期拆成每則連結 | T-036、T-037 |
| `source-stats [--days 14]` | 各信源最近幾天的抓取成功率與最近一次錯誤（來自 `fetch_runs`） | T-040 |
| `set-title-zh --list` 或 `<cand_id> "<中文標題>" [--force]` | 列出還是外文標題的候選；替候選存中文標題（只翻一次），inbox 之後改顯示中文，沒有就顯示原文 | T-101 |
| （排程）`page-watch` | 每 10 分鐘讀自架的 changedetection.io（`deploy/changedetection.yml`），網頁有變動就在 inbox 加一則「<頁面> 有更新」候選，備註附差異；沒設定就略過，服務停了不影響其他來源 | T-105 |

另外：S 級候選第一次出現時通知一次（T-103，`FEISHU_INTERNAL_ENABLED=true` 且設了 `FEISHU_ALERT_WEBHOOK_URL` 才真的送，否則只記日誌）；S／A 級候選用 Threads 關鍵字搜尋查中文圈有沒有人報導，寫在 inbox 的「在地」一行（T-104，需要 `THREADS_ACCESS_TOKEN`，沒有就顯示「未檢查」）；X 貼文的互動數相對該帳號中位數的倍數是熱度訊號 `x_velocity`（預設關閉）。

相關 skill 在外層 [../skills/](../skills/)：`open-topic`、`gather-assets`、`triage-inbox`。

**發表會行事曆**：在工作區的 `calendar/events.md` 填表（日期 `YYYY-MM-DD`、時間 `10:00` 或 `10:00-12:30`、活動、關鍵字用逗號隔開、官方連結，臺北時間）。活動前一天 inbox 會多一則「準備：…」候選；活動結束 24 小時內，標題含關鍵字的新聞直接判為 S 級。表格有誤的列會被略過。活動前後把收集頻率改成每 5 分鐘還沒做。

**手動新增的短網址**（t.co、bit.ly 等）會先展開成真正的頁面再去重複；展開失敗就維持原網址。

**前置資訊讀寫**（`../src/newsroom/core/frontmatter.py`）：改指定欄位，其他欄位與內文原封不動。

**下載的限制**：YouTube 可以下載；Vimeo 與 X 推文目前需要登入（用自己帳號時，匯出 cookies 檔並設定 `YTDLP_COOKIES_FILE`）；網站擋下或 `robots.txt` 不允許的，只會成為素材缺口，不會繞過。需要先裝 `python -m pip install --user yt-dlp gallery-dl`。

**熱度分級**（`../src/newsroom/collect/heat.config.json`）：S ＝ 預告過的發表會剛結束、或 6 小時內 3 個以上來源共振、或分數 ≥ 70；A ＝ 2 個來源、或分數 ≥ 40；其餘 B。分數是共振、HN 增速、GitHub／HF 增速、電子報提及、來源權重加權（0 到 100），每個候選在 inbox 都列出分數明細與分級原因，同樣的輸入一定得到同樣的結果。X 互動與在地缺口目前停用（權重 0，不報錯）。**門檻是初始值，還沒有用人工標記的樣本校準**，要用 `backtest-heat` 調整。

**inbox 的規則**：人只改勾選、節目、格式、備註；程式只改受管區塊。沒勾的候選帶到隔天標「昨日」，超過 48 小時自動略過；勾了還沒建題的持續保留。

**原文擷取的順序**：GitHub、arXiv、YouTube 用各自的介面，其他網頁用 Readability（Python 版用 readability-lxml，比 AIHOT 的 TypeScript 版少了串流內容、隱藏元素、各站規則的處理），有設定 `JINA_API_KEY` 時再用 Jina Reader 備援，都失敗就 blocked 並請人把原文貼進 `sources/manual-1.md`。依據見 [../docs/spikes/SPK-FET-1.md](../docs/spikes/SPK-FET-1.md)。

## 本機開發（Windows）

網站與後端（TypeScript）需要 Node.js 24.22.1 以上、一個 PostgreSQL 17（用 Docker 跑最省事）；收集與補料（Python）需要 Python 3.11 以上與 ffmpeg（素材盤點要用 `ffprobe`）。

```bash
docker run -d --name mediahot-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v mediahot-pgdata:/var/lib/postgresql/data postgres:17
npm install
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # 先建好這個資料庫
node scripts/migrate.ts                   # AIHOT 的資料表
cd .. && python -m pip install -e ".[dev]" && newsroom migrate   # 收集／補料用的資料表
export NEWSROOM_HOME=./AIHOT/.data/workspace    # 工作區（inbox、topics）放哪裡，預設就是這個
```

測試：

```bash
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_test   # 名稱必須以 _test 或 _ci 結尾
cd AIHOT && npm run typecheck && npm test            # 網站與後端（TypeScript）
cd .. && python -m pytest tests                       # 收集與補料（Python）；沒有 DATABASE_URL 時，要資料庫的測試會略過
```

注意：完整的 `npm test` 在 Windows 上有 3 個 `SIGTERM during …` 測試會逾時（Windows 沒有真正的 SIGTERM），這與我們的改動無關，其他測試通過；並行建測試資料庫時偶爾有一個檔案互搶失敗，單獨跑都會通過（可用 `--test-concurrency=1`）。

## 環境變數（我們新增的）

| 變數 | 用途 |
|---|---|
| `NEWSROOM_HOME` | 工作區位置（預設 `AIHOT/.data/workspace`） |
| `INBOX_DIR` | inbox 放哪裡（預設工作區底下的 `inbox`） |
| `GITHUB_TOKEN`、`JINA_API_KEY` | 選用，擷取 GitHub 與備援讀取時用（引擎原有的憑證機制） |
| `CHANGEDETECTION_URL`、`CHANGEDETECTION_API_KEY` | 選用，自架 changedetection.io 的位置與金鑰；沒設定就不做網頁變動監測 |
| `THREADS_ACCESS_TOKEN` | 選用，Threads 關鍵字搜尋權杖（在地熱度檢查）；沒有就顯示「未檢查」 |
| `FEISHU_INTERNAL_ENABLED`、`FEISHU_ALERT_WEBHOOK_URL` | 選用，S 級通知送到飛書警報群；沒開就只記日誌 |
| `SOCIALDATA_API_KEY` | 選用，X 貼文影片補料（L1）與 AIHOT 的 X 收集器共用 |
| `YTDLP_COOKIES_FILE` | 選用，自己帳號的 cookies 檔路徑，給 yt-dlp、gallery-dl 用；不要放進版本控制 |

其餘設定見 [.env.example](.env.example)。

## 檔案

- 引擎怎麼運作、怎麼換行業：[docs/customize.md](docs/customize.md)、[docs/architecture.md](docs/architecture.md)、[docs/sources.md](docs/sources.md)、[docs/selection.md](docs/selection.md)、[docs/deploy.md](docs/deploy.md)（AIHOT 原作者的檔案，部分說法仍指向原專案）。
- 我們的需求與規格：[../docs/spec/02_collect.md](../docs/spec/02_collect.md)、[../docs/spec/03_fetch_assets.md](../docs/spec/03_fetch_assets.md)、[../docs/spec/07_interfaces.md](../docs/spec/07_interfaces.md)。

## 授權

MIT，見 [LICENSE](LICENSE)。引擎部分的版權屬於原作者；我們的修改一併以 MIT 發布。
