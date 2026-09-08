# -*- coding: utf-8 -*-
"""给数字人填本体 + 把本体/数字人关系沉淀到本体库（candidates）。

三件事：
  1. 给本体为空的数字人（需求分析师/技术设计/代码审查员/训练师）填本体段
     （persona_ontology），补齐"每个数字人都有知识"。
  2. 把所有数字人的本体段沉淀到全局本体库（candidates 表，status=approved），
     让本体成为跨数字人可复用/可引用的资产，而非锁死在单个数字人里。
  3. 数字人与数字人的关系（谁依赖谁、谁向谁交接）作为 type=「组织架构」的
     本体条目录入本体库——这是整个体系的骨架，优先级最高。

铁律：所有写入都是确定性代码（不经过 LLM），name_norm 去重、幂等可重跑。

用法：backend 目录下
    python seed_ontology.py
"""
from app import db, identity, trainer

# 数字人之间的组织关系（pipeline 上下游）：(源数字人名, 关系, 目标数字人名, 说明)
ORG_RELATIONS = [
    ("需求分析师", "supply", "技术设计工程师", "需求分析师产出结构化需求规格，供给技术设计"),
    ("需求分析师", "supply", "测试工程师", "需求分析师产出验收标准，供给测试工程师写用例"),
    ("技术设计工程师", "supply", "代码工程师", "技术设计产出技术方案，供给代码工程师实现"),
    ("代码工程师", "review", "代码审查员", "代码工程师产出代码，交代码审查员复核"),
    ("代码审查员", "handoff", "测试工程师", "审查通过的代码交接给测试工程师验证"),
    ("测试工程师", "review", "调试工程师", "测试失败报告交调试工程师定位修复"),
    ("调试工程师", "handoff", "代码工程师", "调试产出修复补丁，交回代码工程师重新生成"),
]

# 每个数字人的本体段（kind/name/definition）——补齐空本体 + 补充已有
PERSONA_ONTOLOGY = {
    1: [  # 需求分析师
        ("规则", "需求结构化方法", "把模糊需求拆成功能点/验收标准/边界条件/非功能需求四要素，用 INVEST 标准校验"),
        ("规则", "验收标准可测试", "每个需求必须能写出可验证的验收标准，否则需求不可交付"),
        ("概念", "边界条件", "需求的边界条件要显式声明（空输入/异常/并发/权限），未声明的边界等于没有需求"),
        ("流程", "需求对齐流程", "先对齐目标再拆功能，先定验收再定实现，需求变更必须回流规格"),
    ],
    2: [  # 技术设计工程师
        ("规则", "技术方案要素", "技术方案必须包含模块划分/接口定义/数据结构/技术栈选型，缺一不可"),
        ("概念", "技术栈约束", "设计必须遵守项目技术栈约束（FastAPI + pgvector + PostgreSQL），不引入未声明依赖"),
        ("流程", "设计评审流程", "方案先评审再实现，接口定义先定契约再写代码，避免返工"),
    ],
    3: [  # 代码工程师（已有安全边界，补充编程规范）
        ("规则", "输出纯代码", "只输出纯 Python 代码，不带解释和 markdown 标记，引号括号成对闭合"),
        ("规则", "边界条件处理", "空输入/单元素/负数/去重/截断等边界必须显式处理，不能只写主路径"),
    ],
    4: [  # 代码审查员
        ("规则", "审查四维度", "审查正确性/可维护性/安全性/性能四维度，按严重性分级输出问题清单"),
        ("规则", "只提意见不改码", "审查员只提审查意见，不直接修改代码，修改权在代码工程师"),
        ("概念", "严重性分级", "问题按阻塞/严重/轻微三级分类，阻塞问题必须修复才能通过"),
    ],
    5: [  # 测试工程师（已有安全边界，补充测试方法论）
        ("规则", "测试用例设计", "用等价类划分+边界值分析设计用例，覆盖正常/边界/异常三类"),
        ("流程", "测试执行流程", "先写用例再执行，失败必须记录期望值/实际值/堆栈，全绿才算通过"),
    ],
    6: [  # 调试工程师（已有安全边界，补充调试方法论）
        ("规则", "根因分析", "先定位根因再修复，不靠试错；读报错堆栈定位到具体行"),
        ("流程", "调试闭环", "复现失败→定位根因→修复→回归验证，修复后必须跑回归测试确认转绿"),
    ],
    7: [  # Pipeline 训练师
        ("流程", "训练闭环", "找训练集→建基线→失败归因→装配本体→复测，循环到无提升或达标"),
        ("规则", "训练师只教学", "训练师只装配本体教学，不裁决；通过率提升由确定性代码统计，不靠 LLM 自评"),
        ("概念", "能力题", "能力题=任务+隐藏断言测试，判定走可执行验证（跑 assert）而非 LLM 猜"),
    ],
}


def _upsert_candidate(conn, kind, name, definition, tags=None) -> int:
    """幂等入库本体库（candidates），按 name_norm 去重。返回 candidate_id。"""
    from app.ontology import _norm_name
    norm = _norm_name(name)
    row = conn.execute(
        "SELECT id FROM candidates WHERE name_norm=? ORDER BY id LIMIT 1",
        (norm,)).fetchone()
    if row:
        conn.execute(
            "UPDATE candidates SET kind=?, definition=?, status='approved' WHERE id=?",
            (kind, definition, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, name_norm, definition, status, tags,"
        " created_at) VALUES(?,?,?,?, 'approved', ?, ?)",
        (kind, name, norm, definition, tags or [], db.now()))
    conn.commit()
    return cur.lastrowid


def main():
    conn = db.get_conn()

    print("=== 1. 给数字人填本体段 ===")
    for iid, items in PERSONA_ONTOLOGY.items():
        name = conn.execute("SELECT name FROM identities WHERE id=?",
                            (iid,)).fetchone()["name"]
        added = 0
        for kind, oname, defn in items:
            n = trainer.add_ontology(conn, iid, kind, oname, defn,
                                     note="seed_ontology 初始本体")
            added += 1 if n else 0
        print(f"  #{iid} {name}: 新增 {added} 段本体")

    print("\n=== 2. 数字人本体沉淀到本体库（candidates）===")
    cand_count = 0
    id_to_cand = {}  # identity_id -> candidate_id（用于组织关系映射）
    id_to_ont_cands = {}  # identity_id -> [本体段 candidate_id]
    for iid in range(1, 8):
        ident = conn.execute(
            "SELECT name, mission FROM identities WHERE id=?", (iid,)).fetchone()
        if not ident:
            continue
        # 数字人本身作为「角色」本体入库
        cid = _upsert_candidate(conn, "角色", ident["name"],
                                ident["mission"] or "数字人")
        id_to_cand[iid] = cid
        cand_count += 1
        # 该数字人的本体段入库（记录 candidate id，用于挂载关系）
        id_to_ont_cands[iid] = []
        for o in conn.execute(
                "SELECT kind, name, definition FROM persona_ontology"
                " WHERE identity_id=? AND status='active'", (iid,)).fetchall():
            kind = o["kind"] if o["kind"] in ("组织架构", "角色", "规则", "系统",
                                              "流程", "概念", "对象", "其他") else "概念"
            ocid = _upsert_candidate(conn, kind, o["name"], o["definition"] or "")
            id_to_ont_cands[iid].append(ocid)
            cand_count += 1
    print(f"  入库 {cand_count} 个本体（含数字人角色实体）")

    print("\n=== 2.5 本体段挂载到所属数字人（role --owns--> ontology）===")
    owns_count = 0
    for iid, ocids in id_to_ont_cands.items():
        role_cid = id_to_cand[iid]
        role_name = conn.execute("SELECT name FROM candidates WHERE id=?",
                                 (role_cid,)).fetchone()["name"]
        for ocid in ocids:
            ont_name = conn.execute("SELECT name FROM candidates WHERE id=?",
                                    (ocid,)).fetchone()["name"]
            exists = conn.execute(
                "SELECT id FROM relations WHERE source_id=? AND target_name=?"
                " AND relation_type='owns'", (role_cid, ont_name)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO relations(source_id, target_name, relation_type)"
                    " VALUES(?,?, 'owns')", (role_cid, ont_name))
                owns_count += 1
    conn.commit()
    print(f"  挂载 {owns_count} 条 owns 关系（角色 → 本体段）")

    print("\n=== 3. 数字人组织关系入库（type=组织架构）===")
    name_to_cand = {}
    for iid, cid in id_to_cand.items():
        nm = conn.execute("SELECT name FROM identities WHERE id=?",
                          (iid,)).fetchone()["name"]
        name_to_cand[nm] = cid

    rel_count = 0
    for src, rtype, tgt, desc in ORG_RELATIONS:
        if src not in name_to_cand or tgt not in name_to_cand:
            continue
        # 组织关系作为「组织架构」type 的本体条目入库
        rel_name = f"{src}→{tgt}"
        _upsert_candidate(conn, "组织架构", rel_name, desc)
        rel_count += 1
        # 同时写 relations 表（source 是源数字人的角色实体，target 是目标名）
        sid = name_to_cand[src]
        exists = conn.execute(
            "SELECT id FROM relations WHERE source_id=? AND target_name=?"
            " AND relation_type=?", (sid, tgt, rtype)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO relations(source_id, target_name, relation_type)"
                " VALUES(?,?,?)", (sid, tgt, rtype))
    conn.commit()
    print(f"  入库 {rel_count} 条组织架构关系")

    # 验证
    print("\n=== 验证 ===")
    for r in conn.execute(
            "SELECT kind, COUNT(*) n FROM candidates GROUP BY kind ORDER BY n DESC").fetchall():
        print(f"  candidates[{r['kind']}]: {r['n']}")
    print(f"  relations 总数: {conn.execute('SELECT COUNT(*) n FROM relations').fetchone()['n']}")


if __name__ == "__main__":
    main()
