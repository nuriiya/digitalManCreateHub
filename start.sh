#!/usr/bin/env bash
# One-click full-docker startup for Linux.
#   ./start.sh [dev|prod]     (default: dev)
#
# Does everything automatically:
#   1. installs Docker (get.docker.com) if missing, starts the daemon
#   2. builds the backend image (frontend dist is built inside the image)
#   3. docker compose up (dev/prod override)
#   4. waits for Ollama and pulls bge-m3 if absent
#   5. waits for the backend to answer on :8000

set -euo pipefail

ENV="${1:-dev}"
if [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; then
    echo "usage: $0 [dev|prod]" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

log()  { printf '\033[36m[start]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[start]\033[0m %s\n' "$*"; }

# ---------- 1. Docker (install if missing, then ensure daemon) ----------
if ! command -v docker >/dev/null 2>&1; then
    log "docker not found - installing (get.docker.com)..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    # the group change needs a re-login; retry via sudo for this run
    if ! docker info >/dev/null 2>&1; then
        warn "docker group change needs a new shell - retrying via sudo docker"
        sudo docker info >/dev/null 2>&1 || true
    fi
fi

if ! docker info >/dev/null 2>&1; then
    log "starting docker daemon..."
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl start docker 2>/dev/null || true
    else
        sudo dockerd >/var/log/dockerd.log 2>&1 &
    fi
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
fi
docker info >/dev/null 2>&1 || { warn "docker daemon still unreachable - check 'docker info'"; exit 1; }
log "docker ready ($(docker --version))"

# ---------- 2. Compose files ----------
COMPOSE=(-f docker-compose.yml)
if [ "$ENV" = "prod" ]; then
    COMPOSE+=(-f docker-compose.prod.yml)
else
    COMPOSE+=(-f docker-compose.dev.yml)
fi

# ---------- 3. Build + up ----------
log "building + starting containers (env=$ENV)..."
docker compose "${COMPOSE[@]}" up -d --build

# ---------- 4. Ollama + bge-m3 ----------
log "waiting for Ollama..."
for _ in $(seq 1 30); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 2
done
if curl -fsS http://localhost:11434/api/tags 2>/dev/null | grep -q bge-m3; then
    log "bge-m3 ready"
else
    log "pulling bge-m3 embedding model (first time)..."
    curl -fsS http://localhost:11434/api/pull -d '{"model":"bge-m3"}' \
        || warn "bge-m3 pull failed - retry later via the UI or re-run this script"
fi

# ---------- 5. Backend ready ----------
log "waiting for backend..."
for _ in $(seq 1 30); do
    curl -fsS http://localhost:8000/ >/dev/null 2>&1 && break
    sleep 2
done
if curl -fsS http://localhost:8000/ >/dev/null 2>&1; then
    log "done: http://localhost:8000 (env=$ENV)"
else
    warn "backend not answering yet - check: docker compose logs backend"
fi
