# -*- coding: utf-8 -*-
"""验证「数字人 → 模板」反向沉淀（design §16.5）。

判据：**往返一致性** —— 反向生成的模板在「空填写渲染」下必须还原出与原数字人
等价的蓝图（mission/description/prompt/keywords/anchors/ontology/actions）。
否则就是"沉淀时丢了信息"。

跑法（容器内）：
  docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
    python scripts/verify_template_from_identity.py
"""
import sys

sys.path.insert(0, "/app/backend")

from app import db, persona_templates as pt  # noqa: E402

fails: list[str] = []
PAT = "verifyrtx%"


def check(label, ok, extra=""):
    mark = "ok  " if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"   {extra}" if extra else ""))
    if not ok:
        fails.append(label)


def _cleanup(conn):
    conn.execute("DELETE FROM persona_templates WHERE code LIKE ?", (PAT,))
    conn.execute("DELETE FROM persona_ontology WHERE identity_id IN"
                 " (SELECT id FROM identities WHERE name LIKE ?)", (PAT,))
    conn.execute("DELETE FROM persona_actions WHERE identity_id IN"
                 " (SELECT id FROM identities WHERE name LIKE ?)", (PAT,))
    conn.execute("DELETE FROM anchors WHERE identity_id IN"
                 " (SELECT id FROM identities WHERE name LIKE ?)", (PAT,))
    conn.execute("DELETE FROM identities WHERE name LIKE ?", (PAT,))
    conn.commit()


conn = db.get_conn()
_cleanup(conn)

# ---- 取一个真实数字人作为源（优先 DFMEA 工程师）----
src = conn.execute(
    "SELECT * FROM identities WHERE category='general' AND name LIKE ?"
    " ORDER BY id LIMIT 1", ("%DFMEA%",)).fetchone()
if not src:
    src = conn.execute("SELECT * FROM identities ORDER BY id LIMIT 1").fetchone()
src = dict(src)
iid, sname = src["id"], src["name"]
print(f"源数字人：#{iid} 「{sname}」")

src_anchors = [(r["name"], r["type"] or "规则") for r in conn.execute(
    "SELECT name, type FROM anchors WHERE identity_id=? ORDER BY id", (iid,))]
src_ont = [(r["kind"], r["name"], r["definition"] or "") for r in conn.execute(
    "SELECT kind, name, definition FROM persona_ontology WHERE identity_id=?"
    " ORDER BY id", (iid,))]
src_acts = [r["builtin_name"] for r in conn.execute(
    "SELECT builtin_name, kind FROM persona_actions WHERE identity_id=?"
    " ORDER BY id", (iid,)) if r["kind"] == "builtin" and r["builtin_name"]]

print(f"  源：锚点 {len(src_anchors)} · 本体 {len(src_ont)} · 内置动作 {len(src_acts)}")

# ---- [1] 反向生成（不加额外参数化）----
print("[1] identity_to_template（默认只参数化数字人名）")
r1 = pt.identity_to_template(conn, iid, "verifyrtx1", label="验证往返")
check("反向生成成功", r1.get("ok"), str(r1.get("errors") or ""))
if not r1.get("ok"):
    _cleanup(conn)
    sys.exit(1)
tpl = r1["template"]
check("builtin 标记为 False（反推的是用户模板）", tpl["builtin"] is False)
check("含 name 槽位", [s["key"] for s in tpl["slots"]] == ["name"],
      f"slots={[s['key'] for s in tpl['slots']]}")
check("name 槽位 default = 原名",
      tpl["slots"][0]["default"] == sname, tpl["slots"][0]["default"])

# ---- [2] 往返一致性：空填写渲染 == 原数字人 ----
print("[2] 往返一致性（render 空值 vs 原记录）")
r = pt.render(tpl, {})
check("渲染 ok", r.get("ok"), str(r.get("errors") or ""))
check("mission 一致", r["mission"] == (src.get("mission") or "").strip())
check("description 一致",
      r["description"] == (src.get("description") or "").strip())
check("prompt 一致", r["prompt"] == (src.get("prompt") or "").strip())
got_anchors = [(a[0], a[1]) for a in r["anchors"]]
check("anchors 一致", got_anchors == src_anchors,
      f"{len(got_anchors)} vs {len(src_anchors)}")
got_ont = [(o[0], o[1], o[2]) for o in r["ontology"]]
check("ontology 一致（含定义全文）", got_ont == src_ont,
      f"{len(got_ont)} vs {len(src_ont)}")
got_acts = [a.get("builtin_name") for a in r["actions"]]
check("actions 一致", got_acts == src_acts, f"{got_acts} vs {src_acts}")
check("name 还原", r["name"] == sname, r["name"])

# ---- [3] 参数化：把领域词变成槽位 ----
print("[3] 参数化（把源里出现的某个词换成槽位）")
# 找一个在 mission/description 里真实出现的词组作为被参数化对象
import re  # noqa: E402
probe_word = None
for w in ("手机蓝牙模块", "蓝牙", "DFMEA", "手机"):
    if w in (src.get("mission") or "") or w in (src.get("description") or ""):
        probe_word = w
        break
if not probe_word:
    check("找到可参数化词组", False, "源文本里没找到探针词")
else:
    r2 = pt.identity_to_template(
        conn, iid, "verifyrtx2", label="验证参数化",
        parametrize=[{"find": probe_word, "key": "domain", "label": "应用领域"}])
    check("参数化生成成功", r2.get("ok"), str(r2.get("errors") or ""))
    t2 = r2["template"] if r2.get("ok") else None
    keys = [s["key"] for s in (t2 or {}).get("slots", [])]
    check("槽位含 name + domain", keys == ["name", "domain"], f"{keys}")
    if t2:
        rp = pt.render(t2, {})
        check("参数化后空值仍还原原文",
              rp["mission"] == (src.get("mission") or "").strip())
        rn = pt.render(t2, {"domain": "WiFi 模块"})
        check(f"填入新值后 {{domain}} 被替换（不含「{probe_word}」）",
              probe_word not in rn["mission"] and "WiFi 模块" in rn["mission"],
              rn["mission"][:60])
        # 占位符确实进了蓝图（说明是真参数化，不是巧合）
        check("蓝图里存在占位符",
              "{{domain}}" in (t2["blueprint"].get("mission") or ""))

# ---- [4] 反推模板可实例化（端到端）----
print("[4] 反推模板可实例化出新数字人")
if r1.get("ok"):
    r4 = pt.instantiate(conn, tpl, {"name": "verifyrtx新人"})
    check("实例化成功", r4.get("ok"), str(r4.get("errors") or ""))
    if r4.get("ok"):
        check("本体条数一致",
              r4["counts"]["ontology"] == len(src_ont),
              f"{r4['counts']['ontology']} vs {len(src_ont)}")
        check("动作绑定数一致",
              r4["counts"]["actions_bound"] == len(src_acts),
              f"{r4['counts']['actions_bound']} vs {len(src_acts)}")
        check("锚点数一致",
              r4["counts"]["anchors"] == len(src_anchors),
              f"{r4['counts']['anchors']} vs {len(src_anchors)}")

# ---- [5] code 冲突与非法 code ----
print("[5] 边界")
r5 = pt.identity_to_template(conn, iid, "verifyrtx1")
check("同 code 重复生成被拒", not r5.get("ok"))
# 注意：code 会先归一化为小写（与 chunk_types 一致），所以 `BadCode` 是**合法**的、
# 会创建成功 —— 真正被拒的是「数字开头 / 太短 / 含非法字符」。
r6 = pt.identity_to_template(conn, iid, "1bad")
check("非法 code（数字开头）被拒", not r6.get("ok"), str(r6.get("errors") or ""))
r6b = pt.identity_to_template(conn, iid, "ab")
check("非法 code（过短）被拒", not r6b.get("ok"))
r7 = pt.identity_to_template(conn, 99999999, "verifyrtx9")
check("不存在的数字人被拒", not r7.get("ok"))

_cleanup(conn)
print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
