# -*- coding: utf-8 -*-
"""知识摄取官（design §12）：把 RAG 摄取契约装进一个**平台级通用**数字人。

它不执行摄取 —— 摄取仍由确定性 `ingest` job 完成（铁律 L1：代码定路径，LLM
只做节点）。数字人只在三处介入：① 规则定义（本体，经审批生效）② 类型词表维护
③ 复盘提名。本脚本负责 ①，并把词表维护/复盘的入口所需的 action 一并登记。

幂等可重跑；全部为**确定性写入**（不经过 LLM）。

用法（容器内）：
    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python seed_ingest_identity.py
"""
import json

from app import db, trainer

IDENTITY_NAME = "知识摄取官"

IDENTITY = {
    "name": IDENTITY_NAME,
    "mission": "把部门文档切成分段、判明性质、守住证据，产出可检索、可溯源、可审批的知识",
    "description": ("平台级通用数字人：负责 RAG 摄取的规则定义、内容类型词表维护与复盘提名；"
                    "不参与逐条判定 —— 摄取执行由确定性 ingest job 完成"),
    "category": "general",
    "prompt": ("回答时优先引用摄取契约本体；涉及分块/去重/类型判定的问题只陈述规则，"
               "不代替 ingest 链路做逐条裁决；拿不准就说不知道。"),
    "keywords": ["分段", "分段摘要", "内容类型", "置信度", "强制等级",
                 "证据 span", "去重键", "入库契约"],
}

# 锚点 = 核心关注（对话时固定全量注入），只放「最不能违反」的四条
ANCHORS = [
    ("入库契约", "规则"),
    ("类型判定规则", "规则"),
    ("强制注入规则", "规则"),
    ("Chunk 分段单元", "概念"),
]

# 本体段（kind, name, definition）—— design §12.3：8 概念 + 7 规则 = 15 条
ONTOLOGY = [
    ("概念", "Chunk 分段单元",
     "文档切分后的最小检索单位；句边界优先、超长句硬切并保留重叠（默认 800 字 / 重叠 100）"),
    ("概念", "分段摘要",
     "对 chunk 生成的语义浓缩；是 embedding 的向量化对象，不是原文"),
    ("概念", "内容类型 type",
     "chunk_types 词表项；派生「置信度 + 强制等级」两个正交维度"),
    ("概念", "置信度",
     "知识可信程度：high / medium / low；由 type 词表默认值决定，用户可覆盖"),
    ("概念", "强制等级",
     "知识约束力：0 参考 / 1 建议 / 2 强制；=2 时对话必选注入，不受 top-k 截断"),
    ("概念", "证据 span",
     "chunk 内逐字可回溯的字符区间（start/end）；本体候选必须携带"),
    ("概念", "去重键",
     "文档 name：hash 相同则跳过；同名不同 hash 视为冲突，须用户二次确认"),
    ("概念", "入库契约",
     "覆盖语义：DELETE chunks（mentions 级联）+ UPDATE documents（保 id）+ INSERT 新 chunks"),
    ("规则", "分块规则",
     "句边界优先切分；单句超长硬切且相邻块保留重叠，禁止整篇成为一块"),
    ("规则", "类型判定规则",
     "LLM 只提名 type；两个维度一律由词表默认值确定性裁决，提名不直接落库"),
    ("规则", "未知类型不臆造",
     "type 不在 active 词表即置 unknown，禁止临时造类型"),
    ("规则", "证据逐字规则",
     "抽取的实体名称与定义必须在来源 chunk 内逐字命中，否则拒绝入池"),
    ("规则", "embedding 对象规则",
     "只对 summary 向量化（bge-m3 锁定），原文与 tags 不参与向量"),
    ("规则", "强制注入规则",
     "mandatory=2 的 chunk 必选注入上下文，不参与 top-K 截断"),
    ("规则", "覆盖确认规则",
     "同名不同 hash 不静默覆盖，必须先 dry-run 呈现冲突再二次确认"),
]

# 动作（builtin_name, name, description, input_schema）
ACTIONS = [
    ("rag_ingest", "触发摄取",
     "对指定目录触发确定性 ingest job（加载→分段→摘要+类型→嵌入→入库）",
     {"type": "object", "properties": {"work_dir": {"type": "string"}},
      "required": ["work_dir"]}),
    ("chunk_classify", "重判分块类型",
     "对单个 chunk 重新提名 type，并按词表确定性重取置信度与强制等级",
     {"type": "object", "properties": {"chunk_id": {"type": "integer"}},
      "required": ["chunk_id"]}),
    ("chunk_search", "检索分块",
     "按向量/标签/类型检索已入库 chunk，返回带出处的原文片段",
     {"type": "object", "properties": {"query": {"type": "string"},
                                       "top_k": {"type": "integer"}},
      "required": ["query"]}),
    ("ontology_extract", "触发本体抽取",
     "对已入库语料触发本体候选提名 + 三关校验 job",
     {"type": "object", "properties": {}, "required": []}),
]

# 关系：知识摄取官 --owns--> 契约条目（design §12.3）
OWNS_TARGETS = ["入库契约", "类型判定规则", "强制注入规则", "证据逐字规则", "内容类型 type"]


def _upsert_candidate(conn, kind, name, definition, tags=None) -> int:
    """幂等入库本体库（candidates），按 name_norm 去重。"""
    from app.ontology import _norm_name
    norm = _norm_name(name)
    row = conn.execute(
        "SELECT id FROM candidates WHERE name_norm=? ORDER BY id LIMIT 1",
        (norm,)).fetchone()
    if row:
        conn.execute("UPDATE candidates SET kind=?, definition=?, status='approved'"
                     " WHERE id=?", (kind, definition, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, name_norm, definition, status, tags,"
        " created_at) VALUES(?,?,?,?, 'approved', ?, ?)",
        (kind, name, norm, definition, tags or [], db.now()))
    conn.commit()
    return cur.lastrowid


def upsert_identity(conn) -> int:
    row = conn.execute("SELECT id FROM identities WHERE name=?",
                       (IDENTITY_NAME,)).fetchone()
    if row:
        conn.execute(
            "UPDATE identities SET mission=?, description=?, keywords=?, prompt=?,"
            " category=?, status='approved' WHERE id=?",
            (IDENTITY["mission"], IDENTITY["description"],
             json.dumps(IDENTITY["keywords"], ensure_ascii=False),
             IDENTITY["prompt"], IDENTITY["category"], row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO identities(name, mission, description, keywords, prompt,"
        " status, category, created_at) VALUES(?,?,?,?,?, 'approved', ?, ?)",
        (IDENTITY["name"], IDENTITY["mission"], IDENTITY["description"],
         json.dumps(IDENTITY["keywords"], ensure_ascii=False),
         IDENTITY["prompt"], IDENTITY["category"], db.now()))
    conn.commit()
    return cur.lastrowid


def upsert_anchor(conn, iid: int, name: str, atype: str) -> int:
    row = conn.execute("SELECT id FROM anchors WHERE identity_id=? AND name=?",
                       (iid, name)).fetchone()
    if row:
        conn.execute("UPDATE anchors SET type=?, status='approved' WHERE id=?",
                     (atype, row["id"]))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
        " VALUES(?,?,?,?, 'approved', ?)", (iid, name, atype, "", db.now()))
    conn.commit()
    return cur.lastrowid


def upsert_action(conn, iid: int, builtin_name: str, name: str, description: str,
                  schema: dict) -> None:
    payload = json.dumps(schema, ensure_ascii=False)
    conn.execute(
        "INSERT INTO persona_actions(identity_id, name, description, input_schema,"
        " kind, builtin_name, status, created_at)"
        " VALUES(?,?,?,?, 'builtin', ?, 'approved', ?)"
        " ON CONFLICT (identity_id, name) DO UPDATE SET"
        " description=excluded.description, input_schema=excluded.input_schema,"
        " builtin_name=excluded.builtin_name, status='approved'",
        (iid, name, description, payload, builtin_name, db.now()))
    conn.commit()


def main() -> int:
    conn = db.get_conn()

    print("=== 1. 数字人 ===")
    iid = upsert_identity(conn)
    print(f"  #{iid} {IDENTITY_NAME}（category={IDENTITY['category']}）")

    print("\n=== 2. 锚点（核心关注 · 固定注入）===")
    for aname, atype in ANCHORS:
        upsert_anchor(conn, iid, aname, atype)
        print(f"  + {aname}（{atype}）")

    print("\n=== 3. 本体段（摄取契约 15 条）===")
    for kind, oname, defn in ONTOLOGY:
        trainer.add_ontology(conn, iid, kind, oname, defn,
                             note="seed_ingest_identity：摄取契约")
    rows = conn.execute(
        "SELECT kind, COUNT(*) n FROM persona_ontology WHERE identity_id=?"
        " GROUP BY kind ORDER BY kind", (iid,)).fetchall()
    total = 0
    for r in rows:
        print(f"  {r['kind']}: {r['n']}")
        total += r["n"]
    print(f"  本体段合计 {total} 条")

    print("\n=== 4. 动作（builtin）===")
    for builtin_name, name, desc, schema in ACTIONS:
        upsert_action(conn, iid, builtin_name, name, desc, schema)
        print(f"  + {name}（{builtin_name}）")

    print("\n=== 5. 沉淀到全局本体库 + owns 关系 ===")
    role_cid = _upsert_candidate(conn, "角色", IDENTITY_NAME,
                                 IDENTITY["mission"])
    owns = 0
    for oname in OWNS_TARGETS:
        rec = conn.execute(
            "SELECT kind, definition FROM persona_ontology"
            " WHERE identity_id=? AND name=?", (iid, oname)).fetchone()
        if not rec:
            continue
        ocid = _upsert_candidate(conn, rec["kind"], oname, rec["definition"] or "")
        exists = conn.execute(
            "SELECT id FROM relations WHERE source_id=? AND target_name=?"
            " AND relation_type='owns'", (role_cid, oname)).fetchone()
        if not exists:
            conn.execute("INSERT INTO relations(source_id, target_name, relation_type)"
                         " VALUES(?,?, 'owns')", (role_cid, oname))
            owns += 1
    conn.commit()
    print(f"  角色实体 candidate #{role_cid}；新增 owns 关系 {owns} 条")

    print("\n=== 验证 ===")
    d = conn.execute(
        "SELECT name, category, status FROM identities WHERE id=?", (iid,)).fetchone()
    print(f"  identity: {d['name']} / {d['category']} / {d['status']}")
    print("  锚点: " + str(conn.execute(
        "SELECT COUNT(*) n FROM anchors WHERE identity_id=? AND status='approved'",
        (iid,)).fetchone()["n"]))
    print("  本体段: " + str(conn.execute(
        "SELECT COUNT(*) n FROM persona_ontology WHERE identity_id=?",
        (iid,)).fetchone()["n"]))
    print("  动作: " + str(conn.execute(
        "SELECT COUNT(*) n FROM persona_actions WHERE identity_id=?",
        (iid,)).fetchone()["n"]))
    print("\nRESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
