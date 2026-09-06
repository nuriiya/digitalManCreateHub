# stop rag-mvp backend (uvicorn on :8000) + WSL keepalive
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$KeepaliveFile = Join-Path $DataDir "wsl_keepalive.pid"

# 1. Stop backend on :8000
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $conn | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force -Confirm:$false } catch { }
    }
    Write-Host "[stop] backend stopped"
} else {
    Write-Host "[stop] nothing listening on :8000"
}

# 2. Stop WSL keepalive (the hidden-window `wsl -e sleep infinity` started by
# start.ps1). Without this, WSL stays alive in the background and PG keeps
# running when you actually wanted everything down.
if (Test-Path $KeepaliveFile) {
    $pid_ = Get-Content $KeepaliveFile -Raw
    if ($pid_ -match '^\d+$') {
        try { Stop-Process -Id ([int]$pid_) -Force -Confirm:$false
              Write-Host "[stop] WSL keepalive (PID $pid_) stopped" }
        catch { Write-Host "[stop] WSL keepalive (PID $pid_) already gone" }
    }
    Remove-Item $KeepaliveFile -Force -ErrorAction SilentlyContinue
}