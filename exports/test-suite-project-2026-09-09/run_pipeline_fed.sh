#!/usr/bin/env bash
# C 组重跑（reviewer 信息喂饱版）：pipeline 介入 + 本体注入 + 新上下文
#   writer=代码工程师3 ↔ reviewer=调试工程师6, use_ontology=1
# reviewer 现在能看到：任务 docstring + 隐藏测试断言 + 全量失败输出
set -u
cd "$(dirname "$0")/../.."
SUITE="exports/test-suite-project-2026-09-09"
DIR="$SUITE/results_pipe1_fed"
mkdir -p "$DIR"
BASE="http://localhost:8000"
TOKEN=$(curl -s --max-time 8 -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo "token ok (pipeline + fed reviewer)"
TASKS="165:proj_normalize_name 166:proj_parse_dsn 167:proj_paginate 168:proj_dedup_tags 169:proj_jsonb_get 170:proj_chunk_overlap 171:proj_cosine 172:proj_validate_tags 173:proj_safe_truncate 174:proj_extract_code 175:proj_merge_summaries 176:proj_topk"
for tv in $TASKS; do
  tid="${tv%%:*}"; key="${tv##*:}"
  echo "=== [FED|$key] task=$tid running..."
  curl -s --max-time 560 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -X POST -d "{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"ollama\",\"ollama_model\":\"qwen2.5:7b-32k\",\"use_ontology\":true,\"mode\":\"pipeline\",\"reviewer_id\":6}" \
    "$BASE/api/capability/run_for_identity" > "$DIR/$key.json"
  v=$(python -c "import json;d=json.load(open('$DIR/$key.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo PARSE_ERR)
  echo "    -> $v"
done
echo "FED batch done -> results_pipe1_fed"
