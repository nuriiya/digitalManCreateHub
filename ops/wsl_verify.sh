#!/usr/bin/env bash
# WSL 部署验证
set -uo pipefail
BASE=http://127.0.0.1:8000

echo "=== 1. 登录 + identities ==="
TOKEN=$(curl -s -m 8 -X POST -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"123456"}' "$BASE/api/auth/login" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("token",""))' 2>/dev/null)
echo "token: ${TOKEN:0:16}..."

curl -s -m 8 -H "Authorization: Bearer $TOKEN" "$BASE/api/identities" > /tmp/_ids.json
python3 - << 'PY'
import json
d = json.load(open('/tmp/_ids.json'))
ids = d.get('identities', [])
print(f"identities: {len(ids)}")
for i in ids[:8]:
    print(f"  - id={i['id']} {i['name']} [{i.get('status')}] {i.get('category','')}")
PY

echo ""
echo "=== 2. pipeline ==="
curl -s -m 8 -H "Authorization: Bearer $TOKEN" "$BASE/api/pipelines" > /tmp/_pipes.json
python3 - << 'PY'
import json
d = json.load(open('/tmp/_pipes.json'))
ps = d.get('pipelines', [])
print(f"pipelines: {len(ps)}")
for p in ps:
    print(f"  - id={p['id']} {p['name']} nodes={len(p.get('nodes',[]))} rels={len(p.get('relations',[]))} [{p.get('status')}]")
PY

echo ""
echo "=== 3. 前端 ==="
curl -s -m 5 -o /dev/null -w 'GET / -> %{http_code}\n' "$BASE/"
curl -s -m 5 "$BASE/" | head -c 150
echo ""

echo ""
echo "=== 4. 能力题任务数 ==="
curl -s -m 8 -H "Authorization: Bearer $TOKEN" "$BASE/api/capability/tasks" > /tmp/_tasks.json 2>/dev/null
python3 - << 'PY'
import json
try:
    d = json.load(open('/tmp/_tasks.json'))
    ts = d.get('tasks', d if isinstance(d, list) else [])
    print(f"capability tasks: {len(ts)}")
except Exception as e:
    print("tasks endpoint:", e)
PY

echo ""
echo "=== 5. backend 日志尾部 ==="
docker logs --tail 6 rag_backend 2>&1 | tail -6
