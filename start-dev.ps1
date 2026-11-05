# 一次啟動本機開發環境：資料庫（Docker 裡的 parity-pg）、Python 後端 API、worker、網站。
# 用法（在專案根目錄的 PowerShell）：  .\start-dev.ps1            啟動
#                                       .\start-dev.ps1 -Collect  同時打開自動收集信源（要連外網）
# 停止：.\stop-dev.ps1
# 這裡的密碼與密鑰都是本機測試用的，不要拿去正式環境。
param([switch]$Collect)

$root = $PSScriptRoot
$env:DATABASE_URL = "postgres://postgres:ci@127.0.0.1:55432/seeded"
$env:SITE_URL = "http://127.0.0.1:3000"
$env:API_BASE_URL = "http://127.0.0.1:3101"
$env:API_PORT = "3101"
$env:SESSION_SECRET = "ci-session-secret-0123456789abcdef"
$env:IMG_PROXY_SIGN_SECRET = "ci-img-secret-0123456789abcdef"
$env:ADMIN_PASSWORD = "ci-admin-password-123"
$env:INGEST_TOKEN = "ci-ingest-token-0123456789abcdef"
$env:AIHOT_DATA_DIR = Join-Path $root ".data"
$env:PYTHONUTF8 = "1"
$env:COLLECT_ENABLED = if ($Collect) { "true" } else { "false" }

# 1. 資料庫
docker start parity-pg | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Error "資料庫容器 parity-pg 啟動失敗：請先打開 Docker Desktop。"; exit 1 }
for ($i = 0; $i -lt 30; $i++) {
    docker exec parity-pg pg_isready -U postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep 1
}

# 2. 後端 API 與 worker，3. 網站：各開一個視窗，視窗標題寫明是哪一個
function Start-Window($title, $command) {
    Start-Process powershell -WorkingDirectory $root -ArgumentList "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='$title'; $command"
}
Start-Window "MediaHOT API" "python -m uvicorn --factory newsroom.api.app:build_app --port 3101"
Start-Window "MediaHOT worker" "newsroom worker"
Start-Window "MediaHOT 網站" "cd AIHOT\apps\web; `$env:API_BASE_URL='http://127.0.0.1:3101'; npm run dev"

Write-Host ""
Write-Host "已啟動三個視窗（API、worker、網站），網站約 20 秒後可用："
Write-Host "  網站  http://127.0.0.1:3000"
Write-Host "  後臺  http://127.0.0.1:3000/admin/login   密碼 ci-admin-password-123"
Write-Host "  API   http://127.0.0.1:3101/api/health"
if (-not $Collect) { Write-Host "自動收集是關著的；要打開請用  .\start-dev.ps1 -Collect" }
