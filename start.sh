#!/usr/bin/env bash
# One-click full-docker startup for Linux.
#   ./start.sh [dev|prod] [--rebuild]     (default: dev)
#
# Does everything automatically:
#   1. installs Docker (get.docker.com) if missing, starts the daemon
#   2. docker compose up (dev/prod override). The backend image is REUSED by
#      default (no rebuild) so a restart is instant; pass --rebuild to rebuild
#      after a code change. When the image is absent, compose builds it itself.
#   3. waits for Ollama and pulls bge-m3 if absent
#   4. waits for the backend to answer on :8000

set -euo pipefail

ENV="${1:-dev}"
REBUILD=0
if [ "$ENV" = "--rebuild" ]; then
    ENV="dev"
    REBUILD=1
fi
for a in "$@"; do
    [ "$a" = "--rebuild" ] && REBUILD=1
done
if [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; then
    echo "usage: $0 [dev|prod] [--rebuild]" >&2
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

# ---------- 2.5 Pre-pull base images (CN network: Docker Hub is blocked) ----------
# Pull via mirror prefixes then re-tag to standard names so compose does not hit
# registry-1.docker.io directly. Override the mirrors via REGISTRY_MIRRORS env.
# Dev skips the ollama image (reuses host Ollama via host.docker.internal).
MIRRORS="${REGISTRY_MIRRORS:-dockerproxy.net docker.1ms.run}"
IMAGES="pgvector/pgvector:pg17"
[ "$ENV" = "prod" ] && IMAGES="$IMAGES ollama/ollama:latest"
for img in $IMAGES; do
    if docker image inspect "$img" >/dev/null 2>&1; then continue; fi
    pulled=0
    for m in $MIRRORS; do
        # retry loop: docker pull resumes from cached layers on a dropped
        # connection (ollama is ~3.5GB and can drop on slow mirrors)
        attempt=1
        while [ "$attempt" -le 4 ] && [ "$pulled" = "0" ]; do
            log "pulling $img via $m (attempt $attempt)..."
            if docker pull "$m/$img"; then
                docker tag "$m/$img" "$img"
                pulled=1
            else
                warn "pull interrupted - retrying (resumes from cached layers)"
            fi
            attempt=$((attempt + 1))
        done
        [ "$pulled" = "1" ] && break
    done
    [ "$pulled" = "1" ] || warn "could not pull $img - compose will try direct (may fail on CN network)"
done

# ---------- 3. Up (rebuild only when --rebuild is passed) ----------
# Reuse the existing rag-backend:latest image by default so a restart is
# instant. compose auto-builds when the image is absent (first run); pass
# --rebuild to force a fresh build after a code change.
if [ "$REBUILD" = "1" ]; then
    log "rebuilding + starting containers (env=$ENV)..."
    if [ "$ENV" = "prod" ]; then
        docker compose "${COMPOSE[@]}" --profile ollama up -d --build
    else
        docker compose "${COMPOSE[@]}" up -d --build
    fi
else
    log "reusing existing image + starting containers (env=$ENV)..."
    if [ "$ENV" = "prod" ]; then
        docker compose "${COMPOSE[@]}" --profile ollama up -d
    else
        docker compose "${COMPOSE[@]}" up -d
    fi
fi

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
