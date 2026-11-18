# 收集、補料、inbox：指令、安裝與環境變數

`newsroom` 指令的用法、手動安裝與跑測試、我們新增的環境變數。專案是什麼、文件怎麼找，看根目錄 [README.md](../../README.md)；在另一台電腦上從頭裝起來，看 [setup-new-machine.md](setup-new-machine.md)。

## 指令

原本在 `modules/inbox` 的 TypeScript 已全部改寫成 Python，放在根目錄的 [src/newsroom/](../../src/newsroom/)，直接讀寫這個專案的 PostgreSQL（同一個資料庫、同一張 `schema_migrations`）。AIHOT 的網站、API、worker 照舊，只是不再載入 inbox 模組。改寫的對照與不同之處見 [python-port.md](python-port.md)。

安裝與使用（在 repo 根目錄）：

```bash
python -m pip install -e ".[dev]" -c constraints.txt      # 需要 Python 3.12 以上；補料還需要 ffmpeg，選用 yt-dlp、gallery-dl
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

相關 skill 在根目錄的 [skills/](../../skills/)：`open-topic`、`gather-assets`、`triage-inbox`。

**發表會行事曆**：在工作區的 `calendar/events.md` 填表（日期 `YYYY-MM-DD`、時間 `10:00` 或 `10:00-12:30`、活動、關鍵字用逗號隔開、官方連結，臺北時間）。活動前一天 inbox 會多一則「準備：…」候選；活動結束 24 小時內，標題含關鍵字的新聞直接判為 S 級。表格有誤的列會被略過。活動前後把收集頻率改成每 5 分鐘還沒做。

**手動新增的短網址**（t.co、bit.ly 等）會先展開成真正的頁面再去重複；展開失敗就維持原網址。

**前置資訊讀寫**（`src/newsroom/core/frontmatter.py`）：改指定欄位，其他欄位與內文原封不動。

**下載的限制**：YouTube 可以下載；Vimeo 與 X 推文目前需要登入（用自己帳號時，匯出 cookies 檔並設定 `YTDLP_COOKIES_FILE`）；網站擋下或 `robots.txt` 不允許的，只會成為素材缺口，不會繞過。需要先裝 `python -m pip install --user yt-dlp gallery-dl`。

**熱度分級**（`src/newsroom/collect/heat.config.json`）：S ＝ 預告過的發表會剛結束、或 6 小時內 3 個以上來源共振、或分數 ≥ 70；A ＝ 2 個來源、或分數 ≥ 40；其餘 B。分數是共振、HN 增速、GitHub／HF 增速、電子報提及、來源權重加權（0 到 100），每個候選在 inbox 都列出分數明細與分級原因，同樣的輸入一定得到同樣的結果。X 互動與在地缺口目前停用（權重 0，不報錯）。**門檻是初始值，還沒有用人工標記的樣本校準**，要用 `backtest-heat` 調整。

**inbox 的規則**：人只改勾選、節目、格式、備註；程式只改受管區塊。沒勾的候選帶到隔天標「昨日」，超過 48 小時自動略過；勾了還沒建題的持續保留。

**原文擷取的順序**：GitHub、arXiv、YouTube 用各自的介面，其他網頁用 Readability（Python 版用 readability-lxml，比 AIHOT 的 TypeScript 版少了串流內容、隱藏元素、各站規則的處理），有設定 `JINA_API_KEY` 時再用 Jina Reader 備援，都失敗就 blocked 並請人把原文貼進 `sources/manual-1.md`。依據見 [SPK-FET-1](../spikes/SPK-FET-1.md)。

## 手動安裝與跑測試（Windows）

網站與後端（TypeScript）需要 Node.js 24.22.1 以上、一個 PostgreSQL 17（用 Docker 跑最省事）；收集與補料（Python）需要 Python 3.12 以上與 ffmpeg（素材盤點要用 `ffprobe`）。

下面的指令從 `AIHOT/` 資料夾開始（`npm install`、`node scripts/migrate.ts` 在那裡跑），中途 `cd ..` 回到根目錄。

```bash
docker run -d --name mediahot-pg --shm-size=256m -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v mediahot-pgdata:/var/lib/postgresql/data postgres:17
npm install
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # 先建好這個資料庫
node scripts/migrate.ts                   # AIHOT 的資料表
cd .. && python -m pip install -e ".[dev]" -c constraints.txt && newsroom migrate   # 收集／補料用的資料表
export NEWSROOM_HOME=./AIHOT/.data/workspace    # 工作區（inbox、topics）放哪裡，預設就是這個
```

測試：

```bash
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_test   # 名稱必須以 _test 或 _ci 結尾
cd AIHOT && npm run typecheck && npm test            # 網站與後端（TypeScript）
cd .. && python -m pytest tests                       # 收集與補料（Python）；沒有 DATABASE_URL 時，要資料庫的測試會略過
```

想知道哪些程式沒有被測試跑到：`python -m pytest tests --cov=newsroom`（2026-11-15 含 Node 對照測試是 86%）。

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
| `RAG_INDEX_ENABLED` | 選用，`true` 時 worker 每 10 分鐘把新文章轉成向量；`start_dev.py` 預設打開，其他環境預設關著 |
| `RAG_EMBED_URL`、`RAG_EMBED_MODEL`、`RAG_CHAT_MODEL`、`RAG_MIN_SCORE` | 選用，站內問答用的本機 Ollama 位置（預設 `http://127.0.0.1:11434`）、向量模型（`bge-m3`）、聊天模型（`qwen3.5:9b-ctx32k`）、答案採用的最低相似度（`0.6`） |
| `RAG_CHAT_PROVIDER` | 選用，聊天的回答由誰寫：`ollama`（預設）、`claude`、`openai`（任何 OpenAI 相容的服務）。填別的值會報錯，不會默默改用 Ollama。搜尋的向量模型不論選哪個都是本機的 `bge-m3`，所以 Ollama 要開著。雲端的會產生費用 |
| `RAG_CLAUDE_MODEL` | 選用，`RAG_CHAT_PROVIDER=claude` 時的模型（預設 `claude-opus-5-5`）；要先 `python -m pip install -e ".[claude]"`，金鑰用 `ANTHROPIC_API_KEY` |
| `RAG_OPENAI_BASE_URL`、`RAG_OPENAI_API_KEY`、`RAG_OPENAI_MODEL` | `RAG_CHAT_PROVIDER=openai` 時三個都要填：服務網址（例如 `https://api.openai.com/v1`）、金鑰、該服務的模型名稱 |

其餘設定見 [.env.example](../../AIHOT/.env.example)。
