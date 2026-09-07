# Multi-stage build: frontend (vite) -> backend (FastAPI). The image is fully
# self-contained: `docker build` produces the frontend dist inside the image,
# so no local node/npm or pre-built dist is required.

# ---- stage 1: frontend build ----
FROM node:22-slim AS frontend
WORKDIR /app/client
COPY client/package.json client/package-lock.json ./
RUN npm install --no-fund --no-audit
COPY client/ ./
RUN npm run build

# ---- stage 2: backend ----
FROM python:3.13-slim

WORKDIR /app

# Dependencies first (layer cache on rebuilds).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source + the built frontend dist (main.py serves <repo>/client/dist).
COPY backend/ ./backend/
COPY --from=frontend /app/client/dist ./client/dist

# Entrypoint (waits for PG, then execs the command).
COPY scripts/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

WORKDIR /app/backend
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
