# -*- coding: utf-8 -*-
"""给「自动写代码 pipeline」的 6 个数字人装配「询问层级 + 审核门」规则，并在
pipeline 里补 ask 关系（下游向上游提问的层级关系）。

询问层级（谁可以向谁提问，自下而上）：
  需求分析师 → 用户（外部）
  技术设计 → 需求分析师
  代码工程师 → 技术设计
  测试工程师 → 代码工程师
  调试工程师 → 测试工程师
  代码审查员 → 代码工程师

审核门（gate，产出过了审核才能流转）：
  需求分析 → [审核] → 技术设计 → [审核] → 编码 → [代码审查] → 测试 → [验收]
"""
from app import db, trainer, pipeline as pl

# 每个数字人的询问规则 + 审核门规则（kind=规则）
RULES = {
    "需求分析师": [
        ("询问层级：需求分析师可询问用户",
         "当用户需求不明确或缺失关键信息时，主动向用户提问，细化需求后再产出需求规格，不要凭空假设。"),
        ("审核门：需求规格过审才流转",
         "产出需求规格后需经过审核确认（审核门），确认通过后才交接给技术设计工程师。"),
    ],
    "技术设计工程师": [
        ("询问层级：技术设计可询问需求分析师",
         "当需求规格不明确时，向需求分析师提问澄清，不要自行猜测需求意图。"),
        ("审核门：技术方案过审才流转",
         "产出技术方案后需确认（审核门），确认通过后才交接给代码工程师。"),
    ],
    "代码工程师": [
        ("询问层级：代码工程师可询问技术设计工程师",
         "当技术方案不明确时，向技术设计工程师提问澄清，不要自行猜测设计意图。"),
        ("审核门：代码过审才交接",
         "产出代码后需代码审查员审核（审核门），审核通过后才交接给测试工程师，审核驳回则修改后重新提交。"),
    ],
    "代码审查员": [
        ("询问层级：代码审查员可询问代码工程师",
         "审查代码时如有疑问，向代码工程师询问实现意图，不要臆断。"),
        ("审核门：代码审查通过才交接测试",
         "审核代码工程师的代码（审核门）：不通过则驳回要求修改，通过才交接给测试工程师。"),
    ],
    "测试工程师": [
        ("询问层级：测试工程师可询问代码工程师",
         "当代码实现不明确时，向代码工程师提问澄清，不要自行假设行为。"),
        ("审核门：测试通过才验收",
         "测试全部通过才验收交付（审核门），失败则交接给调试工程师定位修复。"),
    ],
    "调试工程师": [
        ("询问层级：调试工程师可询问测试工程师",
         "定位失败时向测试工程师询问失败详情与复现步骤，不要凭空猜测。"),
        ("审核门：修复后交回代码工程师重新走审核",
         "修复补丁交回代码工程师，重新走代码审查与测试的审核门，直到通过。"),
    ],
}

# 询问层级 ask 关系（from_node_key -> to_node_key，下游问上游）
ASK_RELATIONS = [
    ("tech_designer", "requirement_analyst"),
    ("code_engineer", "tech_designer"),
    ("test_engineer", "code_engineer"),
    ("debug_engineer", "test_engineer"),
    ("code_reviewer", "code_engineer"),
]


def main():
    conn = db.get_conn()
    # 1. 数字人本体规则
    print("=== 1. 装配询问层级 + 审核门规则 ===")
    for name, rules in RULES.items():
        ident = conn.execute("SELECT id FROM identities WHERE name=?",
                             (name,)).fetchone()
        if not ident:
            print(f"  [跳过] {name} 不存在")
            continue
        for rname, rdef in rules:
            trainer.add_ontology(conn, ident["id"], "规则", rname, rdef,
                                 note="询问层级+审核门")
        print(f"  {name}：+{len(rules)} 条规则")

    # 2. pipeline ask 关系（询问层级）
    print("\n=== 2. pipeline ask 关系（询问层级）===")
    pipe = conn.execute("SELECT id FROM pipelines WHERE status='approved'"
                        " ORDER BY id LIMIT 1").fetchone()
    if not pipe:
        print("  无已批准 pipeline")
        return
    pid = pipe["id"]
    node_key_to_id = {n["node_key"]: n["id"] for n in pl._nodes(conn, pid)}
    # 已有关系（避免重复）
    existing = {(r["from_node_id"], r["to_node_id"], r["relation_type"])
                for r in pl._relations(conn, pid)}
    for frm, to in ASK_RELATIONS:
        fid, tid = node_key_to_id.get(frm), node_key_to_id.get(to)
        if not fid or not tid:
            print(f"  [跳过] {frm}->{to} 节点缺失")
            continue
        if (fid, tid, "ask") in existing:
            print(f"  [已存在] {frm} --ask--> {to}")
            continue
        pl.add_relation(conn, pid, fid, tid, "ask")
        print(f"  {frm} --ask--> {to}")

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
