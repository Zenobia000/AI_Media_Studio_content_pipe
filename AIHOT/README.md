# MediaHOT

AI Media Studio 內容產線的**素材收集**部分：每天從一批 AI 與科技信源收資料，聚成事件、排出熱度，整理成每日 inbox；創作者勾選題目後，自動建題、擷取原文、盤點素材，交給後面的寫稿與製作。

這是 Albert Chiang 負責的模組。整個專案的需求、介面契約與分工見外層的 [../README.md](../README.md) 與 [../docs/](../docs/)。

## 基於 AIHOT

MediaHOT 是 [AIHOT](https://github.com/KKKKhazix/AIHOT) 開源引擎（MIT 授權，Copyright (c) 2026 数字生命卡兹克）的修改版。信源收集、去重、事件歸組、評分精選、熱度榜、網站與後台都來自 AIHOT；原作者的版權聲明保留在 [LICENSE](LICENSE) 與 [NOTICE](NOTICE)。AIHOT 的名稱與 Logo 不在授權內，這個專案不使用它們。

## 我們改了什麼

| 項目 | 內容 |
|---|---|
| 站名與設定 | 站名 MediaHOT、繁體中文（zh-TW）、爬蟲標識 `MediaHOTBot`（`site/site.ts`） |
| 信源 | 33 個：官方 AI blog、英文科技媒體、TLDR AI 與 AINews 電子報、Hacker News、Hugging Face 論文與熱門模型、8 個 GitHub release、iThome 與科技新報（`industry/sources.json`） |
| 精選標準 | 讀者設定成「經營 Threads／IG 的 AI 內容創作者」，加入開源工具與產業事件的加分、一般資安新聞的壓分（`industry/prompts/selection-score.md`） |
| 分類 | 六個分類的顯示名稱改成繁體（`industry/taxonomy.ts`） |
| **inbox 與建題** | `modules/inbox`：每日 inbox、手動新增候選、跨日規則、建題與合併、原文擷取、素材盤點。詳見下表 |

提示詞與標籤詞表的其餘部分仍是簡體，門檻（`industry/selection.ts`）還沒有用我們標註的樣本校準。

## 工具

都在 `scripts/`，用 `node --env-file=.env scripts/<名稱>.ts` 執行。加 `--json` 時輸出依整個專案的工具介面（IF-020），結束碼：`0` 成功、`2` 輸入有誤、`3` 被阻擋（要人補資料）。

| 工具 | 做什麼 | 對應票 |
|---|---|---|
| `add-source.ts <url> [--note --program --formats --title]` | 手動新增候選；同網址（任何寫法）只留一筆並附加備註 | T-011 |
| （排程）`inbox-write` | 每 30 分鐘寫 `inbox/YYYY-MM-DD.md`：熱點事件、手動新增、前幾天帶過來的 | T-021 |
| `open-topic.ts <cand_id>... [--program --formats --angle]` | 依勾選建立 topic 與各格式的 content，不重建已有的 | T-022 |
| `merge-candidates.ts <保留> <併入>` | 把兩個候選合成一題 | T-022 |
| `fetch-topic.ts <topic_id> [--force]` | 擷取每個來源的原文，並補上報導連到的官方來源 | T-023 |
| `fetch-media.ts <topic_id> [--layers 2,3]` | 下載媒體：先收網頁直接給的影片、GIF、主視覺圖（遵守 robots.txt、每頁最多 10 個、單檔 500 MB），沒拿到影片的來源再用 yt-dlp、gallery-dl | T-038、T-039 |
| `assets-topic.ts <topic_id> [--voiceover]` | 盤點 `media/` 的素材，寫 `assets.json` 與 `media.md`，列出缺口 | T-024 |
| `init-workspace.ts [path]`、`doctor.ts` | 建立工作區（只補缺、不覆蓋）、檢查環境並給修正建議 | T-008 |
| （排程）`signals-snapshot`、`newsletter-split` | 每小時記 HN 分數、GitHub star、HF likes 的時間序列；把電子報每一期拆成每則連結 | T-036、T-037 |

相關 skill 在外層 [../skills/](../skills/)：`open-topic`、`gather-assets`。

**下載的限制**：YouTube 可以下載；Vimeo 與 X 推文目前需要登入（用自己帳號時，匯出 cookies 檔並設定 `YTDLP_COOKIES_FILE`）；網站擋下或 `robots.txt` 不允許的，只會成為素材缺口，不會繞過。需要先裝 `python -m pip install --user yt-dlp gallery-dl`。

**inbox 的規則**：人只改勾選、節目、格式、備註；程式只改受管區塊。沒勾的候選帶到隔天標「昨日」，超過 48 小時自動略過；勾了還沒建題的持續保留。

**原文擷取的順序**：GitHub、arXiv、YouTube 用各自的介面，其他網頁用 Readability，有設定 `JINA_API_KEY` 時再用 Jina Reader 備援，都失敗就 blocked 並請人把原文貼進 `sources/manual-1.md`。依據見 [../docs/spikes/SPK-FET-1.md](../docs/spikes/SPK-FET-1.md)。

## 本機開發（Windows）

需要 Node.js 24.22.1 以上、一個 PostgreSQL 17（用 Docker 跑最省事）、ffmpeg（素材盤點要用 `ffprobe`）。

```bash
docker run -d --name mediahot-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v mediahot-pgdata:/var/lib/postgresql/data postgres:17
npm install
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_dev   # 先建好這個資料庫
node scripts/migrate.ts
export NEWSROOM_HOME=./.data/workspace    # 工作區（inbox、topics）放哪裡，預設就是這個
```

測試：

```bash
export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5432/mediahot_test   # 名稱必須以 _test 或 _ci 結尾
npm run typecheck
node --test-global-setup=tests/databases.ts --import ./tests/databases.ts --test --test-concurrency=2 "modules/*/tests/*.test.ts" tests/architecture.test.ts
```

注意：完整的 `npm test` 在 Windows 上有 3 個 `SIGTERM during …` 測試會逾時（Windows 沒有真正的 SIGTERM），這與我們的改動無關，其他測試通過。網站與 worker 的完整啟動（`docker compose up`）我們還沒有驗證。

## 環境變數（我們新增的）

| 變數 | 用途 |
|---|---|
| `NEWSROOM_HOME` | 工作區位置（預設 `.data/workspace`） |
| `INBOX_DIR` | inbox 放哪裡（預設工作區底下的 `inbox`） |
| `GITHUB_TOKEN`、`JINA_API_KEY` | 選用，擷取 GitHub 與備援讀取時用（引擎原有的憑證機制） |
| `YTDLP_COOKIES_FILE` | 選用，自己帳號的 cookies 檔路徑，給 yt-dlp、gallery-dl 用；不要放進版本控制 |

其餘設定見 [.env.example](.env.example)。

## 文件

- 引擎怎麼運作、怎麼換行業：[docs/customize.md](docs/customize.md)、[docs/architecture.md](docs/architecture.md)、[docs/sources.md](docs/sources.md)、[docs/selection.md](docs/selection.md)、[docs/deploy.md](docs/deploy.md)（AIHOT 原作者的文件，部分說法仍指向原專案）。
- 我們的需求與規格：[../docs/spec/02_collect.md](../docs/spec/02_collect.md)、[../docs/spec/03_fetch_assets.md](../docs/spec/03_fetch_assets.md)、[../docs/spec/07_interfaces.md](../docs/spec/07_interfaces.md)。

## 授權

MIT，見 [LICENSE](LICENSE)。引擎部分的版權屬於原作者；我們的修改一併以 MIT 釋出。
