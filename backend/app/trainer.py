# -*- coding: utf-8 -*-
"""Pipeline 训练师：训练特定 pipeline（找训练集 → 建基线 → 迭代 → 更新数字人）。

训练师的训练闭环：
  1. 找训练集：capability_tasks 按 persona_role 选能力题
  2. 建基线：目标数字人批量跑题，测通过率
  3. 迭代：给目标数字人装配本体（训练师的知识教学）
  4. 重新测：对比通过率，算出提升率

铁律：训练师只「教学」（装配本体），本体仍需三关校验 + 用户审批；每次
迭代的通过率提升是确定性代码统计，不是 LLM 自评。
"""
from . import db, capability, identity

TRAINER_NAME = "Pipeline 训练师"


def ensure_trainer(conn) -> int:
    """确保训练师数字人存在（幂等，自动创建），返回 identity_id。"""
    r = conn.execute("SELECT id FROM identities WHERE name=? ORDER BY id LIMIT 1",
                     (TRAINER_NAME,)).fetchone()
    if r:
        return r["id"]
    return identity.create_identity(
        conn, TRAINER_NAME,
        mission="训练 pipeline：寻找训练集、建立基线、迭代 pipeline、更新数字人",
        description="平台通用数字人，负责训练特定的 pipeline，通过给数字人装配本体来迭代提升能力。",
        prompt="你是 Pipeline 训练师。负责训练 pipeline 里的数字人：找训练集、建基线、迭代、更新。",
        category="general")


def list_train_sets(conn) -> list[dict]:
    """训练集：能力题按 persona_role 分组统计。"""
    return [dict(r) for r in conn.execute(
        "SELECT persona_role, COUNT(*) AS n FROM capability_tasks"
        " GROUP BY persona_role ORDER BY persona_role").fetchall()]


def list_tasks_by_role(conn, persona_role) -> list[dict]:
    """某角色的能力题（训练集）。"""
    return [dict(r) for r in conn.execute(
        "SELECT id, task_key, entry_point FROM capability_tasks WHERE persona_role=?"
        " ORDER BY id", (persona_role,)).fetchall()]


def baseline(conn, identity_id, task_ids, provider="llm") -> dict:
    """建基线：批量跑题，返回通过率 + 逐题结果。"""
    results = []
    for tid in task_ids:
        r = capability.run_for_identity(conn, identity_id, tid, provider=provider)
        results.append({"task_id": tid, "verdict": r.get("verdict")})
    passed = sum(1 for x in results if x["verdict"] == "pass")
    return {"total": len(results), "pass": passed,
            "pass_rate": round(passed / len(results), 4) if results else None,
            "results": results}


def add_ontology(conn, identity_id, kind, name, definition, note="") -> int:
    """给数字人装配本体段（幂等：同 kind+name 已存在则复用）。"""
    exists = conn.execute(
        "SELECT id FROM persona_ontology WHERE identity_id=? AND kind=? AND name=?",
        (identity_id, kind, name)).fetchone()
    if exists:
        return exists["id"]
    cur = conn.execute(
        "INSERT INTO persona_ontology(identity_id, kind, name, definition, status,"
        " note, created_at) VALUES(?,?,?,?,'active',?,?)",
        (identity_id, kind, name, definition, note, db.now()))
    conn.commit()
    return cur.lastrowid


def list_ontology(conn, identity_id) -> list[dict]:
    """数字人的本体段。"""
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, name, definition FROM persona_ontology WHERE identity_id=?"
        " AND status='active' ORDER BY id", (identity_id,)).fetchall()]


def train_iteration(conn, trainer_id, target_identity_id, task_ids,
                    provider="llm", ontology_seeds=None) -> dict:
    """训练迭代：给目标数字人装配本体（训练师教学），再重新测对比基线。

    ontology_seeds = [{"kind","name","definition"}, ...] 本轮要教的本体段。
    返回基线、迭代后、装配数、提升率。
    """
    base = baseline(conn, target_identity_id, task_ids, provider)
    added = 0
    for o in (ontology_seeds or []):
        if not o.get("name"):
            continue
        add_ontology(conn, target_identity_id, o.get("kind", "规则"),
                     o["name"], o.get("definition", ""),
                     note=f"训练师 #{trainer_id} 教学")
        added += 1
    after = baseline(conn, target_identity_id, task_ids, provider)
    base_rate = base["pass_rate"] or 0
    after_rate = after["pass_rate"] or 0
    return {
        "identity_id": target_identity_id,
        "baseline": base,
        "after": after,
        "added_ontology": added,
        "improvement": round(after_rate - base_rate, 4),
    }


def report(conn, identity_id, task_ids, provider="llm") -> dict:
    """训练报告：当前通过率 + 本体数 + 本体清单。"""
    b = baseline(conn, identity_id, task_ids, provider)
    ont = list_ontology(conn, identity_id)
    return {"identity_id": identity_id, "baseline": b,
            "ontology_count": len(ont), "ontology": ont}
