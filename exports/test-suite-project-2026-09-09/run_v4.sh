#!/usr/bin/env bash
# 4 元消融 × V4-Flash（provider=llm, deepseek-v4-flash）
#   A = 单数字人 reactive + 本体          results_v4_ont
#   B = 单数字人 reactive - 本体          results_v4_none
#   C = pipeline(writer3↔reviewer6) + 本体 results_v4_pipe1
#   D = pipeline(writer3↔reviewer6) - 本体 results_v4_pipe0
# 从项目根运行：bash exports/test-suite-project-2026-09-09/run_v4.sh
set -u
cd "$(dirname "$0")/../.."
SUITE="exports/test-suite-project-2026-09-09"
BASE="http://localhost:8000"

run_group() {
  local DIR="$1" MODE="$2" USE="$3"
  mkdir -p "$DIR"
  local TOKEN
  TOKEN=$(curl -s --max-time 8 -X POST -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
    | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
  echo "token ok ($DIR mode=$MODE use=$USE)"
  local TASKS="165:proj_normalize_name 166:proj_parse_dsn 167:proj_paginate 168:proj_dedup_tags 169:proj_jsonb_get 170:proj_chunk_overlap 171:proj_cosine 172:proj_validate_tags 173:proj_safe_truncate 174:proj_extract_code 175:proj_merge_summaries 176:proj_topk"
  local tv tid key body
  for tv in $TASKS; do
    tid="${tv%%:*}"; key="${tv##*:}"
    echo "=== [$DIR|$key] task=$tid (V4-Flash) running..."
    if [ "$MODE" = "pipeline" ]; then
      body="{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"llm\",\"use_ontology\":$USE,\"mode\":\"pipeline\",\"reviewer_id\":6}"
    else
      body="{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"llm\",\"use_ontology\":$USE}"
    fi
    curl -s --max-time 560 -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" -X POST -d "$body" \
      "$BASE/api/capability/run_for_identity" > "$DIR/$key.json"
    v=$(python -c "import json;d=json.load(open('$DIR/$key.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo PARSE_ERR)
    echo "    -> $v"
  done
}

run_group "$SUITE/results_v4_ont"   "single"   true
run_group "$SUITE/results_v4_none"  "single"   false
run_group "$SUITE/results_v4_pipe1" "pipeline" true
run_group "$SUITE/results_v4_pipe0" "pipeline" false
echo "V4-Flash 4-way batch done"
