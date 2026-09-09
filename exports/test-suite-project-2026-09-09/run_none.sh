#!/usr/bin/env bash
set -u
cd "$(dirname "$0")/../.."
SUITE="exports/test-suite-project-2026-09-09"
mkdir -p "$SUITE/results_none"
BASE="http://localhost:8000"
TOKEN=$(curl -s --max-time 8 -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo "token ok: ${TOKEN:0:8}..."
TASKS="165:proj_normalize_name 166:proj_parse_dsn 167:proj_paginate 168:proj_dedup_tags 169:proj_jsonb_get 170:proj_chunk_overlap 171:proj_cosine 172:proj_validate_tags 173:proj_safe_truncate 174:proj_extract_code 175:proj_merge_summaries 176:proj_topk"
for tv in $TASKS; do
  tid="${tv%%:*}"; key="${tv##*:}"
  echo "=== [NONE|$key] task=$tid running..."
  curl -s --max-time 500 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -X POST -d "{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"ollama\",\"ollama_model\":\"qwen2.5:7b-32k\",\"use_ontology\":false}" \
    "$BASE/api/capability/run_for_identity" > "$SUITE/results_none/$key.json"
  v=$(python -c "import json;d=json.load(open('$SUITE/results_none/$key.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo PARSE_ERR)
  echo "    -> $v"
done
echo "NONE-ONTOLOGY batch done"
