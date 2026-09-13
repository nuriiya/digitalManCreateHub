# -*- coding: utf-8 -*-
"""验证数字人模板机制（design §16）。

覆盖：内置模板 seed · 槽位替换 · 必填校验 · 领域知识槽位解析 · 预览不落库 ·
实例化幂等 · 自定义模板 CRUD · 内置模板不可删。

测试产生的自定义模板与数字人会清理，不污染真实数据。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_persona_templates.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, persona_templates as pt  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(("    PASS  " if cond else "    FAIL  ") + label + (" " + extra if extra else ""))
    if not cond:
        fails.append(label)


conn = db.get_conn()

print("[1] 内置模板")
tpls = pt.list_templates(conn)
codes = {t["code"] for t in tpls}
check("内置模板已 seed（≥4）", len(tpls) >= 4, f"codes={sorted(codes)}")
check("含 dfmea_engineer / part_expert / dfmea_reviewer",
      {"dfmea_engineer", "part_expert", "dfmea_reviewer"} <= codes)
eng = pt.get_by_code(conn, "dfmea_engineer")
check("DFMEA 工程师模板有槽位", len(eng["slots"]) >= 2,
      f"slots={[s['key'] for s in eng['slots']]}")
# 用「>=」而非硬编码条数：内置模板会随平台演进增删条目（如 §15.7 给 DFMEA 工程师
# 加了「搜索部件清单」），硬编码会让断言随功能升级而假失败。
check("统计字段正确", eng["stats"]["ontology"] > 0 and eng["stats"]["actions"] >= 4,
      f"stats={eng['stats']}")

print("[2] 槽位替换（{{domain}}）")
r = pt.render(eng, {"name": "测试DFMEA", "domain": "手机蓝牙模块"})
check("渲染成功", r.get("ok"), f"{r.get('errors')}")
check("mission 已替换", "手机蓝牙模块" in r["mission"], r["mission"][:40])
check("prompt 已替换", "手机蓝牙模块" in r["prompt"], r["prompt"][:40])
check("本体条数 = 模板自带", len(r["ontology"]) == eng["stats"]["ontology"])

print("[3] 必填校验")
bad = pt.render(eng, {"domain": "X"})          # name 有默认值，不应报错
check("有默认值的必填项不报错", bad.get("ok"))
empty_slots = [{"key": "must", "label": "必填项", "required": True, "default": ""}]
no_default = dict(eng, slots=empty_slots, blueprint={})
r2 = pt.render(no_default, {})
check("缺必填项被拒", not r2.get("ok"), f"{r2.get('errors')}")

print("[4] 领域知识槽位（每行 类型|名称|定义）")
pe = pt.get_by_code(conn, "part_expert")
base = pe["stats"]["ontology"]
r3 = pt.render(pe, {"name": "测试专家", "subsystem": "X",
                    "domain_ontology": "概念|条目A|定义A\n"
                                       "规则|条目B|定义B\n"
                                       "非法类型|条目C|定义C\n"
                                       "坏行没有竖线\n"})
check("解析并追加 3 条（非法类型回落概念、坏行跳过）",
      r3.get("ok") and len(r3["ontology"]) == base + 3,
      f"base={base} -> {len(r3.get('ontology', []))}")
kinds = [o[0] for o in r3["ontology"][base:]]
check("非法类型回落为「概念」", kinds[2] == "概念", f"kinds={kinds}")

print("[5] 实例化 + 幂等")
tmp_name = "_verify_tpl_实例"
r4 = pt.instantiate(conn, pe, {"name": tmp_name, "subsystem": "测试子系统",
                               "domain_ontology": "概念|T|D"})
check("实例化成功", r4.get("ok"), f"{r4.get('errors')}")
iid = r4.get("identity_id")
n_ont_1 = conn.execute("SELECT COUNT(*) c FROM persona_ontology WHERE identity_id=?",
                       (iid,)).fetchone()["c"]
r5 = pt.instantiate(conn, pe, {"name": tmp_name, "subsystem": "测试子系统",
                               "domain_ontology": "概念|T|D"})
same = conn.execute("SELECT id FROM identities WHERE name=?",
                    (tmp_name,)).fetchone()
n_ont_2 = conn.execute("SELECT COUNT(*) c FROM persona_ontology WHERE identity_id=?",
                       (iid,)).fetchone()["c"]
check("重复实例化不产生新数字人", same["id"] == iid)
check("重复实例化不重复本体", n_ont_1 == n_ont_2, f"{n_ont_1} -> {n_ont_2}")
n_act = conn.execute(
    "SELECT COUNT(*) c FROM persona_actions WHERE identity_id=? AND status='approved'",
    (iid,)).fetchone()["c"]
check("动作已绑定且 approved", n_act == 2, f"n={n_act}")

print("[6] 自定义模板 CRUD")
try:
    t = pt.create_template(conn, "verifycustom", "验证模板", "domain_expert",
                           "验证用", [{"key": "name", "label": "名", "required": True}],
                           {"mission": "m", "ontology": [["概念", "A", "B"]]})
    check("创建自定义模板", t and t["code"] == "verifycustom")
except pt.TemplateError as e:
    check("创建自定义模板", False, str(e))
    t = None
r6 = pt.instantiate(conn, t, {"name": "_verify_custom_人"}) if t else {}
check("自定义模板可实例化", r6.get("ok"), f"{r6.get('errors')}")
if t:
    up = pt.update_template(conn, t["id"], {"label": "验证模板改名"})
    check("更新模板", up["label"] == "验证模板改名")
    try:
        pt.delete_template(conn, t["id"])
        check("删除自定义模板", pt.get_by_id(conn, t["id"]) is None)
    except pt.TemplateError as e:
        check("删除自定义模板", False, str(e))

print("[7] 内置模板保护 + code 校验")
try:
    pt.delete_template(conn, eng["id"])
    check("内置模板不可删", False, "竟然删除成功")
except pt.TemplateError:
    check("内置模板不可删", True)
# code **归一化**（大写 -> 小写，与 chunk_types.create_type 一致），非法字符才拒
try:
    t2 = pt.create_template(conn, "VerifyUpper", "x2", blueprint={"mission": "m"})
    check("大写 code 被归一化为小写", t2["code"] == "verifyupper", t2["code"])
except pt.TemplateError as e:
    check("大写 code 被归一化为小写", False, str(e))
try:
    pt.create_template(conn, "1bad", "x3", blueprint={"mission": "m"})
    check("非法 code（数字开头）被拒", False, "竟然通过")
except pt.TemplateError:
    check("非法 code（数字开头）被拒", True)
try:
    pt.create_template(conn, "dupcode", "x4", blueprint={})
    check("空 blueprint 被拒", False, "竟然通过")
except pt.TemplateError:
    check("空 blueprint 被拒", True)

# ---- 清理 ----
# 注意：LIKE 的模式必须**参数化**传入 —— 直接写进 SQL 字符串里的 `%` 会被
# psycopg 当成占位符报 "only '%s' are allowed as placeholders"。
PAT = "\\_verify\\_%"
for sql in (
    "DELETE FROM persona_ontology WHERE identity_id IN"
    " (SELECT id FROM identities WHERE name LIKE ?)",
    "DELETE FROM persona_actions WHERE identity_id IN"
    " (SELECT id FROM identities WHERE name LIKE ?)",
    "DELETE FROM anchors WHERE identity_id IN"
    " (SELECT id FROM identities WHERE name LIKE ?)",
    "DELETE FROM identities WHERE name LIKE ?",
):
    conn.execute(sql, (PAT,))
# 测试模板的 code 一律以 `verify` 开头 —— 用前缀一次性清干净（含早期用例
# `BadCode` 归一化后留下的 `badcode` 历史残留）。
# 注意：`verify%` 必须**作为参数**传入，不能写进 SQL 字面量（`%` 会被 psycopg
# 当成占位符报错 —— 这个坑记在项目记忆里，本次又踩了一次）。
conn.execute("DELETE FROM persona_templates WHERE code LIKE ? OR code = ?",
             ("verify%", "badcode"))
conn.commit()
left = conn.execute("SELECT COUNT(*) c FROM identities WHERE name LIKE ?",
                    (PAT,)).fetchone()["c"]
print(f"\n清理：残留测试数字人 {left} 个")
print("RESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
