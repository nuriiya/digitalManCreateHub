# One-click full-docker startup for Windows.
#   .\start1.ps1 [dev|prod]     (default: dev)
#
# Does everything automatically:
#   1. installs Docker Desktop (winget) if missing, starts it if not running
#   2. builds the backend image (frontend dist is built inside the image)
#   3. docker compose up (dev/prod override)
#   4. waits for Ollama and pulls bge-m3 if absent
#   5. waits for the backend to answer on :8000
#
# NOTE: keep this file ASCII-only (PowerShell 5.1 reads it as GBK when there is
# no BOM - non-ASCII comments break the if-block structure).

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$EnvName = $args[0]
if (-not $EnvName) { $EnvName = "dev" }
if ($EnvName -ne "dev" -and $EnvName -ne "prod") {
    Write-Host "usage: .\start1.ps1 [dev|prod]"
    exit 1
}

function Write-Step($msg) { Write-Host "[start] $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "[start] $msg" -ForegroundColor Yellow }

# ---------- 1. Docker CLI (install if missing) ----------
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) {
    Write-Step "docker not found - installing Docker Desktop (winget)..."
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        # winget returns non-zero on "already installed, no upgrade" - do NOT
        # trust its exit code; refresh PATH and re-detect below instead
        & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements *> $null
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $ddBin = "C:\Program Files\Docker\Docker\resources\bin"
    if (Test-Path (Join-Path $ddBin "docker.exe")) { $env:Path = "$env:Path;$ddBin" }
    $docker = Get-Command docker -ErrorAction SilentlyContinue
}
if (-not $docker) {
    Write-Warn "docker still not found. Install Docker Desktop manually and re-run."
    Write-Warn "  https://www.docker.com/products/docker-desktop/"
    exit 1
}

# ---------- 1.5 Registry mirror (Docker Hub is blocked in CN) ----------
$daemonJson = Join-Path $env:USERPROFILE ".docker\daemon.json"
$mirror = "https://docker.m.daocloud.io"
$needMirror = $true
if (Test-Path $daemonJson) {
    $existing = Get-Content $daemonJson -Raw -ErrorAction SilentlyContinue
    if ($existing -match "registry-mirrors") { $needMirror = $false }
}
if ($needMirror) {
    Write-Step "configuring Docker Hub mirror (daocloud)..."
    New-Item -ItemType Directory -Force -Path (Split-Path $daemonJson) | Out-Null
    $cfg = $null
    if (Test-Path $daemonJson) {
        try { $cfg = Get-Content $daemonJson -Raw | ConvertFrom-Json } catch { $cfg = $null }
    }
    if ($cfg) {
        if ($cfg.PSObject.Properties.Name -notcontains "registry-mirrors") {
            $cfg | Add-Member -NotePropertyName "registry-mirrors" -NotePropertyValue @($mirror)
        } else {
            $cfg."registry-mirrors" = @($mirror)
        }
        $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $daemonJson -Encoding ascii
    } else {
        Set-Content -Path $daemonJson -Value ('{"registry-mirrors":["' + $mirror + '"]}') -Encoding ascii
    }
    Write-Warn "mirror configured - restarting Docker Desktop to apply"
    Stop-Process -Name "com.docker.backend" -Force -ErrorAction SilentlyContinue
    Stop-Process -Name "Docker Desktop" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
}

# ---------- 2. Ensure the Docker daemon is up ----------
$dockerOk = $false
for ($i = 0; $i -lt 20 -and -not $dockerOk; $i++) {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $dockerOk) {
    Write-Step "starting Docker Desktop..."
    $dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dd) { Start-Process -FilePath $dd }
    for ($i = 0; $i -lt 60 -and -not $dockerOk; $i++) {
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
        Start-Sleep -Seconds 3
    }
}
if (-not $dockerOk) {
    Write-Warn "docker daemon still not reachable - open Docker Desktop and re-run"
    exit 1
}
Write-Step "docker ready"

# ---------- 3. Compose files ----------
$composeFiles = @("-f", "docker-compose.yml")
if ($EnvName -eq "prod") {
    $composeFiles += @("-f", "docker-compose.prod.yml")
} else {
    $composeFiles += @("-f", "docker-compose.dev.yml")
}

# ---------- 3.5 Pre-pull base images (CN network: Docker Hub is blocked) ----------
# Pull via mirror prefixes then re-tag to the standard names so compose does not
# hit registry-1.docker.io. This works even when the daemon's registry-mirrors
# config has not been applied (no daemon restart needed).
$mirrorPrefixes = @("dockerproxy.net", "docker.1ms.run")
$baseImages = @("pgvector/pgvector:pg17")
if ($EnvName -eq "prod") { $baseImages += @("ollama/ollama:latest") }
foreach ($img in $baseImages) {
    & docker image inspect $img *> $null 2>$null
    if ($LASTEXITCODE -eq 0) { continue }
    $pulled = $false
    foreach ($prefix in $mirrorPrefixes) {
        # retry loop: docker pull resumes from cached layers, so a dropped
        # connection on a large image (ollama ~3.5GB) just continues
        for ($attempt = 1; $attempt -le 4 -and -not $pulled; $attempt++) {
            Write-Step "pulling $img via $prefix (attempt $attempt)..."
            # --progress=plain prints per-layer download progress to stderr
            # (real-time, no ANSI animation) so the user can watch the pull
            & docker pull --progress=plain "$prefix/$img"
            if ($LASTEXITCODE -eq 0) {
                & docker tag "$prefix/$img" $img
                $pulled = $true
            } else {
                Write-Warn "pull interrupted - retrying (resumes from cached layers)"
            }
        }
        if ($pulled) { break }
    }
    if (-not $pulled) { Write-Warn "could not pull $img - compose will try direct (may fail on CN network)" }
}

# ---------- 4. Build + up ----------
Write-Step "building + starting containers (env=$EnvName)..."
if ($EnvName -eq "prod") {
    & docker compose @composeFiles --profile ollama up -d --build
} else {
    & docker compose @composeFiles up -d --build
}
if ($LASTEXITCODE -ne 0) {
    Write-Warn "docker compose up failed - see the error above"
    exit 1
}

# ---------- 5. Ollama + bge-m3 ----------
Write-Step "waiting for Ollama..."
for ($i = 0; $i -lt 30; $i++) {
    & curl.exe -fsS http://localhost:11434/api/tags *> $null 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}
$tags = & curl.exe -fsS http://localhost:11434/api/tags 2>$null
if ($tags -and $tags -match "bge-m3") {
    Write-Step "bge-m3 ready"
} else {
    Write-Step "pulling bge-m3 embedding model (first time)..."
    & curl.exe -fsS http://localhost:11434/api/pull -d '{"model":"bge-m3"}' *> $null
    if ($LASTEXITCODE -ne 0) { Write-Warn "bge-m3 pull failed - retry later" }
}

# ---------- 6. Backend ready ----------
Write-Step "waiting for backend..."
$backendOk = $false
for ($i = 0; $i -lt 30 -and -not $backendOk; $i++) {
    & curl.exe -fsS http://localhost:8000/ *> $null 2>$null
    if ($LASTEXITCODE -eq 0) { $backendOk = $true; break }
    Start-Sleep -Seconds 2
}
if ($backendOk) {
    Write-Step "done: http://localhost:8000 (env=$EnvName)"
} else {
    Write-Warn "backend not answering yet - check: docker compose logs backend"
}
