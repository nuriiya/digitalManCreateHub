#!/usr/bin/env bash
# 4 元消融：pipeline 介入两组（writer=代码工程师3 ↔ reviewer=调试工程师6）
#   USE=1 → 本体全量注入（两侧角色） ; USE=0 → 无本体
# 从项目根运行：bash exports/test-suite-project-2026-09-09/run_pipeline.sh
set -u
cd "$(dirname "$0")/../.."
SUITE="exports/test-suite-project-2026-09-09"
for USE in 1 0; do
  DIR="$SUITE/results_pipe${USE}"
  mkdir -p "$DIR"
  BASE="http://localhost:8000"
  TOKEN=$(curl -s --max-time 8 -X POST -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
    | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
  echo "token ok (use_ontology=$USE)"
  TASKS="165:proj_normalize_name 166:proj_parse_dsn 167:proj_paginate 168:proj_dedup_tags 169:proj_jsonb_get 170:proj_chunk_overlap 171:proj_cosine 172:proj_validate_tags 173:proj_safe_truncate 174:proj_extract_code 175:proj_merge_summaries 176:proj_topk"
  for tv in $TASKS; do
    tid="${tv%%:*}"; key="${tv##*:}"
    echo "=== [PIPE$USE|$key] task=$tid running..."
    curl -s --max-time 560 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -X POST -d "{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"ollama\",\"ollama_model\":\"qwen2.5:7b-32k\",\"use_ontology\":$USE,\"mode\":\"pipeline\",\"reviewer_id\":6}" \
      "$BASE/api/capability/run_for_identity" > "$DIR/$key.json"
    v=$(python -c "import json;d=json.load(open('$DIR/$key.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo PARSE_ERR)
    echo "    -> $v"
  done
done
echo "PIPELINE batch done (results_pipe1 = 有本体, results_pipe0 = 无本体)"
