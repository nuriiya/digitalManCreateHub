#!/usr/bin/env bash
# 项目能力题套件批量执行：qwen2.5:7b-32k × identity 3（代码工程师，本体全量注入）
# 从项目根运行：bash exports/test-suite-project-2026-09-09/run.sh
set -u
cd "$(dirname "$0")/../.."   # 回项目根
SUITE="exports/test-suite-project-2026-09-09"
mkdir -p "$SUITE/results"

BASE="http://localhost:8000"
TOKEN=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
  | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
echo "token ok: ${TOKEN:0:8}..."

# 任务 id 与 key（全部项目题，category != code_generation）
TASKS="165:proj_normalize_name 166:proj_parse_dsn 167:proj_paginate 168:proj_dedup_tags 169:proj_jsonb_get 170:proj_chunk_overlap 171:proj_cosine 172:proj_validate_tags 173:proj_safe_truncate 174:proj_extract_code 175:proj_merge_summaries 176:proj_topk"

for tv in $TASKS; do
  tid="${tv%%:*}"; key="${tv##*:}"
  echo "=== [$key] task=$tid running..."
  curl -s --max-time 500 -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -X POST -d "{\"identity_id\":3,\"task_id\":$tid,\"provider\":\"ollama\",\"ollama_model\":\"qwen2.5:7b-32k\"}" \
    "$BASE/api/capability/run_for_identity" > "$SUITE/results/$key.json"
  v=$(python -c "import json;d=json.load(open('$SUITE/results/$key.json'));print(d.get('verdict','ERR'), len(d.get('rounds',[])))" 2>/dev/null || echo "PARSE_ERR")
  echo "    -> $v"
done

echo "=== all done, building summary ==="
python << 'PYEOF'
import json, os
suite = "exports/test-suite-project-2026-09-09"
res = {}
for fn in sorted(os.listdir(os.path.join(suite, "results"))):
    if not fn.endswith(".json"): continue
    key = fn[:-5]
    try: res[key] = json.load(open(os.path.join(suite, "results", fn)))
    except Exception as e: res[key] = {"error": f"unreadable: {e}"}

names = ["proj_normalize_name","proj_parse_dsn","proj_paginate","proj_dedup_tags","proj_jsonb_get",
         "proj_chunk_overlap","proj_cosine","proj_validate_tags","proj_safe_truncate",
         "proj_extract_code","proj_merge_summaries","proj_topk"]
cats = {"proj_normalize_name":"data_cleaning","proj_parse_dsn":"db","proj_paginate":"web","proj_dedup_tags":"data_cleaning",
        "proj_jsonb_get":"db","proj_chunk_overlap":"rag","proj_cosine":"vector","proj_validate_tags":"validation",
        "proj_safe_truncate":"text","proj_extract_code":"text","proj_merge_summaries":"rag","proj_topk":"vector"}
L = []
L.append("# 项目能力题套件 · qwen2.5:7b-32k 结果\n")
L.append("> 数字人：代码工程师（identity 3，14 条本体注入，reactive）· provider=ollama qwen2.5:7b-32k · temperature=0 · 沙箱可执行验证 ≤3 轮")
L.append("> 测试标准见 README-测试标准.md；原始往返见 results/<task>.json\n")
L.append("| task | category | verdict | 轮次 | R1代码长 | 说明 |")
L.append("|---|---|---|---|---|---|")
pass_n = n1 = 0; rounds_sum = 0
for k in names:
    d = res.get(k, {})
    if not d or "error" in d:
        L.append("| `%s` | %s | ERR | - | - | %s |" % (k, cats.get(k), d.get("error"))); continue
    r = d.get("rounds") or []
    rd = len(r); v = d.get("verdict")
    if v == "pass":
        pass_n += 1
        if rd == 1: n1 += 1
    rounds_sum += rd or 1
    fl = len((r[0].get("code") or "") if r else (d.get("reply") or ""))
    seq = "→".join(x["verdict"] for x in r) if r else "-"
    L.append("| `%s` | %s | **%s** | %d | %d | %s |" % (k, cats.get(k), v, rd, fl, seq))
L.append("")
L.append("**总通过率：%d/12（%d%%）· 首轮通过：%d/12（%d%%）· 平均轮次：%.2f**" % (pass_n, round(pass_n*100/12), n1, round(n1*100/12), rounds_sum/12))
L.append("")
L.append("## 未通过题分析\n")
for k in names:
    d = res.get(k, {})
    if not d or d.get("verdict") != "fail": continue
    L.append("### %s（fail）" % k)
    for r in d.get("rounds") or []:
        L.append("- R%d 输出尾部: `%s`" % (r["round"], str(r.get("output") or "")[-260:]))
L.append("\n## 逐题代码（rounds 完整往返见 JSON）")
open(os.path.join(suite, "results", "summary.md"), "w", encoding="utf-8").write("\n".join(L))
print("summary written:", pass_n, "/12")
PYEOF
