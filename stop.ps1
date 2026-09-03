# stop rag-mvp backend (uvicorn on :8000)
$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    $conn | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force -Confirm:$false } catch { }
    }
    Write-Host "[stop] backend stopped"
} else {
    Write-Host "[stop] nothing listening on :8000"
}
