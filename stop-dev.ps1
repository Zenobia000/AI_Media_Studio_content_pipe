# 停掉 start-dev.ps1 開的後端 API、worker 和網站（資料庫容器不停，資料都在裡面）。
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'uvicorn --factory newsroom.api.app' -or
    $_.CommandLine -match 'newsroom worker' -or
    $_.CommandLine -match 'react-router dev' -or
    ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'apps[\\/]web')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'MediaHOT*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "已停止。要連資料庫一起停：docker stop parity-pg"
