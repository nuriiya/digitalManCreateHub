# -*- coding: utf-8 -*-
"""Pipeline 训练师：训练特定 pipeline（找训练集 → 建基线 → 迭代 → 更新数字人）。

训练师的训练闭环：
  1. 找训练集：capability_tasks 按 persona_role 选能力题
  2. 建基线：目标数字人批量跑题，测通过率
  3. 迭代：给目标数字人装配本体（训练师的知识教学）
  4. 重新测：对比通过率，算出提升率

自动迭代（auto_iterate）——「测试驱动本体生成」的核心：
  跑题 → 失败归因（LLM 分析错误输出，提名本体段）→ 三关校验 → 装配 →
  复测 → 对比提升率。循环直到无提升或达轮数上限。

铁律：归因 LLM 只「提名」本体段（不裁决）；复测通过率是确定性代码统计
（跑 assert），不是 LLM 自评。提名本体仍需过三关（kind 闭集 + 非空 + 去重）。
"""
import threading
import time
import uuid

from . import db, capability, identity

TRAINER_NAME = "Pipeline 训练师"

# 本体 kind 闭集（与 ontology.ENTITY_TYPES 优先级一致，含「组织架构」）
from .ontology import ENTITY_TYPES as ONTOLOGY_KINDS

# 训练任务进度（内存，单进程够用）。job_id -> 进度 dict。
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


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


def baseline(conn, identity_id, task_ids, provider="llm", samples=1) -> dict:
    """建基线：批量跑题，返回通过率 + 逐题结果。

    samples>1 时每题跑多次取多数票（LLM 解题有随机性，单次采样噪声大，
    多数票才是稳定信号）。逐题结果 verdict 为多数票，另附 per_sample。
    """
    results = []
    for tid in task_ids:
        votes = []
        for _ in range(samples):
            r = capability.run_for_identity(conn, identity_id, tid, provider=provider)
            votes.append(r.get("verdict"))
        passes = votes.count("pass")
        verdict = "pass" if passes > len(votes) / 2 else "fail"
        results.append({"task_id": tid, "verdict": verdict,
                        "per_sample": votes, "pass_count": passes})
    passed = sum(1 for x in results if x["verdict"] == "pass")
    return {"total": len(results), "pass": passed,
            "pass_rate": round(passed / len(results), 4) if results else None,
            "results": results, "samples": samples}


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


# ---------------- 自动迭代（测试驱动本体生成） ----------------

def _analyze_failures(conn, identity_id, fails: list[dict], provider="llm") -> list[dict]:
    """失败归因：LLM 分析每道失败题的（题目 + 数字人代码 + 错误输出），提名本体段。

    铁律：LLM 只提名（kind/name/definition），不做裁决；提名仍需过三关。
    返回 [{"kind","name","definition"}, ...]。
    """
    from . import llm

    seeds: list[dict] = []
    for f in fails:
        task = capability.get_task(conn, f["task_id"])
        if not task:
            continue
        # 取最近一次该题该数字人的失败输出
        run = conn.execute(
            "SELECT code, output FROM capability_runs WHERE task_id=? AND identity_id=?"
            " AND verdict!='pass' ORDER BY id DESC LIMIT 1",
            (f["task_id"], identity_id)).fetchone()
        if not run:
            continue
        messages = [
            {"role": "system", "content":
             "你是 Pipeline 训练师，负责诊断数字人解题失败的原因，并提名需要补充的本体知识段。\n"
             "本体知识段的 kind 只能是：规则/概念/流程/对象/角色/系统/其他。\n"
             "只输出 JSON 数组，每项 {\"kind\":\"规则\",\"name\":\"...\",\"definition\":\"...\"}，"
             "name 和 definition 用中文，一句话讲清。最多 3 条。若无需补充输出 []。"},
            {"role": "user", "content":
             f"题目：{task['prompt']}\n\n数字人生成的代码：\n{run['code'][:1500]}\n\n"
             f"执行错误：\n{run['output'][:1000]}\n\n"
             f"请归因失败根因，提名需补充的本体知识段（JSON 数组）："},
        ]
        try:
            reply = llm.chat(messages, temperature=0.0) if provider == "llm" \
                else llm.chat2(messages)
        except Exception:  # noqa: BLE001
            continue
        parsed = llm.extract_json(reply)
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                seeds.append({
                    "kind": item.get("kind", "规则"),
                    "name": str(item.get("name", "")).strip(),
                    "definition": str(item.get("definition", "")).strip(),
                })
    return seeds


def _validate_seeds(seeds: list[dict]) -> list[dict]:
    """三关校验（确定性代码）：kind 闭集 + name 非空 + definition 非空 + 去重。"""
    out: list[dict] = []
    seen: set = set()
    for s in seeds:
        kind = s.get("kind", "规则")
        name = (s.get("name") or "").strip()
        definition = (s.get("definition") or "").strip()
        if kind not in ONTOLOGY_KINDS:
            kind = "规则"  # 预设外回退到最泛化的一类，而非丢弃
        if not name or not definition:
            continue
        key = (kind, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "name": name, "definition": definition})
    return out


def auto_iterate(conn, trainer_id, identity_id, task_ids, provider="llm",
                 max_rounds=3, samples=1, progress=None) -> dict:
    """自动迭代闭环：跑题 → 失败归因 → 提名本体 → 三关 → 装配 → 复测。

    循环直到无提升或达 max_rounds。返回逐轮记录 + 最终提升率。
    samples>1 时每题跑多次取多数票（消除 LLM 解题随机性）。
    progress 可选回调（job 进度上报用）。
    """
    def _tick(stage, done, total, msg=""):
        if progress:
            progress(stage=stage, done=done, total=total, msg=msg)

    rounds = []
    base = baseline(conn, identity_id, task_ids, provider, samples)
    base_rate = base["pass_rate"] or 0
    _tick("baseline", len(task_ids), len(task_ids),
          f"基线 {base['pass']}/{base['total']}（×{samples} 采样）")
    prev_rate = base_rate

    for rnd in range(1, max_rounds + 1):
        fails = [r for r in base["results"] if r["verdict"] != "pass"]
        if not fails:
            _tick("done", len(task_ids), len(task_ids), "全部通过，训练完成")
            break
        _tick("analyze", 0, len(fails), f"第 {rnd} 轮：归因 {len(fails)} 道失败题")
        seeds = _validate_seeds(_analyze_failures(conn, identity_id, fails, provider))
        if not seeds:
            _tick("stall", 0, len(fails), "归因无有效本体提名，停止")
            break

        _tick("teach", 0, len(seeds), f"第 {rnd} 轮：装配 {len(seeds)} 条本体")
        added = 0
        for s in seeds:
            add_ontology(conn, identity_id, s["kind"], s["name"], s["definition"],
                         note=f"训练师 #{trainer_id} 自动归因（第 {rnd} 轮）")
            added += 1

        after = baseline(conn, identity_id, task_ids, provider, samples)
        after_rate = after["pass_rate"] or 0
        improvement = round(after_rate - prev_rate, 4)
        _tick("round_done", len(task_ids), len(task_ids),
              f"第 {rnd} 轮：{after['pass']}/{after['total']}（{after_rate:.0%}），提升 {improvement:+.0%}")
        rounds.append({
            "round": rnd,
            "fails": [f["task_id"] for f in fails],
            "seeds": seeds,
            "added": added,
            "before": prev_rate,
            "after": after_rate,
            "improvement": improvement,
            "results": after["results"],
        })
        if after_rate <= prev_rate:
            _tick("stall", len(task_ids), len(task_ids), f"第 {rnd} 轮无提升，停止")
            break
        prev_rate = after_rate
        base = after

    final_rate = prev_rate
    return {
        "identity_id": identity_id,
        "baseline_rate": base_rate,
        "final_rate": final_rate,
        "total_improvement": round(final_rate - base_rate, 4),
        "rounds": rounds,
        "ontology": list_ontology(conn, identity_id),
    }


# ---------------- 训练任务（后台线程 + 进度上报） ----------------

def start_job(identity_id: int, task_ids: list[int], provider="llm",
              max_rounds=3, auto=True, ontology_seeds=None, samples=1) -> str:
    """启动一个训练任务（后台线程），返回 job_id。前端轮询 job_progress 拿进度。"""
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "job_id": job_id, "status": "running", "stage": "queued",
            "done": 0, "total": len(task_ids), "msg": "排队中",
            "identity_id": identity_id, "result": None, "error": None,
            "started_at": time.time(),
        }

    def _progress(stage, done, total, msg=""):
        with _JOBS_LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["stage"] = stage
                j["done"] = done
                j["total"] = total
                j["msg"] = msg

    def _run():
        try:
            conn = db.get_conn()
            tid = ensure_trainer(conn)
            if auto:
                result = auto_iterate(conn, tid, identity_id, task_ids,
                                      provider=provider, max_rounds=max_rounds,
                                      samples=samples, progress=_progress)
            else:
                result = train_iteration(conn, tid, identity_id, task_ids,
                                         provider=provider,
                                         ontology_seeds=ontology_seeds or [])
            with _JOBS_LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j["status"] = "done"
                    j["result"] = result
        except Exception as e:  # noqa: BLE001
            with _JOBS_LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j["status"] = "error"
                    j["error"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return job_id


def job_progress(job_id: str) -> dict | None:
    """查询训练任务进度。"""
    with _JOBS_LOCK:
        return dict(_JOBS.get(job_id) or {}) or None
