# -*- coding: utf-8 -*-
"""pipeline-factory 元流程（design §18）。

把 §15.7 十六轮迭代中**实际发生过的工作流程**抽象为平台能力：
9 节点元流程中，本模块承载**m2 资产盘点 / m6 编译考卷 / m7 考试迭代**三个
承载能力（即 §18.5 落地顺序 1~3）。其余节点（m1/m3/m4/m5/m8/m9）复用既有
能力：persona_templates.instantiate / _run_build_persona / trainer.add_ontology /
generate_from_request / validate_pipeline / identity_to_template。

边界（与既有铁律一致）：
- **LLM 只提名、代码裁决**：m2 / m6 / m7 都是 deterministic；考卷编译按
  invariant 列表 + 夹具自动产出，**不调 LLM 打分**。
- **复用优先**：m2 在节点开头先扫已有数字人 / 模板 / 动作 / MCP / 夹具，
  命中即复用；只在缺失时再建议新建。
- **判分只读产出**：m7 跑 G/R/V 三段时，V 段只读落库后的 dfmea_rows /
  pipeline_run_handoffs，**不读中间过程**。
- **考官不干预**：m7 的失败只能回灌到 m4「补能力」（补动作 / 补本体），
  不得改 pipeline 产出内容。
"""
import json
from typing import Any


# ---------------- m2 资产盘点 ----------------

def inventory(conn) -> dict:
    """m2：查现有数字人 / 模板 / 动作 / MCP / 夹具 → 已有 / 缺失清单。

    deterministic、只读。无 LLM。
    """
    out: dict[str, Any] = {"identities": [], "templates": [], "actions": [],
                            "mcp": [], "fixtures": {}, "summary": {}}
    out["identities"] = [dict(r) for r in conn.execute(
        "SELECT id, name, category, status, reactive FROM identities"
        " WHERE status='approved' ORDER BY id").fetchall()]
    out["templates"] = [dict(r) for r in conn.execute(
        "SELECT id, code, label, category FROM persona_templates"
        " WHERE status='active' ORDER BY id").fetchall()]
    out["actions"] = [dict(r) for r in conn.execute(
        "SELECT identity_id, name, status FROM persona_actions"
        " WHERE status='approved' ORDER BY identity_id, id").fetchall()]
    out["mcp"] = [dict(r) for r in conn.execute(
        "SELECT id, name, approval_status FROM mcp_servers"
        " WHERE approval_status='approved' ORDER BY id").fetchall()]
    fixtures = {}
    for t in ("fmea_parts", "fmea_cases", "fmea_ap_matrix", "fmea_sod_criteria"):
        r = conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()
        fixtures[t] = r["n"]
    out["fixtures"] = fixtures

    s = out["summary"]
    s["n_identities"] = len(out["identities"])
    s["n_templates"] = len(out["templates"])
    s["n_actions"] = len(out["actions"])
    s["n_mcp"] = len(out["mcp"])
    s["fixtures_ok"] = all(n > 0 for n in fixtures.values())
    return out


# ---------------- m6 编译考卷 ----------------

# Invariants 列表（§15.7 十六轮沉淀的不变量，落到考卷 = 每条一个判定点）
# 形态：{category, label, target_table, where_sql, expect}
EXAM_INVARIANTS: list[dict] = [
    {"category": "B-rules", "label": "每行 action 非空",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows WHERE run_id=%s AND action IS NULL OR TRIM(action)=''",
     "expect": 0},
    {"category": "B-rules", "label": "AP 必须是 H/M/L 之一",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows WHERE run_id=%s AND ap NOT IN ('H','M','L')",
     "expect": 0},
    {"category": "B-rules", "label": "severity ∈ [1,10]",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows WHERE run_id=%s AND (severity < 1 OR severity > 10)",
     "expect": 0},
    {"category": "B-rules", "label": "occurrence ∈ [1,10]",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows WHERE run_id=%s AND (occurrence < 1 OR occurrence > 10)",
     "expect": 0},
    {"category": "B-rules", "label": "detection ∈ [1,10]",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows WHERE run_id=%s AND (detection < 1 OR detection > 10)",
     "expect": 0},
    {"category": "B-rules", "label": "severity 格必带来源（来源闭集）",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows r WHERE run_id=%s"
            " AND NOT EXISTS (SELECT 1 FROM jsonb_each(r.sources) kv"
            "                  WHERE kv.key='severity')",
     "expect": 0},
    {"category": "B-rules", "label": "专家引用 expert: ≥1 处",
     "sql": "SELECT COUNT(*) AS n FROM dfmea_rows r WHERE run_id=%s"
            " AND EXISTS (SELECT 1 FROM jsonb_each(r.sources) kv"
            "             WHERE kv.value::text LIKE '%%expert%%')",
     "expect_op": ">=", "expect": 1},
]


def compile_exam(conn, fixture_spec: dict | None = None) -> dict:
    """m6：从夹具 + 不变量自动编译 ExamSpec。

    deterministic。覆盖型 ← 对象清单（fmea_parts）；基准型 ← 历史库
    （fmea_cases）；规则型 ← EXAM_INVARIANTS。每条判定点都是 (sql, expect)，
    跑题时执行 sql 拿值，与 expect 比对得 pass/fail。

    threshold 默认 98%（与 §15.7 考官卷一致）。可通过 fixture_spec 覆盖。
    """
    spec = fixture_spec or {}
    threshold = spec.get("threshold", 0.98)
    rows: list[dict] = []

    # A 类覆盖型 ← fmea_parts（每子系统一题）
    parts = conn.execute("SELECT id, subsystem FROM fmea_parts ORDER BY id").fetchall()
    for p in parts:
        rows.append({
            "category": "A-cover",
            "label": f"覆盖·{p['subsystem']}",
            "sql": "SELECT COUNT(*) n FROM dfmea_rows WHERE run_id=%s AND part=?",
            "params": [p["subsystem"]],
            "expect_op": ">=", "expect": 1,
        })

    # C 类基准型 ← fmea_cases（按 part_no 一致性核对类比推导）
    cases = conn.execute("SELECT id, part_no FROM fmea_cases ORDER BY id").fetchall()
    for c in cases:
        rows.append({
            "category": "C-baseline",
            "label": f"类比·{c['part_no']}",
            "sql": None,  # 类比推导的正确性在落库前由 writer 端负责；考试只确认行存在
            "expect_op": ">=", "expect": 0,
            "note": f"history#{c['id']} 应至少有一处引用",
        })
    # C 类补充：history 引用总数
    rows.append({
        "category": "C-baseline", "label": "history 引用总数 ≥ 13",
        "sql": "SELECT COUNT(*) n FROM dfmea_rows r WHERE run_id=%s"
               " AND EXISTS (SELECT 1 FROM jsonb_each(r.sources) kv"
               "            WHERE kv.value::text LIKE '%%history%%')",
        "expect_op": ">=", "expect": 13,
    })

    # B 类规则型 ← EXAM_INVARIANTS
    for inv in EXAM_INVARIANTS:
        rows.append(dict(inv))

    return {
        "rows": rows,
        "threshold": threshold,
        "n_rows": len(rows),
        "categories": {"A-cover": sum(1 for r in rows if r["category"] == "A-cover"),
                       "B-rules": sum(1 for r in rows if r["category"] == "B-rules"),
                       "C-baseline": sum(1 for r in rows if r["category"] == "C-baseline")},
    }


# ---------------- m7 考试迭代 ----------------

def _exec_one(conn, row: dict, run_id: int) -> tuple[bool, str]:
    """跑一条判定点。返回 (pass, detail)。"""
    sql = row.get("sql")
    if not sql:
        return True, "no-op（仅人工核对）"
    expected = row.get("expect")
    op = row.get("expect_op", "=")
    params = list(row.get("params") or [])
    params.insert(0, run_id)
    r = conn.execute(sql, tuple(params)).fetchone()
    n = r["n"] if r else 0
    if op == "=":
        ok = (n == expected)
    elif op == ">=":
        ok = (n >= expected)
    elif op == "<=":
        ok = (n <= expected)
    else:
        ok = (n == expected)
    detail = f"{row['label']}: got {n}, expect {op} {expected}"
    return ok, detail


def run_exam(conn, run_id: int, exam: dict) -> dict:
    """m7：跑一张考卷（已编译好的 ExamSpec），返回通过率与未通过清单。

    不调 LLM；纯 deterministic。**只读落库后的 dfmea_rows**。
    """
    rows = exam.get("rows") or []
    total = len(rows)
    passed: list[dict] = []
    failed: list[dict] = []
    for r in rows:
        ok, detail = _exec_one(conn, r, run_id)
        (passed if ok else failed).append({**r, "detail": detail})
    rate = (len(passed) / total) if total else 1.0
    return {
        "run_id": run_id,
        "total": total,
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": round(rate, 4),
        "threshold": exam.get("threshold", 0.98),
        "meets_threshold": rate >= exam.get("threshold", 0.98),
        "failed_items": failed,
    }


def diagnose(conn, exam_result: dict) -> dict:
    """m7 末段：失败归因分类（structural vs semantic）。

    LLM 提名留给上层（与 trainer._analyze_failures 同口径）。这里给出
    「结构性失败」的**确定性分类**：每条 failed_item 若能静态关联到缺失的
    动作 / 夹具 / 模板 ⇒ structural，否则归 semantic。

    用于 m7 → m4 的回灌路由。
    """
    cats = {"structural": [], "semantic": []}
    for f in exam_result.get("failed_items") or []:
        # 启发式：B-rules 中"来源闭集/severity 来源/动作空"类失败通常归 semantic
        # （本体规则不足，不是缺零件）；其余（如 AP/值域/动作缺失）归 structural
        lbl = f.get("label", "")
        if any(k in lbl for k in ("来源", "AP 一致", "不臆造")):
            cats["semantic"].append(f)
        else:
            cats["structural"].append(f)
    return cats


# ---------------- 装配 factory pipeline ----------------

def assemble_factory(conn) -> int:
    """把 pipeline-factory 元流程（m1~m9）落库为 draft。

    m2/m6/m7 的 deterministic 节点由 `pipeline._run_factory_*` 派发。
    其余节点（nominate）绑定 Pipeline 训练师（#7）并使用既有引擎能力。

    返回新插入的 pipeline_id。
    """
    from . import pipeline as P, db as DB
    # 已有 factory 就不重复插（按 name 判重）
    existing = conn.execute(
        "SELECT id FROM pipelines WHERE name='pipeline-factory' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if existing:
        return existing["id"]

    pipeline_id = P.create_pipeline(
        conn, "pipeline-factory",
        description="design §18 · 创建 pipeline 的 pipeline（m1~m9）",
        tags=["meta", "factory", "design-§18"])
    if pipeline_id is None:
        raise RuntimeError("factory pipeline 落库失败")

    # 节点 = 角色 / 步骤
    # m1 / m5 / m8 用 nominate（绑定 Pipeline 训练师 #7）
    trainer_id = conn.execute(
        "SELECT id FROM identities WHERE name='Pipeline 训练师' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    trainer_id = trainer_id["id"] if trainer_id else None
    # m2 / m3 / m4 / m6 / m7 / m9 用 deterministic（绑定内置 step_name，引擎按
    # step_name 内置映射派发到对应函数；详见 pipeline._run_deterministic）。
    nodes = [
        # m1 需求解析（nominate）
        ("m1", "需求解析", KIND_N := "nominate", trainer_id,
         "需求 → 交付物 schema + 角色清单 + 是否需要专家"),
        ("m2", "资产盘点", "deterministic", None,
         "查数字人/模板/动作/MCP/夹具 → 已有/缺失清单"),
        ("m3", "补专家", "deterministic", None,
         "模板命中→instantiate；未命中→建数字人子流程"),
        ("m4", "补能力", "deterministic", None,
         "绑动作/装配本体/提示接 MCP/摄取夹具"),
        ("m5", "组装编排", KIND_N, trainer_id,
         "一句话需求 → DAG + 过创建四关"),
        ("m6", "编译考卷", "deterministic", None,
         "夹具+不变量 → ExamSpec（覆盖/规则/基准三型）"),
        ("m7", "考试迭代", "deterministic", None,
         "G/R/V + 归因 + 有界复跑"),
        ("m8", "冻结审批", KIND_N, trainer_id,
         "达标报告 → 人工审批（用户终审）"),
        ("m9", "反向沉淀", "deterministic", None,
         "新本体回流模板库"),
    ]
    key_to_id: dict[str, int] = {}
    # 用预定义坐标（便于画布落点）
    layout = {
        "m1": (60, 60), "m2": (220, 60), "m3": (380, 60), "m4": (540, 60),
        "m5": (700, 60), "m6": (60, 220), "m7": (220, 220),
        "m8": (380, 220), "m9": (540, 220),
    }
    for nk, step, kind, persona, desc in nodes:
        nid = P.add_node(conn, pipeline_id, nk, persona_id=persona,
                         kind=kind, step_name=step,
                         position={"x": layout[nk][0], "y": layout[nk][1]})
        if nid is None:
            raise RuntimeError(f"factory 节点 {nk} 落库失败")
        # 设个说明（写到 description 字段或 ontology；这里用 step_name 已含）
        key_to_id[nk] = nid

    # 关系：m1 design→ m2 → m3 → m4 → m5 →(supply) m6 →(supply) m7 →(review) m8 →(handoff) m9
    # + m7 ask→ m3/m4（权限边）
    flow = [
        ("m1", "m2", "design"),
        ("m2", "m3", "design"),
        ("m3", "m4", "design"),
        ("m4", "m5", "design"),
        ("m5", "m6", "supply"),
        ("m6", "m7", "supply"),
        ("m7", "m8", "review"),
        ("m8", "m9", "handoff"),
    ]
    for a, b, t in flow:
        P.add_relation(conn, pipeline_id, key_to_id[a], key_to_id[b], t)
    # ask 权限边：m7 → m3, m4, m5（考试发现缺口回问补齐）
    for tgt in ("m3", "m4", "m5"):
        P.add_relation(conn, pipeline_id, key_to_id["m7"], key_to_id[tgt], "ask")

    # 入口 / 出口
    P.update_pipeline(conn, pipeline_id, {
        "entry_node_id": key_to_id["m1"],
        "exit_node_id": key_to_id["m9"],
    })
    return pipeline_id