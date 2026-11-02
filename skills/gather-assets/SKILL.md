---
name: gather-assets
description: 替已建好的題目補原文與素材，並盤點缺什麼。使用者說「幫我抓原文」「這題有哪些素材」「缺什麼畫面」「我放了影片，更新一下」「原文抓不到怎麼辦」時使用。
owner: Albert Chiang
inputs: [IF-002]
outputs: [IF-004, IF-005]
tools: [fetch, fetch-media, assets]
gates: []
---

# gather-assets

讓一個題目有可以寫稿的原文，並讓人知道素材夠不夠。這個 skill 決定要補什麼、遇到缺口時怎麼告訴人；抓取與盤點由工具做。

## 什麼時候用

- 題目剛建好（`open-topic` 之後），要先有原文才能寫稿。
- 人說「再抓一次」「我貼了原文」「我放了影片到 media/raw」。
- 人問「這題素材夠嗎」「Reel 缺什麼」。

## 工具

目前的實作是 AIHOT 資料夾裡的腳本（`newsroom` 殼完成後改成同名指令，參數不變）；`NEWSROOM_HOME` 指向工作區，一律帶 `--json`：

| 工具 | 指令 |
|---|---|
| `fetch` | `node AIHOT/scripts/fetch-topic.ts <topic_id> [--force] --json` |
| `fetch-media` | `node AIHOT/scripts/fetch-media.ts <topic_id> [--layers 2,3] --json` |
| `assets` | `node AIHOT/scripts/assets-topic.ts <topic_id> [--voiceover] --json` |

## 步驟

1. 先判斷題目有沒有原文：`topics/<topic_id>/sources/` 裡有沒有檔案。沒有就先 `fetch`；有就不用重抓，除非人說要重抓（`--force`）。
2. `fetch` 的結果：
   - 成功：告訴人讀到幾個來源、用什麼方法；若有「官方來源」（`official: true`），特別說一聲，這是從報導裡的連結自動補進來的。
   - 部分失敗：列出失敗的網址與原因，但只要還有一個來源有原文就可以繼續。
   - `blocked`（結束碼 3、`E_MISSING_SOURCE`）：**停下來**，告訴人哪些網址抓不到、原因，請人把原文貼進 `sources/manual-1.md`（可以貼多份，`manual-2.md`……），貼好再叫你。不要自己編原文，也不要拿別的來源湊。
3. 原文有了之後，如果題目要做 Reel 或輪播，跑 `fetch-media`：它先收網頁上直接給的影片、GIF 與主視覺圖（L2），這一層沒拿到影片的來源，再用下載器抓 YouTube 等（L3）；跑完會自動盤點。只做 Threads 的題目不需要。純盤點、或人剛放了檔案，就單跑 `assets`（只掃 `media/raw`、`gen`、`proc`，不下載任何東西）。
4. 缺口怎麼說：
   - 照 `gaps` 的 `reason` 與 `suggestion` 轉述，用人聽得懂的話，具體到「要錄什麼、放在哪個資料夾」。
   - `fetch-media` 的限制要照實說，不要承諾一定補得到：網站擋下（例如 HTTP 403）、`robots.txt` 不允許、檔案超過 500 MB，都只會成為缺口；YouTube 可以下載，Vimeo 與 X 推文目前需要登入，`failed` 裡 `LOGIN` 的就是這種，要請人自己錄影放進 `media/raw/`，或由人提供自己帳號的 cookies 檔（環境變數 `YTDLP_COOKIES_FILE`）。`JS_RUNTIME` 要請人安裝 Deno，`OUTDATED` 要請人更新下載器，訊息裡有指令，照轉述。
   - 下載來的素材 `origin` 是 `official`（網頁上的檔案）或 `third_party`（下載器），`usage_note` 已預設提醒「確認授權、標註來源」；用在稿子前要請人確認授權，不要替人決定。
   - 缺口只影響對應的格式：只做 Threads 的題目沒有畫面缺口；reel 缺畫面、輪播缺主視覺才會列出。
5. 問人要不要等：缺口擋住的是 Reel 或輪播時，問人「要補素材再做，還是這題先只出 Threads／改輪播」。人決定之前不要往下走。
6. 結果整理成幾行：topic、原文來源數、素材數、缺口與下一步。

## 判斷標準

- 只要有一個來源有原文，Threads 就可以往下寫；缺素材不擋文字稿。
- `warnings` 裡「內容完全相同」「不認得的檔案類型」要轉述給人，因為那個檔案沒有被列進素材。
- 人說「我放了新檔案」就重新 `assets`；結果裡 `added`、`removed` 說明這次變了什麼。

## 失敗時

- `E_INPUT`（結束碼 2）：topic 編號不存在或沒給；先確認編號（`topics/` 底下的資料夾名）。
- 找不到 ffprobe：請人安裝 ffmpeg 並確認在 PATH，再重跑 `assets`。
- 外部網站擋下抓取（例如 HTTP 403）：照實告訴人，建議貼原文。**不要試圖改標頭或用別的方式繞過網站的阻擋**。

## 不要做

- 不要自己用其他方式下載、錄製或生成素材，也不要把檔案搬進 `media/raw/`；下載只透過 `fetch-media`。
- 不要替人登入、換標頭、或用其他手段繞過登入與機器人檢查；cookies 只用人自己提供的檔案。
- 不要改 `assets.json` 或 `sources/*.md` 的內容；人要註記時，叫人改 `usage_note`／`notes`，工具重跑時會保留。
- 不要在缺口還沒告訴人之前就叫 `draft-content` 開始寫 Reel 腳本。
