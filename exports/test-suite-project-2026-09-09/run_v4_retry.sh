#!/usr/bin/env bash
# V4-Flash 补跑：重跑带重试（APIConnectionError 3x backoff 已加入 llm.py）
set -u
cd "$(dirname "$0")/../.."
SUITE="exports/test-suite-project-2026-09-09"
BASE="http://localhost:8000"
TOKEN=$(curl -s --max-time 8 -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo "token ok (retry patch)"
while IFS='|' read -r DIR KEY TID MODE USE; do
  [ -z "$DIR" ] && continue
  FULL="$SUITE/$DIR"
  mkdir -p "$FULL"
  echo "=== [retry|$DIR|$KEY] task=$TID mode=$MODE use=$USE ==="
  if [ "$MODE" = "pipeline" ]; then
    body="{\"identity_id\":3,\"task_id\":$TID,\"provider\":\"llm\",\"use_ontology\":$USE,\"mode\":\"pipeline\",\"reviewer_id\":6}"
  else
    body="{\"identity_id\":3,\"task_id\":$TID,\"provider\":\"llm\",\"use_ontology\":$USE}"
  fi
  curl -s --max-time 560 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -X POST -d "$body" "$BASE/api/capability/run_for_identity" > "$FULL/$KEY.json"
  v=$(python -c "import json;d=json.load(open('$FULL/$KEY.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo PARSE_ERR)
  echo "    -> $v"
done < "$SUITE/retry_v4.txt"
echo "RETRY batch done"
