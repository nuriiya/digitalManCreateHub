#!/usr/bin/env bash
# 把 Windows 侧导出的 PG dump 导入 WSL 侧 rag_pg 容器
set -uo pipefail
DUMP="/mnt/d/workspace/调研/rag_prototype/_pgdump.sql"
cd "$HOME/rag_prototype"

if [ ! -f "$DUMP" ]; then echo "ERR: dump not found: $DUMP"; exit 1; fi

echo "[import] waiting for rag_pg..."
for i in $(seq 1 40); do
  docker exec rag_pg pg_isready -U postgres -d rag >/dev/null 2>&1 && { echo "[import] pg ready (${i}x2s)"; break; }
  sleep 2
done

echo "[import] ensure vector extension"
docker exec rag_pg psql -U postgres -d rag -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 | tail -1

echo "[import] loading dump ($(du -h "$DUMP" | cut -f1))..."
docker exec -i rag_pg psql -U postgres -d rag -v ON_ERROR_STOP=0 < "$DUMP" 2>&1 | grep -Ei "error|fatal" | head -10
echo "[import] done"

echo ""
echo "=== 校验 ==="
docker exec rag_pg psql -U postgres -d rag -c "
SELECT 'identities' t, count(*) n FROM identities
UNION ALL SELECT 'persona_ontology', count(*) FROM persona_ontology
UNION ALL SELECT 'capability_tasks', count(*) FROM capability_tasks
UNION ALL SELECT 'pipelines', count(*) FROM pipelines
UNION ALL SELECT 'documents', count(*) FROM documents
UNION ALL SELECT 'chunks', count(*) FROM chunks
ORDER BY t;"
