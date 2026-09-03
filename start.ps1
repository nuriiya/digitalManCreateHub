# rag-mvp one-click start (PowerShell 5.1 compatible)
# - checks Python venv + deps (auto install)
# - builds frontend dist if stale
# - embedding: host Ollama first (11434 reachable or Windows binary), WSL2 only
#   as fallback (detect user, cache sudo password via DPAPI, ensure Ollama+bge-m3)
# - starts uvicorn on http://localhost:8000 (serves client/dist)
# Graceful degradation: Ollama/WSL2 problems never block startup (hash embedding fallback).

# NOTE: must be "Continue" - in PS 5.1, $ErrorActionPreference="Stop" makes any
# redirected native stderr (2>$null) throw NativeCommandError and kill the script
# (e.g. the deps check traceback below). All real failures are guarded by explicit
# $LASTEXITCODE checks + throw.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $Root "data"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$CredFile = Join-Path $DataDir "wsl_cred"

function Write-Step($msg) { Write-Host "[start] $msg" -ForegroundColor Cyan }
function Write-Warn2($msg) { Write-Host "[start] $msg" -ForegroundColor Yellow }

# ---------- 0. Stop any backend already running (port :8000) ----------
# A stale uvicorn would block the port (new process dies with 10048) and its
# dead workers leave ghost 'running' jobs. So: detect -> stop -> wait -> start.
$oldConn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($oldConn) {
    $pids = $oldConn | Select-Object -ExpandProperty OwningProcess -Unique
    Write-Step "backend already running on :8000 (PID $($pids -join ',')) - stopping it first..."
    $pids | ForEach-Object {
        try { Stop-Process -Id $_ -Force -Confirm:$false } catch { }
    }
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 300
        if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) { break }
    }
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        throw "old backend on :8000 refused to die - stop it manually (stop.ps1) and retry"
    }
    Write-Step "old backend stopped (paused jobs are recovered on startup)"
} else {
    Write-Step "no existing backend on :8000"
}

# ---------- 1. Python backend ----------
Write-Step "checking python backend..."
if (-not (Test-Path $VenvPython)) {
    Write-Step "creating venv (.venv)..."
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw "python not found in PATH. Install Python 3.10+ first." }
    & python -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
& $VenvPython -c "import fastapi, uvicorn, pypdf, httpx" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Step "installing backend requirements..."
    & $VenvPython -m pip install -r (Join-Path $Root "backend\requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# ---------- 2. Frontend build (only when stale) ----------
$Dist = Join-Path $Root "client\dist\index.html"
$NeedBuild = -not (Test-Path $Dist)
if (-not $NeedBuild) {
    $distTime = (Get-Item $Dist).LastWriteTime
    Get-ChildItem -Recurse (Join-Path $Root "client\src") -File | ForEach-Object {
        if ($_.LastWriteTime -gt $distTime) { $NeedBuild = $true }
    }
}
if ($NeedBuild) {
    Write-Step "building frontend (src newer than dist)..."
    $nodeDir = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $nodeDir) {
        $managed = "C:\Users\12030\.workbuddy\binaries\node\versions\22.22.2"
        if (Test-Path $managed) { $env:PATH = "$managed;$env:PATH" }
    }
    Push-Location (Join-Path $Root "client")
    if (-not (Test-Path "node_modules")) { npm install --no-fund --no-audit | Out-Null }
    npm run build | Out-Null
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "frontend build failed" }
    Pop-Location
} else {
    Write-Step "frontend dist up to date, skip build"
}

# ---------- 3. Embedding backend: host Ollama FIRST, WSL2 only as fallback ----------
# Priority: (1) something already serving on localhost:11434 (host or WSL - same
# endpoint), (2) Ollama installed on the Windows host -> start it, (3) WSL2
# install flow. Host Ollama must never trigger the WSL2/sudo password dance.
function Get-OllamaTags {
    $tags = & curl.exe -s --max-time 4 http://localhost:11434/api/tags 2>$null
    if ($tags -and $tags -match '"models"') { return $tags }
    return $null
}

function Ensure-BgeM3 {
    param($Tags)
    if ($Tags -notmatch 'bge-m3') {
        Write-Step "pulling bge-m3 embedding model (large, first time only)..."
        & curl.exe -s http://localhost:11434/api/pull -d '{"model": "bge-m3"}' | Out-Null
    } else {
        Write-Step "bge-m3 model ready"
    }
}

$OllamaReady = $false
$tags = Get-OllamaTags
if (-not $tags) {
    $hostOllama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($hostOllama) {
        Write-Step "host Ollama found (not running) - starting service..."
        Start-Process -FilePath $hostOllama.Source -ArgumentList "serve" -WindowStyle Hidden
        for ($i = 0; $i -lt 8 -and -not $tags; $i++) {
            Start-Sleep -Seconds 2
            $tags = Get-OllamaTags
        }
    }
}

if ($tags) {
    $OllamaReady = $true
    Write-Step "Ollama reachable on localhost:11434 (host) - WSL2 setup skipped"
    Ensure-BgeM3 $tags
} else {
    # ---------- WSL2 fallback (host has no Ollama) ----------
    $WslOk = $false
    try {
        $status = & wsl --status 2>$null
        if ($LASTEXITCODE -eq 0) { $WslOk = $true }
    } catch { }

    if ($WslOk) {
        Write-Step "WSL2 detected"
        # detect human users (UID >= 1000)
        $users = @()
        try {
            $raw = & wsl -e bash -c "getent passwd | awk -F: '`$3>=1000 && `$3<65534 {print `$1}'" 2>$null
            if ($raw) { $users = @($raw | Where-Object { $_ -and $_ -ne "nobody" }) }
        } catch { }
        if ($users.Count -eq 0) { $users = @("root") }

        $WslUser = $users[0]
        if ($users.Count -gt 1) {
            Write-Host "WSL2 has multiple users:"
            for ($i = 0; $i -lt $users.Count; $i++) {
                Write-Host ("  [{0}] {1}" -f ($i + 1), $users[$i])
            }
            $sel = Read-Host "select user for Ollama deployment [1-$($users.Count), default 1]"
            $idx = 0
            if ($sel -match '^\d+$') { $idx = [int]$sel - 1 }
            if ($idx -ge 0 -and $idx -lt $users.Count) { $WslUser = $users[$idx] }
        }
        Write-Step "WSL2 user: $WslUser"

        # sudo password: DPAPI-encrypted cache, bound to current Windows user + machine
        $Plain = $null
        if (Test-Path $CredFile) {
            try {
                $sec = Get-Content $CredFile -Raw | ConvertTo-SecureString
                $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
                $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)
                # verify cached password still works
                $probe = "echo '$Plain' | sudo -S -k true 2>/dev/null"
                & wsl -u $WslUser -e bash -c $probe *> $null
                if ($LASTEXITCODE -ne 0) { $Plain = $null; Write-Warn2 "cached password invalid, re-asking" }
            } catch { $Plain = $null }
        }
        if (-not $Plain -and $WslUser -ne "root") {
            $sec = Read-Host "WSL sudo password for '$WslUser'" -AsSecureString
            $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            $Plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)
            $probe = "echo '$Plain' | sudo -S -k true 2>/dev/null"
            & wsl -u $WslUser -e bash -c $probe *> $null
            if ($LASTEXITCODE -ne 0) { throw "sudo password verification failed" }
            New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
            # DPAPI encryption (current user + machine scope)
            $sec | ConvertFrom-SecureString | Set-Content $CredFile -NoNewline
            Write-Step "sudo password cached (DPAPI, data/wsl_cred). Delete the file to reset."
        }

        # Ollama: install if missing (idempotent)
        $hasOllama = & wsl -u $WslUser -e bash -c "command -v ollama" 2>$null
        if (-not $hasOllama) {
            Write-Step "installing Ollama in WSL2 (may take a few minutes)..."
            if ($Plain) {
                $installCmd = "echo '$Plain' | sudo -S -k curl -fsSL https://ollama.com/install.sh | sh"
            } else {
                $installCmd = "curl -fsSL https://ollama.com/install.sh | sudo sh"
            }
            & wsl -u $WslUser -e bash -c $installCmd
            if ($LASTEXITCODE -ne 0) { Write-Warn2 "Ollama install failed - continuing with hash embedding fallback" }
        } else {
            Write-Step "Ollama present in WSL2"
        }

        # ensure Ollama service is up + bge-m3 model
        & wsl -u $WslUser -e bash -c "pgrep -x ollama >/dev/null 2>&1 || (nohup ollama serve >/dev/null 2>&1 &)" 2>$null
        Start-Sleep -Seconds 2
        $tags = Get-OllamaTags
        if ($tags) {
            $OllamaReady = $true
            Write-Step "Ollama reachable on localhost:11434 (WSL2)"
            Ensure-BgeM3 $tags
        } else {
            Write-Warn2 "Ollama not reachable on localhost:11434 - embedding falls back to hash mode (chain stays alive). Configure it later in Settings."
        }
    } else {
        Write-Warn2 "no host Ollama and no WSL2 - embedding uses local hash mode. Everything else works."
    }
}

# default the app to the reachable Ollama (only when settings are still on the
# untouched 'hash' default AND no embedding lock exists - iron law 1)
if ($OllamaReady) {
    Push-Location (Join-Path $Root "backend")
    try {
        $out = & $VenvPython -c "from app.db import get_conn; from app import embedding; print('switched' if embedding.default_to_reachable_ollama(get_conn()) else 'kept')" 2>$null
        if ($out -match 'switched') { Write-Step "embedding default switched: hash -> ollama (localhost:11434)" }
        elseif ($out -match 'kept') { Write-Step "embedding settings kept (explicit choice or existing vectors)" }
    } catch { } finally { Pop-Location }
}

# ---------- 4. Start backend ----------
Write-Step "starting backend on http://localhost:8000 (Ctrl+C to stop)..."
$env:PYTHONIOENCODING = "utf-8"
Push-Location (Join-Path $Root "backend")
try {
    & $VenvPython -m uvicorn app.main:app --host localhost --port 8000
} finally {
    Pop-Location
}
