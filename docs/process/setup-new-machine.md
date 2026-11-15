# 在另一台電腦上跑起來（Windows，本機使用）

這份是給「把專案裝到老師或別人的電腦」用的。目標是在那台電腦上執行 `python start_dev.py`，網站、後端、爬蟲和本機模型都在同一台電腦上跑，**只有那台電腦自己連得到，不對外開放**。

聊天頁和素材區都沒有登入，所以不要開到網路上（見最後一節）。

## 電腦需要

| | 需求 |
|---|---|
| 作業系統 | Windows 10 或 11（`start_dev.py` 只能在 Windows 跑） |
| 顯卡 | NVIDIA，顯示記憶體 8 GB 以上（本機模型 `qwen3.5:9b` 開 32k 上下文約用 6.7 GB）。沒有顯卡的話，本機模型會慢到不能用，要改接雲端模型（沒測過） |
| 硬碟 | 空出 30 GB 以上（模型約 8 GB、Docker 映像、資料庫、下載的素材） |
| 要一直開機 | 爬蟲和分析要持續跑，日報每天 08:00 出；睡眠或 Windows 更新自動重啟都會漏掉 |

## 1. 安裝軟體

| 軟體 | 怎麼裝 |
|---|---|
| Git | https://git-scm.com |
| Python 3.12 以上 | https://www.python.org（安裝時勾「Add python.exe to PATH」） |
| Node.js 24.22.1 以上 | https://nodejs.org |
| Docker Desktop | https://www.docker.com/products/docker-desktop（只用來跑資料庫，裝好後打開並等它啟動完成） |
| ffmpeg | PowerShell 執行：`winget install Gyan.FFmpeg`，裝完開新的終端機 |
| Ollama | https://ollama.com（裝好會常駐在右下角） |
| NVIDIA 驅動程式 | 用 NVIDIA App 或官網更新到最新 |

## 2. 取得專案並安裝套件

```
git clone https://github.com/Albertchiang40210/AI_Media_Studio_content_pipe.git
cd AI_Media_Studio_content_pipe
git switch dev
python -m pip install -e ".[dev]" -c constraints.txt
python -m pip install --user yt-dlp gallery-dl
cd AIHOT
npm ci
cd ..
```

如果 clone 時要求登入，表示這個 GitHub 專案是私人的，需要先把老師的帳號加進去。

## 3. 準備模型

```
ollama pull bge-m3
ollama pull qwen3.5:9b
ollama create qwen3.5:9b-ctx32k -f deploy/qwen3.5-9b-ctx32k.Modelfile
```

為什麼要多建一個 `ctx32k`：Ollama 預設的上下文只有 4096 個 token，文章分析的提示詞比它長，超過的部分會被截掉開頭而且不報錯，結果會變得不準。`ctx32k` 是同一份模型權重加一行設定，不佔額外空間。

## 4. 建立 `AIHOT/.env`

這個檔案不在 git 裡，要自己建。在 `AIHOT` 資料夾裡新增 `.env`，內容：

```
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3.5:9b-ctx32k
LLM_EXTRA_JSON={"reasoning_effort":"none"}
```

`LLM_EXTRA_JSON` 是關掉模型的「思考」：不關的話，每次判斷要多花約 20 秒，文章分析還會因為輸出額度被思考用光而失敗。

選用的（沒設也能跑）：`GITHUB_TOKEN`（預設有 8 個 GitHub 信源，沒有的話會被限流）、`JINA_API_KEY`（有些網頁抓不到正文時的備援）。密碼和金鑰不用設，`start_dev.py` 會自己用本機測試用的值。

## 5. 檢查環境

```
python check_setup.py
```

每一項會印 `[ok  ]`、`[warn]` 或 `[FAIL]`，失敗的會寫怎麼補。全部沒有 `[FAIL]` 再往下。

## 6. 啟動

```
python start_dev.py
```

第一次啟動會自己建立資料庫容器（`parity-pg`，會下載 PostgreSQL 映像，約一分鐘）、建資料庫、放進預設的信源、啟動三個服務。之後約 20 秒後打開 http://127.0.0.1:3000 。

- 啟動訊息最後一行要寫「模型呼叫：打開」。
- **第一次文章會慢慢出現**：爬蟲要收 33 個信源，再逐篇分析，我這邊 1,000 篇約花 2 小時。這段時間網站上的新聞會一點一點變多。
- 停止：`python stop_dev.py`；連資料庫一起停：`docker stop parity-pg`。下次 `python start_dev.py` 會自己把它啟動。

## 7. 讓它一直開著

- 不要睡眠：`powercfg /change standby-timeout-ac 0`
- Windows Update：到「設定 → Windows Update → 進階選項」設定使用時間，避免它在夜裡自己重啟。重啟後要再跑一次 `python start_dev.py`。
- 日報每天 08:00 才會出，所以要開著過夜。服務沒開的那幾天補不出日報（日報只算「當天判斷」的新聞）。

## 8. （選用）把現有電腦的資料帶過去，省掉第一次的等待

**這一段的指令沒有實際跑過**，用之前請先把原來的資料庫備份好。

在原本的電腦（`parity-pg` 開著時）：

```
docker exec parity-pg pg_dump -U postgres -Fc -f /tmp/dev.dump dev
docker cp parity-pg:/tmp/dev.dump .\dev.dump
```

把 `dev.dump` 複製到新電腦。在新電腦，先照第 6 步啟動過一次（讓它建好資料庫），再 `python stop_dev.py` 停掉，然後：

```
docker cp .\dev.dump parity-pg:/tmp/dev.dump
docker exec parity-pg pg_restore -U postgres -d dev --clean --if-exists --no-owner /tmp/dev.dump
python start_dev.py
```

## 常見問題

| 現象 | 原因與做法 |
|---|---|
| `start_dev.py` 說「資料庫容器 parity-pg 啟動失敗」 | Docker Desktop 沒開，打開它等啟動完成 |
| 聊天頁說「回答用的模型現在沒有開」 | Ollama 沒在跑（右下角沒有圖示），打開 Ollama 或執行 `ollama serve` |
| 文章一直停在「待分析」 | 看啟動訊息有沒有「模型呼叫：打開」；沒有的話檢查 `AIHOT/.env` 有沒有 `LLM_API_KEY` |
| 分析很慢、顯卡沒在用 | 執行 `ollama ps`，`PROCESSOR` 要寫 `100% GPU`；不是的話顯示記憶體不夠或驅動程式太舊 |
| 埠 3000 或 3101 被佔用 | 上次的服務沒關乾淨，執行 `python stop_dev.py` |
| 沒有下載影片 | 沒裝 yt-dlp；X 貼文和 Vimeo 要登入（見 README 的「下載的限制」） |

## 為什麼不能開到網路上

- 聊天頁沒有登入也沒有使用限制，任何連得到的人都能用這台電腦的顯卡跑模型。
- 素材區的下載沒有登入，連得到的人可以叫這台電腦下載檔案。
- `start_dev.py` 裡的密碼和金鑰都是本機測試用的固定值。

所以只在這台電腦用瀏覽器開 `127.0.0.1`。如果以後要讓別人從網路連，要先加登入、使用限制，並換掉所有密碼金鑰，用 `deploy/` 的 Docker 設定部署。
