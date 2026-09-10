#!/usr/bin/env bash
# WSL 一键启动（原生 dockerd + mirrored 网络 + host 网络 backend）
# 用法（WSL 内）：
#   bash ops/wsl_up.sh            # 启动（复用镜像）
#   bash ops/wsl_up.sh --rebuild  # 改代码后重建镜像
set -uo pipefail
cd "$HOME/rag_prototype"

COMPOSE=(-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.wsl.yml)

if ! docker info >/dev/null 2>&1; then
  echo "[wsl_up] starting dockerd..."
  sudo service docker start 2>/dev/null || sudo dockerd >/var/log/dockerd.log 2>&1 &
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker info >/dev/null 2>&1 || { echo "[wsl_up] ERR: docker daemon unreachable"; exit 1; }

if [ "${1:-}" = "--rebuild" ]; then
  docker compose "${COMPOSE[@]}" up -d --build
else
  docker compose "${COMPOSE[@]}" up -d
fi

echo ""
echo "[wsl_up] waiting for backend..."
for _ in $(seq 1 30); do
  curl -fsS -m 3 http://127.0.0.1:8000/ >/dev/null 2>&1 && break
  sleep 2
done
docker ps --format '{{.Names}}\t{{.Status}}' | head -4
echo "[wsl_up] http://localhost:8000"
