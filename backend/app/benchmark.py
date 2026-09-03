# -*- coding: utf-8 -*-
"""数字人四组对照 benchmark + 本体问题归因提名 + 版本管理.

Flow (every LLM output is a NOMINATION; deterministic code adjudicates and
the user is the sole final approver — LLM 无终审权):

  1. select quizzes tied to the persona's ontology (0 LLM, closed join)
  2. four arms answer each question on a local Ollama model (low temperature):
       G0 none  · G1 ontology · G2 rag · G3 rag_ontology
     — ontology arms reuse the chat dynamic retrieval window, rag arms reuse
       ingest.search (chunk texts injected as 参考资料 blocks)
  3. GLM (chat2) judges every reply: verdict nomination in the closed set
     {correct, partial, wrong, refused} -> deterministic gates (closed-set
     filter + refusal markers) -> per-arm stats -> conclusion rendered by a
     code template (no LLM ever writes conclusions)
  4. for ontology-arm failures GLM nominates problematic ontology entries
     (annotate / update / delete) or a missing key concept (add) -> closed-set
     validation (annotate/update/delete names must be among that question's
     injected ontology AND active in persona_ontology; add must be a genuinely
     new entity with a suggested definition) -> pending change set
     (persona_ontology_changes)
  5. the user reviews pending changes on the persona card; merge snapshots
     the current ontology 段 (persona_ontology_versions) then applies the
     changes (annotate -> note, update -> definition, delete -> deprecated,
     add -> insert a new entity);
     any version can be rolled back by restoring its snapshot (rollback
     snapshots the current state first, so it is itself reversible).

Metrics: 准确率 = correct 占比 · 幻觉率 = wrong 占比 · 拒答率 = refused 占比
         partial 单列；本体边际 = G3−G2 · RAG 边际 = G3−G1 · 总增益 = G3−G0.
"""
import json
import time

from . import db, llm, jobs, chat, ingest

ARMS = ("none", "ontology", "rag", "rag_ontology")
ARM_LABELS = {"none": "G0 裸模型", "ontology": "G1 仅本体",
              "rag": "G2 仅RAG", "rag_ontology": "G3 RAG+本体"}
VERDICTS = ("correct", "partial", "wrong", "refused")
ACTIONS = ("annotate", "update", "delete", "add")
ACTION_LABELS = {"annotate": "标注", "update": "修改", "delete": "删除", "add": "新增"}
_ACTION_PRIORITY = {"annotate": 1, "update": 2, "delete": 3, "add": 4}
REFUSAL_MARKERS = ("我不知道", "本体里没有", "本体知识", "无法回答", "没有掌握",
                   "没有这方面", "资料里没有", "不掌握")
DEFAULT_LIMIT = 15
MAX_LIMIT = 30
RAG_TOP_K = 3
ANSWER_TEMPERATURE = 0.1
ANALYSIS_MAX_ENTRIES = 60   # cap injected entries in the analysis prompt
MAX_NOMINATIONS = 3        # deterministic cap per failed question (thinking-off GLM over-nominates)


# ---------------- selection (0 LLM) ----------------

def _select_quizzes(conn, identity_id: int, limit: int) -> list[dict]:
    """Quizzes whose chunk is mentioned by one of the persona's ontology
    entities — i.e. questions about what THIS persona actually knows."""
    return [dict(r) for r in conn.execute(
        "SELECT DISTINCT q.id AS quiz_id, q.question, q.answer, q.evidence,"
        " q.chunk_id FROM quiz q"
        " JOIN mentions m ON m.chunk_id = q.chunk_id"
        " JOIN persona_ontology po"
        "   ON po.source_candidate_id = m.candidate_id"
        "  AND po.identity_id=? AND po.status='active'"
        " ORDER BY q.id LIMIT ?", (identity_id, limit)).fetchall()]


def _rag_search(conn, query: str, top_k: int) -> list[dict]:
    """RAG retrieval (wrapped so tests can monkeypatch it deterministically)."""
    return ingest.search(conn, query, top_k=top_k)


# ---------------- answering ----------------

def _build_messages(ident: dict, question: str, arm: str, base: dict,
                    budget_tokens: int) -> tuple[list[dict], list[dict]]:
    """Assemble the arm's messages + return the injected ontology entities
    (used later for the closed-set validation of failure nominations)."""
    ont_arms = arm in ("ontology", "rag_ontology")
    rag_arms = arm in ("rag", "rag_ontology")

    injected: list[dict] = []
    ctx_relations: list[dict] = []
    if ont_arms:
        retrieved = chat._retrieve_context(
            base["anchors"], base["ontology"], base["relations"],
            question, budget_tokens)
        injected = retrieved["ontology"]
        ctx_relations = retrieved["relations"]

    rag_texts: list[str] = []
    if rag_arms:
        rag_texts = [h.get("text") or "" for h in base["rag"].get(question, [])]

    ctx = {
        "identity": ident,
        "anchors": base["anchors"] if ont_arms else [],
        "ontology": injected,
        "relations": ctx_relations,
        "rag": rag_texts,
    }
    system = chat._system_prompt(ctx, use_ontology=ont_arms)
    return [{"role": "system", "content": system},
            {"role": "user", "content": question}], injected


# ---------------- judging (GLM nominates, code adjudicates) ----------------

def _judge_prompt(question: str, gold: str, evidence: str, reply: str) -> str:
    return (
        "你是阅卷官。根据题目、标准答案和原文证据，判定被测回答的质量等级。\n"
        "只输出 JSON：{\"verdict\": \"correct|partial|wrong|refused\"}\n"
        "correct=与标准答案一致；partial=部分正确；wrong=答错或编造（幻觉）；"
        "refused=明确拒答（如「我不知道」）。\n"
        f"【题目】{question}\n"
        f"【标准答案】{gold}\n"
        f"【原文证据】{evidence}\n"
        f"【被测回答】{(reply or '').strip()[:2000]}\n"
        "只输出上述 JSON，不要输出其他内容。"
    )


def _deterministic_verdict(reply: str, judge_verdict: str) -> str:
    """Final verdict: deterministic code has the last word over the judge.

    Refusal markers override (an honest refusal stays a refusal even if the
    judge was fooled); anything outside the closed set counts as wrong."""
    r = (reply or "").strip()
    if not r:
        return "wrong"
    if any(m in r for m in REFUSAL_MARKERS):
        return "refused"
    if judge_verdict in VERDICTS:
        return judge_verdict
    return "wrong"


def judge_reply(question: str, gold: str, evidence: str, reply: str) -> str:
    """GLM nominates a verdict; deterministic gates adjudicate it."""
    raw = llm.chat2([{"role": "user",
                      "content": _judge_prompt(question, gold, evidence, reply)}])
    data = llm.extract_json(raw)
    nomination = ""
    if isinstance(data, dict):
        v = str(data.get("verdict") or "")
        if v in VERDICTS:
            nomination = v
    return _deterministic_verdict(reply, nomination)


# ---------------- stats + conclusion (pure code, no LLM) ----------------

def _stats(results: list[dict]) -> dict:
    """results: [{arm, verdict}] for every (question, arm) pair."""
    total = len({r["quiz_id"] for r in results}) or 1
    arms: dict[str, dict] = {}
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm]
        n = len(rows) or 1
        c = {"correct": 0, "partial": 0, "wrong": 0, "refused": 0}
        for r in rows:
            if r["verdict"] in c:
                c[r["verdict"]] += 1
        arms[arm] = {
            **c,
            "accuracy": round(c["correct"] * 100.0 / n, 1),
            "hallucination": round(c["wrong"] * 100.0 / n, 1),
            "refusal": round(c["refused"] * 100.0 / n, 1),
        }
    margins = {
        "ontology_pp": round(arms["rag_ontology"]["accuracy"] - arms["rag"]["accuracy"], 1),
        "rag_pp": round(arms["rag_ontology"]["accuracy"] - arms["ontology"]["accuracy"], 1),
        "total_pp": round(arms["rag_ontology"]["accuracy"] - arms["none"]["accuracy"], 1),
    }
    return {"arms": arms, "margins": margins, "questions": total}


def _conclusion(stats: dict) -> str:
    arms = stats["arms"]
    m = stats["margins"]
    ranked = sorted(ARMS, key=lambda a: -arms[a]["accuracy"])
    order = " > ".join(f"{ARM_LABELS[a]} {arms[a]['accuracy']}%" for a in ranked)
    # 幻觉压制：约束组（本体/RAG）相对裸模型的最低幻觉率，回答「减少多少幻觉」
    hall_base = arms["none"]["hallucination"]
    constrained = ("ontology", "rag", "rag_ontology")
    hall_arm = min(constrained, key=lambda a: arms[a]["hallucination"])
    hall_best = arms[hall_arm]["hallucination"]
    hall_drop = round(hall_base - hall_best, 1)
    return (f"准确率 {order}（{stats['questions']} 题）· "
            f"幻觉 {ARM_LABELS['none']} {hall_base}% → {ARM_LABELS[hall_arm]} "
            f"{hall_best}%（−{hall_drop}pp）· "
            f"本体边际 {m['ontology_pp']:+.1f}pp · RAG边际 {m['rag_pp']:+.1f}pp · "
            f"总增益 {m['total_pp']:+.1f}pp")


# ---------------- failure attribution (GLM nominates, code validates) ----------------

def _analysis_prompt(question: str, gold: str, evidence: str, reply: str,
                     injected: list[dict]) -> str:
    entries = "\n".join(
        f"- {o['name']}：{(o.get('definition') or '').strip()}"
        for o in injected) or "（无）"
    return (
        "你是本体质量分析官（只提名，不裁决）。数字人在本体约束下答错了一道题。\n"
        "请从【注入的本体条目】中找出【真正有害】的本体，或识别【缺失的关键概念】，"
        "并给出处理建议：\n"
        "- annotate 标注：本体有保留价值但定义需注记；\n"
        "- update 修改：定义写错了，给出修正定义；\n"
        "- delete 删除：定义与原文矛盾或纯属噪声，应删除；\n"
        "- add 新增：失败根因是【本体段缺少关键概念】，新增该概念实体"
        "（给新本体名 + 建议定义）。\n"
        "判定标准（务必遵守）：\n"
        "1. 「与本题无关」不等于「有害」——本体定义本身正确、只是没有覆盖本题"
        "答案的，一律不要 annotate/update/delete；\n"
        "2. 只有定义【确实错误/误导/与原文矛盾/无依据】才 annotate/update/delete；\n"
        "3. 若失败根因是「本体段缺少关键概念」（缺少某个定义正确的术语），"
        "用 add 提名新增该概念，并给出建议定义；\n"
        "4. add 的新本体名必须【不在注入的本体条目中】，且建议定义要能支撑本题答案；\n"
        "5. 最多提名 3 条最可疑的本体（add 与其它动作合计）。\n"
        f"【题目】{question}\n"
        f"【标准答案】{gold}\n"
        f"【原文证据】{evidence}\n"
        f"【模型回答（有本体）】{(reply or '').strip()[:1500]}\n"
        f"【注入的本体条目】\n{entries}\n"
        "只输出 JSON：{\"issues\": [{\"name\": \"本体名\", "
        "\"action\": \"annotate|update|delete|add\", \"reason\": \"理由\", "
        "\"suggested_definition\": \"update 时给修正定义；add 时给新本体定义\", "
        "\"note\": \"仅 annotate 时给出注记\"}]}\n"
        "annotate/update/delete 不得提名【注入的本体条目】之外的本体名；"
        "add 提名的新本体名必须在【注入的本体条目】之外，且必须给出 suggested_definition；"
        "没有问题时输出 {\"issues\": []}。"
    )


def _validate_nominations(raw_issues, allowed: dict[str, int],
                          active_names: set[str]) -> list[dict]:
    """allowed: name -> persona_ontology.id (active injected rows only);
    active_names: every active persona_ontology name (add de-dup guard).
    Deterministic closed-set validation: drop unknown names/actions, downgrade
    update without a definition to annotate, drop empty reasons; an `add`
    nomination must NOT already exist in the active ontology and MUST carry a
    suggested_definition (a missing concept is always an entity)."""
    out: dict[str, dict] = {}
    if not isinstance(raw_issues, list):
        return []
    for it in raw_issues:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        action = str(it.get("action") or "").strip()
        reason = str(it.get("reason") or "").strip()
        if action not in ACTIONS or not name or not reason:
            continue
        if action == "add":
            # a new concept entity: must be genuinely missing (else it is an
            # update, not an add) and must carry a definition
            fix = str(it.get("suggested_definition") or "").strip()
            if name in active_names or not fix:
                continue
            item = {"name": name, "ontology_id": None, "action": "add",
                    "suggested_definition": fix, "reason": reason}
        else:
            if name not in allowed:
                continue
            item = {"name": name, "ontology_id": allowed[name], "reason": reason}
            if action == "update":
                fix = str(it.get("suggested_definition") or "").strip()
                if fix:
                    item["action"] = "update"
                    item["suggested_definition"] = fix
                else:  # update without a fixed definition -> just annotate
                    item["action"] = "annotate"
                    item["note"] = reason
            elif action == "annotate":
                item["action"] = "annotate"
                item["note"] = str(it.get("note") or "").strip() or reason
            else:
                item["action"] = "delete"
        old = out.get(name)
        if old is None or _ACTION_PRIORITY[item["action"]] > _ACTION_PRIORITY[old["action"]]:
            if old is not None:
                item["evidence"] = old.get("evidence", [])
            out[name] = item
    return list(out.values())


def analyze_failures(conn, identity_id: int, benchmark_id: int,
                     failures: list[dict]) -> int:
    """GLM analyzes each ontology-arm failure and nominates problematic
    ontology entries; validated nominations land in the pending change set.

    failures: [{quiz_id, question, answer, evidence, reply, injected}]."""
    active = {r["name"]: r["id"] for r in conn.execute(
        "SELECT id, name FROM persona_ontology"
        " WHERE identity_id=? AND status='active'", (identity_id,)).fetchall()}
    active_names = set(active.keys())
    by_name: dict[str, dict] = {}
    for f in failures:
        # cap the injected list in the prompt (hits first, then degree-sorted
        # neighbours) so the analysis call stays fast on big ontology 段
        injected = f["injected"][:ANALYSIS_MAX_ENTRIES]
        allowed = {o["name"]: active[o["name"]] for o in injected
                   if o["name"] in active}
        if not allowed:
            continue
        raw = llm.chat2([{"role": "user", "content": _analysis_prompt(
            f["question"], f["answer"], f["evidence"], f["reply"], injected)}])
        data = llm.extract_json(raw)
        issues = data.get("issues") if isinstance(data, dict) else None
        # deterministic cap: thinking-off GLM can over-nominate (e.g. flag every
        # merely-unrelated ontology entry as delete), so keep at most
        # MAX_NOMINATIONS per failed question to keep the review set usable.
        issues = _validate_nominations(issues, allowed, active_names)[:MAX_NOMINATIONS]
        for item in issues:
            ev = {"quiz_id": f["quiz_id"], "question": f["question"],
                  "reply": (f["reply"] or "").strip()[:200]}
            cur = by_name.get(item["name"])
            if cur is None:
                item["evidence"] = [ev]
                by_name[item["name"]] = item
            else:
                cur.setdefault("evidence", []).append(ev)
                if (_ACTION_PRIORITY[item["action"]]
                        > _ACTION_PRIORITY[cur["action"]]):
                    cur["action"] = item["action"]
                    cur["reason"] = item["reason"]
                    cur["suggested_definition"] = item.get("suggested_definition")
                    cur["note"] = item.get("note")
    now = time.time()
    for item in by_name.values():
        conn.execute(
            "INSERT INTO persona_ontology_changes"
            " (identity_id, benchmark_id, ontology_id, name, kind, action,"
            "  suggested_definition, note, reason, evidence, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)",
            (identity_id, benchmark_id, item["ontology_id"], item["name"],
             "entity" if item["action"] == "add" else None, item["action"],
             item.get("suggested_definition"),
             item.get("note"), item["reason"],
             json.dumps(item.get("evidence", []), ensure_ascii=False), now))
    conn.commit()
    return len(by_name)


# ---------------- the benchmark job ----------------

def run_benchmark(conn, job_id: int, identity_id: int,
                  ollama_model: str | None, limit: int) -> None:
    ident = chat._identity(conn, identity_id)
    if not ident:
        jobs.finish_job(conn, job_id, ok=False, error=f"数字人 #{identity_id} 不存在")
        return
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    quizzes = _select_quizzes(conn, identity_id, limit)
    model = ollama_model or "qwen2.5:7b-32k"
    cur = conn.execute(
        "INSERT INTO persona_benchmarks"
        " (identity_id, job_id, model, judge, total, status, created_at)"
        " VALUES (?,?,?,?,?,'running',?)",
        (identity_id, job_id, model, "llm2/GLM", len(quizzes), time.time()))
    benchmark_id = cur.lastrowid
    if not quizzes:
        conn.execute(
            "UPDATE persona_benchmarks SET status='failed',"
            " error='没有与本体段关联的测试题（先装配本体，或先跑本体提取出题）'"
            " WHERE id=?", (benchmark_id,))
        conn.commit()
        jobs.finish_job(conn, job_id, ok=False,
                        error="没有与本体段关联的测试题")
        return

    try:
        context_window, _ = chat._context_window("ollama", model)
        budget = int(context_window * chat.ONTOLOGY_BUDGET_RATIO)
        base = {
            "anchors": chat._approved_anchors(conn, identity_id),
            "ontology": chat._persona_ontology(conn, identity_id),
            "relations": chat._relations(conn, identity_id),
            "rag": {q["question"]: _rag_search(conn, q["question"], RAG_TOP_K)
                    for q in quizzes},
        }
        results: list[dict] = []
        failures: list[dict] = []
        for i, q in enumerate(quizzes):
            jobs.poll_control(conn, job_id)
            injected: list[dict] = []
            for arm in ARMS:
                messages, inj = _build_messages(ident, q["question"], arm,
                                                base, budget)
                if arm in ("ontology", "rag_ontology"):
                    injected = inj
                reply = llm.chat_ollama(messages, temperature=ANSWER_TEMPERATURE,
                                        model=model)
                verdict = judge_reply(q["question"], q["answer"],
                                      q["evidence"], reply)
                conn.execute(
                    "INSERT INTO persona_benchmark_items"
                    " (benchmark_id, quiz_id, question, answer, arm, reply,"
                    "  verdict, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (benchmark_id, q["quiz_id"], q["question"], q["answer"],
                     arm, reply, verdict, time.time()))
                results.append({"quiz_id": q["quiz_id"], "arm": arm,
                                "verdict": verdict})
                if arm in ("ontology", "rag_ontology") \
                        and verdict in ("wrong", "partial"):
                    failures.append({"quiz_id": q["quiz_id"],
                                     "question": q["question"],
                                     "answer": q["answer"],
                                     "evidence": q["evidence"],
                                     "reply": reply, "injected": injected})
            conn.commit()
            jobs.update_progress(conn, job_id, i + 1, len(quizzes))

        stats = _stats(results)
        conclusion = _conclusion(stats)
        nominees = analyze_failures(conn, identity_id, benchmark_id, failures)
        # done ONLY after the failure-attribution phase: the card must never
        # show a "finished" benchmark whose nomination set is still growing
        conn.execute(
            "UPDATE persona_benchmarks SET status='done', stats=?, conclusion=?"
            " WHERE id=?",
            (json.dumps(stats, ensure_ascii=False), conclusion, benchmark_id))
        conn.commit()
        jobs.emit(conn, job_id, "benchmark.done", {
            "benchmark_id": benchmark_id, "identity_id": identity_id,
            "conclusion": conclusion, "nominees": nominees})
        jobs.finish_job(conn, job_id, ok=True)
    except (jobs.JobPaused, jobs.JobCancelled):
        conn.execute("UPDATE persona_benchmarks SET status='failed',"
                     " error='interrupted' WHERE id=?", (benchmark_id,))
        conn.commit()
        raise
    except Exception as e:  # noqa: BLE001
        conn.execute("UPDATE persona_benchmarks SET status='failed',"
                     " error=? WHERE id=?", (f"{type(e).__name__}: {e}",
                                             benchmark_id))
        conn.commit()
        jobs.finish_job(conn, job_id, ok=False,
                        error=f"{type(e).__name__}: {str(e)[:300]}")


# ---------------- summary / review / merge / rollback ----------------

def benchmark_summary(conn, identity_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM persona_benchmarks WHERE identity_id=?"
        " ORDER BY id DESC LIMIT 1", (identity_id,)).fetchone()
    benchmark = None
    if row:
        benchmark = dict(row)
        try:
            benchmark["stats"] = json.loads(row["stats"]) if row["stats"] else None
        except (ValueError, TypeError):
            benchmark["stats"] = None
    changes = []
    for r in conn.execute(
            "SELECT * FROM persona_ontology_changes WHERE identity_id=?"
            " AND status='pending' ORDER BY id DESC", (identity_id,)).fetchall():
        c = dict(r)
        try:
            c["evidence"] = json.loads(r["evidence"]) if r["evidence"] else []
        except (ValueError, TypeError):
            c["evidence"] = []
        changes.append(c)
    versions = []
    for r in conn.execute(
            "SELECT id, identity_id, version, benchmark_id, changelog,"
            " created_at FROM persona_ontology_versions WHERE identity_id=?"
            " ORDER BY version DESC", (identity_id,)).fetchall():
        v = dict(r)
        try:
            v["changelog"] = json.loads(r["changelog"]) if r["changelog"] else []
        except (ValueError, TypeError):
            v["changelog"] = []
        versions.append(v)
    return {"benchmark": benchmark, "changes": changes, "versions": versions}


def reject_change(conn, change_id: int) -> bool:
    """User dismisses one pending nomination (LLM nomination is never auto-applied)."""
    cur = conn.execute(
        "UPDATE persona_ontology_changes SET status='rejected'"
        " WHERE id=? AND status='pending'", (change_id,))
    conn.commit()
    return cur.rowcount > 0


def _snapshot_rows(conn, identity_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, identity_id, kind, name, definition,"
        " source_candidate_id, status, note, created_at"
        " FROM persona_ontology WHERE identity_id=? ORDER BY id",
        (identity_id,)).fetchall()]


def merge_changes(conn, identity_id: int) -> dict:
    """User final approval: apply all pending changes as one new version.

    Snapshot first (rollback anchor), then annotate -> note / update ->
    definition / delete -> status='deprecated' (soft delete)."""
    pending = [dict(r) for r in conn.execute(
        "SELECT * FROM persona_ontology_changes WHERE identity_id=?"
        " AND status='pending' ORDER BY id", (identity_id,)).fetchall()]
    if not pending:
        return {"ok": False, "error": "没有待合并的变更"}
    snapshot = _snapshot_rows(conn, identity_id)
    version = (conn.execute(
        "SELECT MAX(version) v FROM persona_ontology_versions WHERE identity_id=?",
        (identity_id,)).fetchone()["v"] or 0) + 1
    changelog = [{"name": c["name"], "action": c["action"]} for c in pending]
    cur = conn.execute(
        "INSERT INTO persona_ontology_versions"
        " (identity_id, version, benchmark_id, changelog, snapshot, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (identity_id, version, pending[0]["benchmark_id"],
         json.dumps(changelog, ensure_ascii=False),
         json.dumps(snapshot, ensure_ascii=False), time.time()))
    version_id = cur.lastrowid
    applied = {"annotate": 0, "update": 0, "delete": 0, "add": 0}
    for c in pending:
        if c["action"] == "add":
            # add a missing concept entity (GLM-nominated definition, no
            # source candidate — it never existed in the candidate pool)
            exists = conn.execute(
                "SELECT id FROM persona_ontology WHERE identity_id=? AND kind='entity'"
                " AND name=?", (identity_id, c["name"])).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO persona_ontology(identity_id, kind, name,"
                    " definition, source_candidate_id, status, created_at)"
                    " VALUES(?, 'entity', ?, ?, NULL, 'active', ?)",
                    (identity_id, c["name"],
                     (c["suggested_definition"] or "").strip(), time.time()))
            applied["add"] += 1
            conn.execute("UPDATE persona_ontology_changes SET status='merged',"
                         " version_id=? WHERE id=?", (version_id, c["id"]))
            continue
        row = conn.execute(
            "SELECT * FROM persona_ontology WHERE id=? AND identity_id=?",
            (c["ontology_id"], identity_id)).fetchone()
        if row is None:
            continue
        if c["action"] == "update" and (c["suggested_definition"] or "").strip():
            conn.execute("UPDATE persona_ontology SET definition=? WHERE id=?",
                         (c["suggested_definition"].strip(), c["ontology_id"]))
        elif c["action"] == "delete":
            conn.execute("UPDATE persona_ontology SET status='deprecated'"
                         " WHERE id=?", (c["ontology_id"],))
        else:  # annotate
            note = (c["note"] or c["reason"] or "").strip()
            old = (row["note"] or "").strip()
            merged = (old + "\n" if old else "") + f"[测试提名] {note}"
            conn.execute("UPDATE persona_ontology SET note=? WHERE id=?",
                         (merged, c["ontology_id"]))
        applied[c["action"]] = applied.get(c["action"], 0) + 1
        conn.execute("UPDATE persona_ontology_changes SET status='merged',"
                     " version_id=? WHERE id=?", (version_id, c["id"]))
    conn.commit()
    # 事件驱动实时刷新：任何打开的视图（数字人卡片/本体图谱）收到即刷新
    jobs.emit(conn, None, "benchmark.merged",
              {"identity_id": identity_id, "version": version,
               "applied": applied})
    return {"ok": True, "version": version,
            "applied": applied, "snapshot_rows": len(snapshot)}


def rollback_version(conn, identity_id: int, version_id: int) -> dict:
    """Restore the ontology snapshot of an old version. The current state is
    snapshotted as a NEW version first, so the rollback itself is reversible."""
    row = conn.execute(
        "SELECT * FROM persona_ontology_versions WHERE id=? AND identity_id=?",
        (version_id, identity_id)).fetchone()
    if row is None:
        return {"ok": False, "error": "版本不存在"}
    try:
        snapshot = json.loads(row["snapshot"])
    except (ValueError, TypeError):
        return {"ok": False, "error": "快照损坏，无法回滚"}
    current = _snapshot_rows(conn, identity_id)
    version = (conn.execute(
        "SELECT MAX(version) v FROM persona_ontology_versions WHERE identity_id=?",
        (identity_id,)).fetchone()["v"] or 0) + 1
    conn.execute(
        "INSERT INTO persona_ontology_versions"
        " (identity_id, version, benchmark_id, changelog, snapshot, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (identity_id, version, row["benchmark_id"],
         json.dumps([{"action": "rollback", "to": row["version"]}],
                    ensure_ascii=False),
         json.dumps(current, ensure_ascii=False), time.time()))
    conn.execute("DELETE FROM persona_ontology WHERE identity_id=?",
                 (identity_id,))
    for r in snapshot:
        conn.execute(
            "INSERT INTO persona_ontology (id, identity_id, kind, name,"
            " definition, source_candidate_id, status, note, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (r["id"], r["identity_id"], r["kind"], r["name"],
             r.get("definition"), r.get("source_candidate_id"),
             r.get("status") or "active", r.get("note"),
             r.get("created_at") or time.time()))
    conn.commit()
    jobs.emit(conn, None, "benchmark.rolled_back",
              {"identity_id": identity_id, "version": version,
               "to": row["version"]})
    return {"ok": True, "version": version, "restored": row["version"],
            "rows": len(snapshot)}
