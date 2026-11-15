# 後端改成 Python 之後修了什麼

改寫完成之後發現並修掉的問題，依日期。改寫本身每一步怎麼做，看根目錄 [README.md](../../README.md) 的〈後端改成 Python 之後〉；目前的進度看 [PROGRESS.md](../../AIHOT/PROGRESS.md)。

| 日期 | 問題 | 怎麼修 |
|---|---|---|
| 11-04 | 自動檢查（`check.yml`）放在 `AIHOT/.github/`，GitHub 只讀根目錄的 `.github/`，一直沒真的跑過 | 移到根目錄，指定在 `AIHOT/` 執行，加上 `dev` 分支（還沒 push，沒在 GitHub 上跑過） |
| 11-05 | 從空資料庫 `newsroom migrate` 會報錯 | 修好，現在一個指令建完 86 個 migration |
| 11-05 | 正文擷取與 Node 有一處不同：網頁用 `<template>` 宣告 shadow DOM 時，Node 的 Readability 不會刪裡面的 `<style>`，Python 版會刪，影響評分 | 改成不進入 `<template>`，加回歸測試（舊程式碼下失敗）；重比 74 個真實頁面全部一致 |
| 11-05 | `LLM_EXTRA_JSON`、`LLM_REASONING_TOKENS` 設了被默默忽略（Python 版漏掉） | 補上，錯誤訊息與 Node 一致，對照測試帶這兩個設定 |
| 11-05 | 本機模型不照提示詞填值：16 篇有 5 篇被擋（分類填「article」、分數填 0.85） | 加可選的結構化輸出（`LLM_STRUCTURED_OUTPUT`），讓伺服器限制可填的值；16 篇全部成功 |
| 11-05 | 相關文章有 32%（106/329）沒有分類 | 結構抽取也用結構化輸出，重新分析 102 篇後降到 4.3% |
| 11-05 | 結構抽取輸出被截斷（上限 1,200 token，本機模型平均用掉約 1,170） | `start_dev.py` 對本機模型多給 1,500 token（`LLM_REASONING_TOKENS`） |
| 11-05 | 本機模型一次只處理很少請求，worker 同時送 6 個，後面的逾時（ReadTimeout） | 新增 `ANALYZE_CONCURRENCY`，`start_dev.py` 對本機模型設成 2；0 篇失敗 |
| 11-05 | 本機模型常用簡體字寫標題（337 篇有 47%） | 提示詞最後加一句要求繁體（`LLM_TRADITIONAL`），簡體字從 59 個降到 1 個 |
| 11-05 | Docker 裡安裝後的套件找不到 `AIHOT/` 資料夾（遷移檔、靜態檔、字型）；映像沒有 `pg_dump`，備份失敗 | 用 `AIHOT_ROOT` 指定位置；映像加裝 PostgreSQL 客戶端 |
| 11-05 | 真實收集才出現：工作結果裡有日期時寫不進佇列，工作一直停在進行中 | 日期與 pg-boss 一樣存成 ISO 字串，加回歸測試 |
| 11-05 | 模型選擇的快取會在同一個行程裡留到下一個測試，測試互相影響 | 修好；新增測試要注意清除這類快取 |
| 11-05 | 警報測試資料裡幾種錯誤次數剛好相同，同次數排序沒有定義，測試偶爾失敗 | 改成次數都不同 |
| 11-05 | 網站地圖對照測試偶爾差 1 秒（快取還剩幾秒，兩個伺服器依序被請求、碰到秒的邊界） | 測試不再比對這個剩餘秒數 |
| 11-05 | 發布日期校正（預覽與套用）原版後臺沒有路由、Python 版原本沒有 | 移植，14 個案例與 Node 比對一致 |
| 11-05 | 後臺寫入操作沒有經過前端驗證過 | 用同樣的 22 筆寫入分別經接 Python 與接 Node 的前端送進各自資料庫複本，狀態碼、回應、資料列全部一致 |
| 11-06 | 本機啟動會跳出三個終端 | 改成同一個終端，輸出每行加服務名稱前綴，Ctrl+C 全停；`--separate` 可回到三個視窗 |
| 11-06 | 程式宣告支援 Python 3.11，實際用了 3.12 才有的語法（`pyproject.toml` 與程式不一致）；`ruff` 有 25 個問題，GitHub 上的 lint 會紅燈 | `requires-python`、ruff、mypy 都改成 3.12；修掉測試檔的小毛病，並把測試目錄對 3 條規則放寬（pytest 常見寫法） |
| 11-06 | `mypy src` 有 282 個型別錯誤（260 個在移植的後端），GitHub 上的型別檢查要不就沒有、要不就紅燈 | 全部修掉，現在 `mypy src` 全過、已加進 `check.yml`。主要做法：`core/db.py` 的 `connect()` 標明回傳 dict 列（一次清掉一批）、`_readability_p1.py` 宣告被子類別實作的方法、`dom.py` 宣告動態屬性、其餘用型別標註或 `cast`；沒有放寬規則、沒有用 `ignore_errors`。幾處 `re.match` 後加了 `assert`（只在不可能發生的路徑上，錯誤型別從 AttributeError 變 AssertionError） |
| 11-06 | 第一次在 GitHub 上跑 CI：`check` 的遷移檢查失敗，`git show <commit>:database/...` 找不到檔案（repo 根目錄在 `AIHOT/` 的上一層） | `AIHOT/scripts/check-migrations.ts` 改成 `${base}:./${file}`，路徑相對於 `AIHOT/` |
| 11-06 | `python` 工作 74 個和 Node 對照的測試失敗：工作裡沒有 Node 和 AIHOT 的套件 | `check.yml` 的 `python` 工作加 Node 24 和 `npm ci` |
| 11-06 | 對照測試讀到不完整的 JSON：Node 腳本在 `console.log` 後立刻 `process.exit()`，stdout 是管線時 Linux 上輸出被切在 65,536 位元組（在 `node:24` 容器裡重現）；Windows 上不會發生，所以本機一直沒看到 | 新增 `AIHOT/scripts/flush.ts` 的 `exitAfterFlush()`，19 個 `*-run.ts` 腳本改成輸出寫完才結束 |
| 11-06 | `architecture.test.ts` 失敗：範本列了 `NEWSROOM_HOME`、`INBOX_DIR`、`CHANGEDETECTION_*`、`YTDLP_COOKIES_FILE`，但檢查只掃 TypeScript，這些只有 Python 在讀 | 檢查也掃 `../src/newsroom` 的 Python 程式 |
| 11-06 | `error-logging.test.ts` 逾時（子程序啟動整個 API，限時 10 秒，GitHub 的機器忙時不夠；上一輪還通過，是時間不穩定） | 限時改成 60 秒。最後一次 CI 三個工作全綠 |
| 11-14 | Ars Technica 的文章正文只抓到一段：它把一篇文章切成幾塊、各放在自己的外框裡，中間夾廣告欄，Readability 只取其中一塊（原版 TypeScript 也一樣）。實測 8 篇裡有 5 篇只抓到三分之一到一半 | 擷取前把同一個 `<article>` 裡、標籤與 class 相同、各自直接放段落、又分在不同外框的區塊排在一起（`join_split_body`；TypeScript 版同步加 `joinSplitBody`）。68 個真實頁面（23 個網站）比對前後：8 頁改變，都是補回正文或去掉雜訊，兩種語言的結果 68 頁全部一致。**已經收進資料庫的 Ars 文章不會自動重抓** |
| 11-14 | 連結裡空的查詢字串（`index.html?#data=…`）Python 版會丟掉 `?`，和 Node 版不同（在 research.google 的一頁上發現） | `resolve_url` 保留原本寫的 `?` 與 `#`，加進對照測試 |
