# Multi-stage build: frontend (vite) -> backend (FastAPI). The image is fully
# self-contained: `docker build` produces the frontend dist inside the image,
# so no local node/npm or pre-built dist is required.
#
# REGISTRY_LIBRARY_PREFIX lets CN builds pull the official base images (node /
# python) through a mirror (set in .env). Leave empty for direct Docker Hub.

ARG REGISTRY_LIBRARY_PREFIX=

# ---- stage 1: frontend build ----
FROM ${REGISTRY_LIBRARY_PREFIX}node:22-slim AS frontend
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /app/client
RUN npm config set registry ${NPM_REGISTRY}
COPY client/package.json client/package-lock.json ./
RUN npm install --no-fund --no-audit
COPY client/ ./
RUN npm run build

# ---- stage 2a: docker CLI (talks to host daemon via mounted docker.sock,
# used by the MCP sandbox + capability-test sandbox to run/stop containers) ----
FROM ${REGISTRY_LIBRARY_PREFIX}docker:cli AS dockercli

# ---- stage 2b: backend ----
FROM ${REGISTRY_LIBRARY_PREFIX}python:3.13-slim
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# Dependencies first (layer cache on rebuilds).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r backend/requirements.txt

# Backend source + the built frontend dist (main.py serves <repo>/client/dist).
COPY backend/ ./backend/
COPY --from=frontend /app/client/dist ./client/dist
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

# Entrypoint (waits for PG, then execs the command).
COPY scripts/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

WORKDIR /app/backend
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
