# -*- coding: utf-8 -*-
"""通过现有架构生成「自动写代码」pipeline。

数字人 = identities(identity) + persona_ontology(ontology) + persona_actions(actions)
pipeline = pipelines + pipeline_nodes(节点引用数字人) + pipeline_relations(关系闭集)

6 个数字人：3 通用(general) + 3 专业(domain_expert)
流程：需求分析师 → 技术设计 → 代码工程师 → 代码审查员 → 测试工程师 → 调试工程师

关系闭集：supply(供给) / review(复核) / handoff(交接)
反馈循环（调试→重新编码）是执行引擎的迭代，不进静态 DAG（避免成环）。

用法：backend 目录下运行
    python generate_code_pipeline.py
"""
from app import db
from app import identity, pipeline as pl

conn = db.get_conn()

# ---------------- 1. 六个数字人 ----------------
# (node_key, 名称, category, mission, 附加指令 prompt)
PERSONAS = [
    ("requirement_analyst", "需求分析师", "general",
     "把用户的模糊需求转化为结构化需求规格。",
     "你是需求分析师。把模糊需求转化为结构化规格，输出：1)功能点清单 2)验收标准 "
     "3)边界条件 4)非功能需求。不确定就说不知道，禁止臆造。"),
    ("tech_designer", "技术设计工程师", "domain_expert",
     "把需求规格转化为技术方案（模块划分、接口定义、数据结构）。",
     "你是技术设计工程师。把需求规格转化为技术方案，输出：模块划分、接口定义、"
     "数据结构、技术选型。遵循项目技术栈约束（FastAPI + pgvector）。"),
    ("code_engineer", "代码工程师", "domain_expert",
     "把技术方案转化为可运行代码实现。",
     "你是代码工程师。把技术方案转化为可运行代码。只写有依据的代码，不臆造不存在的"
     "API，引用现有代码库时先确认。"),
    ("code_reviewer", "代码审查员", "general",
     "审查代码的正确性、可维护性、安全性。",
     "你是代码审查员。审查代码的正确性、可维护性、安全性、性能，输出分级问题清单。"
     "只提意见，不修改代码。"),
    ("test_engineer", "测试工程师", "domain_expert",
     "依据需求规格设计并执行测试用例。",
     "你是测试工程师。依据需求规格和验收标准设计测试用例并执行，输出：测试用例、"
     "执行报告（通过/失败清单）。用项目测试框架（pytest）。"),
    ("debug_engineer", "调试工程师", "general",
     "定位 bug 根因并产出修复补丁。",
     "你是调试工程师。定位 bug 根因并产出修复补丁。先复现，再定位，再修复，"
     "修复后说明根因和验证方式。"),
]

persona_ids = {}
print("=== 1. 创建数字人 ===")
for key, name, cat, mission, prompt in PERSONAS:
    pid = identity.create_identity(
        conn, name, mission, description=mission,
        prompt=prompt, category=cat)
    if pid is None:
        print(f"  ✗ 创建失败: {name}")
        raise SystemExit(1)
    persona_ids[key] = pid
    print(f"  [{pid}] {name}（{'通用' if cat == 'general' else '专业'}）")

# ---------------- 2. 创建 pipeline ----------------
print("\n=== 2. 创建 pipeline ===")
pl_id = pl.create_pipeline(
    conn, "自动写代码 pipeline",
    "需求分析→技术设计→编码→审查→测试→调试",
    tags=["写代码", "代码生成", "自动编程"])
print(f"  pipeline [{pl_id}]")

# ---------------- 3. 添加节点（引用数字人） ----------------
print("\n=== 3. 添加节点 ===")
node_ids = {}
STEPS = {
    "requirement_analyst": "需求分析",
    "tech_designer": "技术设计",
    "code_engineer": "编码实现",
    "code_reviewer": "代码审查",
    "test_engineer": "测试",
    "debug_engineer": "调试修复",
}
for i, (key, _, _, _, _) in enumerate(PERSONAS):
    nid = pl.add_node(
        conn, pl_id, key, persona_id=persona_ids[key],
        kind=pl.KIND_NOMINATE, step_name=STEPS[key],
        position={"x": 160.0, "y": float(40 + i * 90)})
    node_ids[key] = nid
    print(f"  node [{nid}] {key} -> persona {persona_ids[key]}")

# ---------------- 4. 添加关系（线性 DAG） ----------------
print("\n=== 4. 添加关系 ===")
EDGES = [
    ("requirement_analyst", "tech_designer", pl.RELATION_SUPPLY, "需求规格"),
    ("tech_designer", "code_engineer", pl.RELATION_SUPPLY, "技术方案"),
    ("code_engineer", "code_reviewer", pl.RELATION_REVIEW, "代码实现"),
    ("code_reviewer", "test_engineer", pl.RELATION_HANDOFF, "审查通过代码"),
    ("test_engineer", "debug_engineer", pl.RELATION_REVIEW, "失败测试报告"),
]
for src, dst, rtype, handoff in EDGES:
    rid = pl.add_relation(
        conn, pl_id, node_ids[src], node_ids[dst], rtype,
        handoff_type=handoff)
    print(f"  relation [{rid}] {src} --{rtype}--> {dst}（{handoff}）")

# ---------------- 5. 设置入口/出口 ----------------
pl.update_pipeline(conn, pl_id, {
    "entry_node_id": node_ids["requirement_analyst"],
    "exit_node_id": node_ids["debug_engineer"],
})

# ---------------- 6. 校验 + 批准 ----------------
print("\n=== 6. 校验 + 批准 ===")
errors = pl.validate_pipeline(conn, pl_id)
if errors:
    print("  校验失败：")
    for e in errors:
        print(f"    - {e}")
else:
    pl.approve_pipeline(conn, pl_id)
    print("  ✅ 校验通过，pipeline 已批准（draft → approved）")

# ---------------- 结果汇总 ----------------
print("\n=== 结果 ===")
for r in conn.execute(
    "SELECT id, name, status, tags FROM pipelines WHERE id=?",
    (pl_id,)).fetchall():
    print(f"  pipeline [{r['id']}] {r['name']} | {r['status']} | tags={r['tags']}")
nodes = pl._nodes(conn, pl_id)
rels = pl._relations(conn, pl_id)
print(f"  节点数: {len(nodes)}，关系数: {len(rels)}")
