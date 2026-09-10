#!/usr/bin/env bash
# WSL 侧一键构建+启动（dev + wsl override）
set -uo pipefail
cd "$HOME/rag_prototype"
log() { printf '\n\033[36m[boot]\033[0m %s\n' "$*"; }

COMPOSE=(-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.wsl.yml)

log "1/4 build frontend dist (host, for dev mount)"
docker run --rm -v "$HOME/rag_prototype/client:/app" -w /app \
  -e NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \
  node:22-slim bash -lc "npm install --no-fund --no-audit && npm run build" 2>&1 | tail -6

log "2/4 build backend image"
docker compose "${COMPOSE[@]}" build backend 2>&1 | tail -8

log "3/4 up -d"
docker compose "${COMPOSE[@]}" up -d 2>&1 | tail -8

log "4/4 wait for backend"
for i in $(seq 1 45); do
  if curl -fsS -m 3 http://127.0.0.1:8000/ >/dev/null 2>&1; then
    echo "backend UP after ${i}x2s"; break
  fi
  sleep 2
done
echo ""
echo "=== containers ==="
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
echo ""
echo "=== backend probe ==="
curl -s -m 5 -o /dev/null -w "http://127.0.0.1:8000/ -> %{http_code}\n" http://127.0.0.1:8000/ || echo "unreachable"
