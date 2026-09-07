#!/bin/sh
# Backend entrypoint: wait for PostgreSQL to accept connections, then exec the
# real command (uvicorn). Uses RAG_DATABASE_URL so the same image works against
# the compose `pg` service or any external DSN.
set -e

echo "[entrypoint] waiting for PostgreSQL..."
python -c "
import os, sys, time
import psycopg
dsn = os.environ.get('RAG_DATABASE_URL', 'postgresql://postgres:postgres@pg:5432/rag')
for i in range(60):
    try:
        psycopg.connect(dsn, connect_timeout=2).close()
        print('[entrypoint] PostgreSQL ready')
        sys.exit(0)
    except Exception:
        time.sleep(2)
print('[entrypoint] PostgreSQL not ready after 120s', file=sys.stderr)
sys.exit(1)
"

exec "$@"
