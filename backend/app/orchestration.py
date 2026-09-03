# -*- coding: utf-8 -*-
"""Ontology post-extraction orchestration (二次编排): clean 4000 -> keep the core.

Two phases, both NOMINATE only (iron law: LLM never adjudicates, the user is
the single final judge):

  phase A — deterministic rule clean (0 LLM), auto-run after extraction:
      R1 pure-number/symbol names, R2 single-char names, R3 citation patterns,
      R4 case-insensitive duplicates (merge). Deterministic, high-precision,
      zero-false-positive rules only.

  phase B — GLM 5.2 relevance triage (user-triggered "保存" button):
      vertical-domain triage core/marginal/irrelevant + delete/merge nomination,
      validated by three deterministic gates before storing.

  confirm — user's one-click final adjudication: execute delete/merge.

Nothing is deleted or merged until the user confirms the batch. GLM output is
a nomination; deterministic code validates it; only the user lands it.
"""
import json
import re

from . import db, jobs, llm, ontology

# ---------------- deterministic rule clean (phase A, 0 LLM) ----------------

_RE_CITATION = re.compile(r"\bet\s+al\b", re.I)
_RE_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def _classify_rule(name: str) -> tuple[str, str, str] | None:
    """Deterministic noise classification of a candidate name.

    Returns (action, category, reason) or None if not classifiable here
    (left to GLM / the user). Deliberately high-precision: a false positive
    here would delete a real entity, so only unambiguous patterns qualify."""
    s = (name or "").strip()
    if not s:
        return None
    # R1: pure number/symbol — no letter, no CJK, but contains a digit
    if re.search(r"\d", s) and not re.search(r"[a-zA-Z\u4e00-\u9fa5]", s):
        return ("delete", "irrelevant", "纯数字/符号（无字母无汉字），非领域实体")
    # R2: single character — too little information to be an entity
    if len(s) == 1:
        return ("delete", "irrelevant", "单字符，信息量不足以作为本体")
    # R3: citation / author-list patterns
    if _RE_CITATION.search(s):
        return ("delete", "irrelevant", "引用模式（et al），作者列表而非实体")
    if _RE_YEAR.search(s) and "," in s:
        return ("delete", "irrelevant", "含年份+逗号的引用（作者, 年份）")
    return None


def _case_dup_merges(conn, skip_ids: set[int]) -> list[dict]:
    """R4: pending entities with the same lower(name) -> merge into the
    most-mentioned (tie-break lowest id). Skip candidates already ruled for
    deletion so a candidate never gets two actions."""
    rows = conn.execute(
        "SELECT id, name FROM candidates WHERE kind='entity' AND status='pending'"
        " ORDER BY id").fetchall()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if r["id"] in skip_ids:
            continue
        groups.setdefault(r["name"].strip().lower(), []).append(dict(r))

    mcounts = {r["candidate_id"]: r["c"] for r in conn.execute(
        "SELECT candidate_id, COUNT(*) c FROM mentions GROUP BY candidate_id")}
    out: list[dict] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda g: (-mcounts.get(g["id"], 0), g["id"]))
        keep = group[0]
        for g in group[1:]:
            out.append({
                "candidate_id": g["id"], "action": "merge",
                "category": "marginal", "merge_into": keep["id"],
                "reason": f"与 #{keep['id']}「{keep['name']}」大小写重复，合并到高提及项",
            })
    return out


def run_rule_clean(conn) -> dict:
    """Phase A: nominate deterministic deletions/merges as a pending batch.

    NOTHING is deleted here — the user confirms the batch afterwards.
    Returns {batch_id, count, deletes, merges}."""
    deletes: list[dict] = []
    delete_ids: set[int] = set()
    for r in conn.execute(
            "SELECT id, name FROM candidates"
            " WHERE kind='entity' AND status='pending' ORDER BY id").fetchall():
        cls = _classify_rule(r["name"])
        if cls:
            action, category, reason = cls
            deletes.append({"candidate_id": r["id"], "action": action,
                            "category": category, "reason": reason,
                            "merge_into": None})
            delete_ids.add(r["id"])
    merges = _case_dup_merges(conn, delete_ids)
    items = deletes + merges
    if not items:
        return {"batch_id": None, "count": 0, "deletes": 0, "merges": 0}
    batch_id = _create_batch(conn, "rule")
    _insert_items(conn, batch_id, items)
    jobs.emit(conn, None, "orchestration.rule_cleaned", {
        "batch_id": batch_id, "deletes": len(deletes), "merges": len(merges)})
    return {"batch_id": batch_id, "count": len(items),
            "deletes": len(deletes), "merges": len(merges)}


# ---------------- GLM relevance triage (phase B, user-triggered) ----------------

GLM_BATCH = 80   # candidates per GLM triage call (token-bounded)


def _mission_context(conn) -> str:
    """Chosen digital-person mission + anchor ontology for the triage prompt.
    Falls back to high-freq words when no identity is approved yet."""
    rows = conn.execute(
        "SELECT i.name iname, i.mission, a.name, a.definition"
        " FROM identities i LEFT JOIN anchors a"
        "   ON a.identity_id=i.id AND a.status='approved'"
        " WHERE i.status='approved' ORDER BY i.id, a.id").fetchall()
    if not rows:
        from .identity import high_freq_words
        words = high_freq_words(conn, 20)
        w = "、".join(k for k, _ in words)
        return ("未选择数字人身份。请按语料高频词判断垂直领域相关性：\n" + w)
    missions: list[str] = []
    anchors: list[str] = []
    seen = set()
    for r in rows:
        if r["iname"] not in seen:
            seen.add(r["iname"])
            missions.append(f"「{r['iname']}」使命：{r['mission'] or '（未填）'}")
        if r["name"]:
            anchors.append(f"{r['name']}：{r['definition'] or '（无定义）'}")
    text = "目标数字人：\n" + "\n".join(missions)
    if anchors:
        text += "\n锚点本体（最应掌握的核心实体）：\n" + "\n".join(anchors)
    return text


def _glm_triage_prompt(mission_ctx: str, batch: list[dict]) -> str:
    lines = []
    for c in batch:
        flags = []
        if c["status"] == "approved":
            flags.append("已批准")
        else:
            flags.append("待审")
        if c["is_anchor"]:
            flags.append("锚点")
        lines.append(
            f"- [{c['id']}] {c['name']}（提及{c['mentions']}次，{'，'.join(flags)}）"
            f"：{c['definition'] or '（无定义）'}")
    return (
        "你是本体二次编排器，只提名、不裁决。\n"
        f"{mission_ctx}\n\n"
        "以下是提取出的候选本体（编号 | 名称 | 提及次数 | 状态 | 定义）。"
        "请做垂直领域相关度分档与清洗建议：\n"
        + "\n".join(lines) + "\n\n"
        "对每个候选输出：\n"
        "1. category 相关度分档：core（与使命/锚点强相关的核心概念）"
        " | marginal（边缘相关） | irrelevant（无关噪音：实验数据、引用、"
        "通用词、论文细节等）\n"
        "2. action 建议动作：keep（保留） | delete（irrelevant 或纯噪音，建议删除）"
        " | merge（与另一候选重复/近义，合并过去）\n"
        "规则：\n"
        "- 已批准（approved）的候选不要建议 delete，最多 keep 并说明理由\n"
        "- 锚点候选不要建议 delete\n"
        "- merge 只在两个候选确实同义/重复时给出，merge_into 填目标候选编号\n"
        "- 不确定的保守归为 marginal + keep\n"
        "只输出建议，不执行。严格按 JSON 输出：\n"
        '{"suggestions": [{"id": 编号, "action": "keep|delete|merge",'
        ' "category": "core|marginal|irrelevant", "reason": "一句中文",'
        ' "merge_into": 目标编号或null}]}\n'
        "id 必须是列表里出现的编号，不得编造。")


def _validate_glm(data, id_map: dict[int, dict]) -> list[dict]:
    """Three deterministic gates on GLM output (LLM only nominates):

      G1 闭集关 — action/category must be in the closed enum
      G2 引用关 — id must map back to a real candidate; merge_into too
      G3 冲突关 — approved candidates are never auto-deleted (demote to keep)
    """
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    seen: set[int] = set()
    for s in (data.get("suggestions") or []):
        if not isinstance(s, dict):
            continue
        action = str(s.get("action") or "").strip()
        category = str(s.get("category") or "").strip()
        if action not in ("keep", "delete", "merge"):
            continue
        if category not in ("core", "marginal", "irrelevant"):
            category = None
        try:
            cid = int(s.get("id"))
        except (TypeError, ValueError):
            continue
        cand = id_map.get(cid)
        if cand is None or cid in seen:
            continue
        seen.add(cid)
        merge_into = None
        if action == "merge":
            try:
                mi = int(s.get("merge_into"))
            except (TypeError, ValueError):
                mi = None
            if mi is None or mi not in id_map or mi == cid:
                continue  # invalid merge target -> drop the suggestion
            merge_into = mi
        reason = str(s.get("reason") or "").strip()[:300]
        if action == "delete" and cand["status"] == "approved":
            action = "keep"
            reason = "已批准候选，GLM 建议删除被降级为保留（需人工复核）"
        if action == "delete" and cand["is_anchor"]:
            action = "keep"
            reason = "锚点候选，GLM 建议删除被降级为保留（需人工复核）"
        out.append({"candidate_id": cid, "action": action, "category": category,
                    "reason": reason, "merge_into": merge_into})
    return out


def run_glm_orchestration(conn, job_id: int) -> None:
    """Phase B job: GLM 5.2 triage over pending+approved entities, batched,
    concurrent, 429-aware. Suggestions are validated and stored as pending
    orchestration_items — nothing is landed here."""
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as futures_wait

    if not llm.llm2_configured():
        jobs.finish_job(conn, job_id, ok=False,
                        error="二次编排需要配置判别模型 GLM（异源）——请到设置页填 GLM token")
        return

    rows = conn.execute(
        "SELECT id, name, definition, status FROM candidates"
        " WHERE kind='entity' AND status IN ('pending','approved')"
        " ORDER BY id").fetchall()
    total = len(rows)
    if total == 0:
        jobs.finish_job(conn, job_id, ok=False, error="没有候选本体，先运行本体提取")
        return

    anchor_names = {r["name"] for r in conn.execute(
        "SELECT a.name FROM anchors a JOIN identities i ON i.id=a.identity_id"
        " WHERE a.status='approved' AND i.status='approved'")}
    mcounts = {r["candidate_id"]: r["c"] for r in conn.execute(
        "SELECT candidate_id, COUNT(*) c FROM mentions GROUP BY candidate_id")}
    mission_ctx = _mission_context(conn)

    # batch candidates (scheduler thread = single reader)
    cands = [{"id": r["id"], "name": r["name"], "definition": r["definition"],
              "status": r["status"], "mentions": mcounts.get(r["id"], 0),
              "is_anchor": r["name"] in anchor_names}
             for r in rows]
    batches = [cands[i:i + GLM_BATCH] for i in range(0, total, GLM_BATCH)]
    nb = len(batches)
    # resume checkpoint (DM7): progress_current = committed GLM batches, so a
    # paused job continues at batch N+1 instead of re-running every GLM call
    # (and re-inserting duplicate orchestration_items).
    jrow = conn.execute("SELECT progress_current FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
    start = min(int(jrow["progress_current"] or 0), nb) if jrow else 0
    jobs.update_progress(conn, job_id, start, nb)
    jobs.emit(conn, job_id, "orchestration.start",
              {"candidates": total, "batches": nb,
               "resumed_from": start if start else None,
               "mission": mission_ctx[:120]})

    id_map = {c["id"]: c for c in cands}
    pending = deque(range(start, nb))  # only not-yet-committed batches
    results: dict[int, list[dict]] = {}
    inflight: dict = {}
    retries: dict[int, int] = {}
    max_w, ok_streak = 2, 0  # GLM triage: lower concurrency to avoid throttling

    def _step_window(new_w: int, why: str) -> None:
        nonlocal max_w
        new_w = max(1, min(2, new_w))
        if new_w != max_w:
            max_w = new_w
            jobs.emit(conn, job_id, "orchestration.concurrency",
                      {"window": max_w, "reason": why})

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ont-orch")

    def _worker(i: int):
        jobs.set_current_job(job_id)
        jobs.set_telemetry_buffer(True)
        try:
            reply = llm.chat2([{"role": "user",
                                "content": _glm_triage_prompt(mission_ctx, batches[i])}])
            data = llm.extract_json(reply)
            return _validate_glm(data, id_map), True
        finally:
            jobs.set_telemetry_buffer(False)

    def _commit(i: int) -> None:
        suggs, _ok = results.pop(i)
        if suggs:
            batch_id = _create_batch(conn, "glm")
            _insert_items(conn, batch_id, suggs)
            jobs.emit(conn, job_id, "orchestration.batch",
                      {"batch": i + 1, "suggestions": len(suggs)})

    llm_failures = 0
    try:
        head = start
        while head < nb:
            while pending and len(inflight) < max_w:
                i = pending.popleft()
                inflight[pool.submit(_worker, i)] = i
            done, _ = (futures_wait(list(inflight), return_when=FIRST_COMPLETED,
                                    timeout=0.5) if inflight else (set(), None))
            for f in done:
                i = inflight.pop(f)
                try:
                    result, llm_ok = f.result()
                except llm.RateLimited as e:
                    retries[i] = retries.get(i, 0) + 1
                    _step_window(max_w - 1, f"429 限流（第 {retries[i]} 次重试）")
                    ok_streak = 0
                    if retries[i] <= 3:
                        pending.appendleft(i)
                        continue
                    jobs.auto_pause(conn, job_id,
                                    f"GLM 连续被限流（429）{retries[i]} 次，已暂停：{e}")
                    return
                except llm.LLMError as e:
                    jobs.auto_pause(conn, job_id,
                                    f"GLM 访问失败已自动暂停：{e}。点击恢复可重试。")
                    return
                if not llm_ok:
                    llm_failures += 1
                    _step_window(max_w - 1, "GLM 调用失败")
                    ok_streak = 0
                else:
                    ok_streak += 1
                    if ok_streak >= 8 and max_w < 2:
                        _step_window(max_w + 1, "连续成功")
                        ok_streak = 0
                results[i] = (result, llm_ok)
            jobs.drain_telemetry(conn)
            while head in results:
                jobs.poll_control(conn, job_id)
                _commit(head)
                jobs.update_progress(conn, job_id, head + 1)
                head += 1
                if head % 5 == 0 or head == nb:
                    jobs.emit(conn, job_id, "orchestration.progress",
                              {"done": head, "total": nb})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        jobs.drain_telemetry(conn)

    pending_items = conn.execute(
        "SELECT COUNT(*) c FROM orchestration_items WHERE status='pending'"
    ).fetchone()["c"]
    jobs.emit(conn, job_id, "orchestration.done",
              {"batches": nb, "llm_failures": llm_failures,
               "pending_items": pending_items})
    jobs.finish_job(conn, job_id, ok=True)


# ---------------- batch / item plumbing ----------------

def _create_batch(conn, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO orchestration_batches(source, status, created_at)"
        " VALUES(?, 'pending', ?)", (source, db.now()))
    conn.commit()
    return cur.lastrowid


def _insert_items(conn, batch_id: int, items: list[dict]) -> None:
    for it in items:
        conn.execute(
            "INSERT INTO orchestration_items(batch_id, candidate_id, action,"
            " category, reason, merge_into, status, created_at)"
            " VALUES(?,?,?,?,?,?, 'pending', ?)",
            (batch_id, it["candidate_id"], it["action"], it.get("category"),
             it.get("reason"), it.get("merge_into"), db.now()))
    conn.commit()


# ---------------- confirm / discard / dismiss (user adjudication) ----------------

def confirm_all(conn) -> dict:
    """User's one-click final adjudication over ALL pending batches.

    Executes delete/merge deterministically; keep is a no-op. Process merges
    first, then deletes (skipping any candidate that is a merge target), so a
    merge never loses its destination to an earlier delete."""
    batch_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM orchestration_batches WHERE status='pending'").fetchall()]
    if not batch_ids:
        return {"ok": False, "error": "没有待确认的编排清单"}
    marks = ",".join("?" for _ in batch_ids)
    items = conn.execute(
        f"SELECT * FROM orchestration_items WHERE status='pending' AND batch_id IN ({marks})",
        batch_ids).fetchall()

    merge_targets = {it["merge_into"] for it in items
                     if it["action"] == "merge" and it["merge_into"]}
    deleted = merged = kept = failed = 0
    # pass 1: merges
    for it in items:
        if it["action"] != "merge" or not it["merge_into"]:
            continue
        if _safe(conn, it, lambda: ontology.merge_candidate(
                conn, it["candidate_id"], it["merge_into"])):
            merged += 1
        else:
            failed += 1
    # pass 2: deletes (skip merge targets) + keeps
    for it in items:
        if it["action"] == "delete":
            if it["candidate_id"] in merge_targets:
                _mark_item(conn, it["id"], "dismissed")
                continue
            if _safe(conn, it, lambda: ontology.delete_candidates(
                    conn, [it["candidate_id"]]) > 0):
                deleted += 1
            else:
                failed += 1
        elif it["action"] == "keep":
            _mark_item(conn, it["id"], "confirmed")
            kept += 1
        # merge items already handled in pass 1
    for bid in batch_ids:
        conn.execute("UPDATE orchestration_batches SET status='confirmed',"
                     " confirmed_at=? WHERE id=?", (db.now(), bid))
    conn.commit()
    jobs.emit(conn, None, "orchestration.confirmed",
              {"deleted": deleted, "merged": merged, "kept": kept,
               "failed": failed, "batches": len(batch_ids)})
    return {"ok": True, "deleted": deleted, "merged": merged,
            "kept": kept, "failed": failed}


def _safe(conn, item, fn) -> bool:
    try:
        ok = fn()
        _mark_item(conn, item["id"], "confirmed" if ok else "dismissed")
        return bool(ok)
    except Exception:
        _mark_item(conn, item["id"], "dismissed")
        return False


def _mark_item(conn, item_id: int, status: str) -> None:
    conn.execute("UPDATE orchestration_items SET status=? WHERE id=?",
                 (status, item_id))


def discard_all(conn) -> dict:
    """Discard all pending batches (user rejects the suggestions wholesale)."""
    n_items = conn.execute(
        "UPDATE orchestration_items SET status='dismissed' WHERE status='pending'"
    ).rowcount
    n_batches = conn.execute(
        "UPDATE orchestration_batches SET status='discarded' WHERE status='pending'"
    ).rowcount
    conn.commit()
    jobs.emit(conn, None, "orchestration.discarded",
              {"batches": n_batches, "items": n_items})
    return {"ok": True, "batches": n_batches, "items": n_items}


def dismiss_item(conn, item_id: int) -> bool:
    cur = conn.execute(
        "UPDATE orchestration_items SET status='dismissed' WHERE id=? AND status='pending'",
        (item_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------------- pending summary (frontend) ----------------

MAX_LIST = 600


def pending_summary(conn) -> dict:
    """All pending suggestions (across batches) + aggregate counts."""
    batches = [dict(r) for r in conn.execute(
        "SELECT id, source, status, created_at FROM orchestration_batches"
        " WHERE status='pending' ORDER BY id DESC").fetchall()]
    rows = conn.execute(
        "SELECT oi.id, oi.batch_id, oi.candidate_id, oi.action, oi.category,"
        " oi.reason, oi.merge_into, b.source, c.name AS cand_name,"
        " c.status AS cand_status"
        " FROM orchestration_items oi"
        " JOIN orchestration_batches b ON b.id=oi.batch_id"
        " LEFT JOIN candidates c ON c.id=oi.candidate_id"
        " WHERE oi.status='pending' ORDER BY oi.id").fetchall()
    items = [dict(r) for r in rows]
    by_source: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for it in items:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1
        by_action[it["action"]] = by_action.get(it["action"], 0) + 1
        if it["category"]:
            by_category[it["category"]] = by_category.get(it["category"], 0) + 1
    return {
        "batches": batches,
        "items": items[:MAX_LIST],
        "total": len(items),
        "truncated": len(items) > MAX_LIST,
        "by_source": by_source,
        "by_action": by_action,
        "by_category": by_category,
    }
