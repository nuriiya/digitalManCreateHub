# -*- coding: utf-8 -*-
"""数字人本体装配 (persona ontology assembly).

已有数字人 + 已有本体池 -> 数字人 ontology 段（六元语之一）。

Three-layer sieve, all NOMINATE only (iron laws: the LLM never adjudicates;
the user is the single final judge):

  L0 anchor recall (0 LLM)  — candidate name matches an approved anchor
      (exact / substring) -> adopt + core
  L1 rule exclusion (0 LLM) — deterministic noise (pure number/symbol,
      single-char, citation) -> exclude + irrelevant
  L2 GLM 5.2 triage          — remaining candidates scored against the
      persona's mission + anchors -> core/marginal/irrelevant + adopt/exclude,
      validated by three deterministic gates

confirm — user's one-click final adjudication: COPY every adopted candidate
    into persona_ontology (the persona's own ontology 段, with a traceability
    back-pointer to its source candidate). Nothing is copied until confirmed.
"""
import json
from math import ceil

from . import db, jobs, llm
from .orchestration import _classify_rule, _mission_context

GLM_BATCH = 80   # candidates per GLM triage call (token-bounded)
MAX_LIST = 600


# ---------------- persona image ----------------

def _identity_by_id(conn, identity_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    return dict(row) if row else None


def _approved_anchors(conn, identity_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM anchors WHERE identity_id=? AND status='approved'",
        (identity_id,)).fetchall()
    return [r["name"].strip() for r in rows if r["name"] and r["name"].strip()]


# ---------------- L0 anchor recall (0 LLM) ----------------

def _anchor_recall(cand_name: str, anchors: list[str]) -> str | None:
    """Deterministic recall: candidate name equals an anchor, or one contains
    the other (only when the shorter side is >= 3 chars, so a generic 2-char
    word is not recalled just because a 5-char anchor contains it)."""
    n = (cand_name or "").strip()
    if len(n) < 2:
        return None
    for a in anchors:
        if not a:
            continue
        if n == a:
            return f"与锚点「{a}」同名"
        if (a in n or n in a) and min(len(a), len(n)) >= 3:
            return f"与锚点「{a}」相关"
    return None


# ---------------- L2 GLM triage (assemble flavour) ----------------

def _assembly_prompt(mission_ctx: str, batch: list[dict]) -> str:
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
        "你是数字人本体装配器，只提名、不裁决。\n"
        f"{mission_ctx}\n\n"
        "以下是候选本体（编号 | 名称 | 提及次数 | 状态 | 定义）。"
        "请判断每个候选是否应该装配进该数字人的本体（ontology 段）：\n"
        + "\n".join(lines) + "\n\n"
        "对每个候选输出：\n"
        "1. category 相关度分档：core（与使命/锚点强相关、该数字人必须掌握的"
        "核心概念） | marginal（边缘相关、可选掌握） | irrelevant（无关噪音："
        "实验数据、引用、通用词、论文细节等）\n"
        "2. action 装配建议：adopt（装配进数字人本体） | exclude（不装配）\n"
        "规则：\n"
        "- core 一律 adopt；irrelevant 一律 exclude\n"
        "- marginal 默认 adopt（保守），除非明显是噪音才 exclude\n"
        "- 已批准（approved）候选不要 exclude\n"
        "- 锚点候选不要 exclude\n"
        "只输出建议，不执行。严格按 JSON 输出：\n"
        '{"suggestions": [{"id": 编号, "action": "adopt|exclude",'
        ' "category": "core|marginal|irrelevant", "reason": "一句中文"}]}\n'
        "id 必须是列表里出现的编号，不得编造。")


def _validate_assembly(data, id_map: dict[int, dict]) -> list[dict]:
    """Three deterministic gates on GLM output (LLM only nominates):

      G1 闭集关 — action/category must be in the closed enum
      G2 引用关 — id must map back to a real candidate
      G3 冲突关 — approved/anchor candidates are never auto-excluded
                  (demote exclude -> adopt)
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
        if action not in ("adopt", "exclude"):
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
        reason = str(s.get("reason") or "").strip()[:300]
        if action == "exclude" and cand["status"] == "approved":
            action = "adopt"
            reason = "已批准候选，GLM 建议排除被降级为装配（需人工复核）"
        if action == "exclude" and cand["is_anchor"]:
            action = "adopt"
            reason = "锚点候选，GLM 建议排除被降级为装配（需人工复核）"
        out.append({"candidate_id": cid, "action": action, "category": category,
                    "reason": reason})
    return out


def _sieve(rows, anchors: list[str]) -> tuple[list[dict], list[dict]]:
    """L0/L1 deterministic pre-pass (0 LLM), idempotent: anchor recall +
    rule exclusion. Returns (pre_items, glm_pool). Called on both fresh runs
    and resume — the same inputs always produce the same split, so the L2
    batch layout (and therefore the checkpoint) stays stable across resume."""
    pre_items: list[dict] = []
    glm_pool: list[dict] = []
    for r in rows:
        rec = _anchor_recall(r["name"], anchors)
        if rec:
            pre_items.append({"candidate_id": r["id"], "action": "adopt",
                              "category": "core", "reason": "锚点召回：" + rec})
            continue
        cls = _classify_rule(r["name"])
        if cls:
            _, category, reason = cls
            pre_items.append({"candidate_id": r["id"], "action": "exclude",
                              "category": category or "irrelevant",
                              "reason": "规则排除：" + reason})
            continue
        glm_pool.append(dict(r))
    return pre_items, glm_pool


def run_assembly(conn, job_id: int, identity_id: int) -> None:
    """Assembly job (kind=assemble): persona image -> L0/L1 (0 LLM) -> L2 GLM
    triage -> pending assembly_items. Nothing is copied here.

    Resume = batch checkpoint (DM7): progress_current records how many L2 GLM
    batches are already committed. A paused job continues at batch N+1 instead
    of re-running every GLM call (the expensive part) AND re-seeding L0/L1.
    L0/L1 is deterministic + 0 LLM, so it is re-derived on resume but its
    pre_items are NOT re-inserted (they already live in the kept pending
    batch). A brand-new job (progress_current == 0) still discards any stale
    pending batch and starts clean — that's a re-assemble, not a resume."""
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as futures_wait

    ident = _identity_by_id(conn, identity_id)
    if not ident:
        jobs.finish_job(conn, job_id, ok=False,
                        error=f"数字人 #{identity_id} 不存在")
        return
    identity_id = ident["id"]
    anchors = _approved_anchors(conn, identity_id)
    if not llm.llm2_configured():
        jobs.finish_job(conn, job_id, ok=False,
                        error="本体装配需要配置判别模型 GLM（异源）——请到设置页填 GLM token")
        return

    rows = conn.execute(
        "SELECT id, name, definition, status FROM candidates"
        " WHERE kind='entity' AND status IN ('pending','approved')"
        " ORDER BY id").fetchall()
    total = len(rows)
    if total == 0:
        jobs.finish_job(conn, job_id, ok=False, error="没有候选本体，先运行本体提取")
        return

    # checkpoint: how many L2 GLM batches were already committed (DM7).
    jrow = conn.execute("SELECT progress_current FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
    start = int(jrow["progress_current"] or 0) if jrow else 0

    anchor_names = set(anchors)
    mcounts = {r["candidate_id"]: r["c"] for r in conn.execute(
        "SELECT candidate_id, COUNT(*) c FROM mentions GROUP BY candidate_id")}
    mission_ctx = _mission_context(conn)

    # deterministic L0/L1 + glm_pool split (idempotent)
    pre_items, glm_pool = _sieve(rows, anchors)

    # Pick the batch this run appends into. A resume (start > 0) reuses the
    # pending batch that already holds L0/L1 pre_items + committed L2 items;
    # a fresh run discards stale pending batches and starts a clean one.
    if start > 0:
        b = conn.execute(
            "SELECT id FROM assembly_batches WHERE identity_id=? AND status='pending'"
            " ORDER BY id DESC LIMIT 1", (identity_id,)).fetchone()
        if b:
            batch_id = b["id"]
        else:
            # progress says mid-L2 but the pending batch is gone (cleared
            # externally) — fall back to a clean start rather than appending
            # to nothing.
            start = 0
            batch_id = _create_batch(conn, identity_id)
            if pre_items:
                _insert_items(conn, batch_id, pre_items)
    else:
        # archive (not delete) any stale pending batch so its items remain
        # recoverable via restore_assembly (DM8: assembly history is never
        # physically dropped by a re-run — the user can always undo/recover).
        for old in conn.execute(
                "SELECT id FROM assembly_batches WHERE identity_id=? AND status='pending'",
                (identity_id,)).fetchall():
            conn.execute("UPDATE assembly_items SET status='dismissed' WHERE batch_id=?",
                         (old["id"],))
            conn.execute("UPDATE assembly_batches SET status='discarded' WHERE id=?",
                         (old["id"],))
        conn.commit()
        batch_id = _create_batch(conn, identity_id)
        if pre_items:
            _insert_items(conn, batch_id, pre_items)

    nb = ceil(len(glm_pool) / GLM_BATCH) if glm_pool else 0
    start = min(start, nb)  # candidates may have shrunk since the checkpoint
    jobs.update_progress(conn, job_id, start, max(nb, 1))
    jobs.emit(conn, job_id, "assembly.start", {
        "identity": ident["name"], "candidates": total,
        "anchors_hit": sum(1 for i in pre_items if i["action"] == "adopt"),
        "rule_excluded": sum(1 for i in pre_items if i["action"] == "exclude"),
        "glm_pool": len(glm_pool), "batches": nb,
        "resumed_from": start if start else None,
        "mission": mission_ctx[:120]})

    if not glm_pool:
        jobs.emit(conn, job_id, "assembly.done",
                  {"batches": 0, "llm_failures": 0, "pending_items": len(pre_items)})
        jobs.finish_job(conn, job_id, ok=True)
        return

    cands = [{"id": r["id"], "name": r["name"], "definition": r["definition"],
              "status": r["status"], "mentions": mcounts.get(r["id"], 0),
              "is_anchor": r["name"] in anchor_names}
             for r in glm_pool]
    batches = [cands[i:i + GLM_BATCH] for i in range(0, len(cands), GLM_BATCH)]
    id_map = {c["id"]: c for c in cands}

    pending = deque(range(start, nb))  # only the not-yet-committed batches
    results: dict[int, list[dict]] = {}
    inflight: dict = {}
    retries: dict[int, int] = {}
    max_w, ok_streak = 2, 0

    def _step_window(new_w: int, why: str) -> None:
        nonlocal max_w
        new_w = max(1, min(2, new_w))
        if new_w != max_w:
            max_w = new_w
            jobs.emit(conn, job_id, "assembly.concurrency",
                      {"window": max_w, "reason": why})

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ont-asm")

    def _worker(i: int):
        jobs.set_current_job(job_id)
        jobs.set_telemetry_buffer(True)
        try:
            reply = llm.chat2([{"role": "user",
                                "content": _assembly_prompt(mission_ctx, batches[i])}])
            data = llm.extract_json(reply)
            return _validate_assembly(data, id_map), True
        finally:
            jobs.set_telemetry_buffer(False)

    def _commit(i: int) -> None:
        suggs, _ok = results.pop(i)
        if suggs:
            _insert_items(conn, batch_id, suggs)
            jobs.emit(conn, job_id, "assembly.batch",
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
                    jobs.emit(conn, job_id, "assembly.progress",
                              {"done": head, "total": nb})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        jobs.drain_telemetry(conn)

    pending_items = conn.execute(
        "SELECT COUNT(*) c FROM assembly_items WHERE status='pending'"
    ).fetchone()["c"]
    jobs.emit(conn, job_id, "assembly.done",
              {"batches": nb, "llm_failures": llm_failures,
               "pending_items": pending_items})
    jobs.finish_job(conn, job_id, ok=True)


# ---------------- batch / item plumbing ----------------

def _create_batch(conn, identity_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO assembly_batches(identity_id, status, created_at)"
        " VALUES(?, 'pending', ?)", (identity_id, db.now()))
    conn.commit()
    return cur.lastrowid


def _insert_items(conn, batch_id: int, items: list[dict]) -> None:
    for it in items:
        conn.execute(
            "INSERT INTO assembly_items(batch_id, candidate_id, action,"
            " category, reason, status, created_at)"
            " VALUES(?,?,?,?,?, 'pending', ?)",
            (batch_id, it["candidate_id"], it["action"], it.get("category"),
             it.get("reason"), db.now()))
    conn.commit()


# ---------------- confirm / discard / dismiss ----------------

def confirm_assembly(conn, identity_id: int | None = None) -> dict:
    """User's one-click final adjudication over pending batches (optionally
    scoped to one persona). Copies every adopted candidate into
    persona_ontology; exclude is a no-op. Nothing is copied until this call."""
    q = "SELECT * FROM assembly_batches WHERE status='pending'"
    args: tuple = ()
    if identity_id is not None:
        q += " AND identity_id=?"
        args = (identity_id,)
    batches = [dict(r) for r in conn.execute(q, args).fetchall()]
    if not batches:
        return {"ok": False, "error": "没有待确认的装配清单"}
    adopted = excluded = skipped = failed = 0
    for b in batches:
        identity_id = b["identity_id"]
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM assembly_items WHERE batch_id=? AND status='pending'",
            (b["id"],)).fetchall()]
        for it in items:
            if it["action"] == "exclude":
                _mark_item(conn, it["id"], "dismissed")
                excluded += 1
                continue
            cand = conn.execute(
                "SELECT kind, name, definition FROM candidates WHERE id=?",
                (it["candidate_id"],)).fetchone()
            if not cand:
                _mark_item(conn, it["id"], "dismissed")
                failed += 1
                continue
            exists = conn.execute(
                "SELECT id FROM persona_ontology WHERE identity_id=? AND kind=? AND name=?",
                (identity_id, cand["kind"], cand["name"])).fetchone()
            if exists:
                _mark_item(conn, it["id"], "confirmed")
                skipped += 1
                continue
            try:
                conn.execute(
                    "INSERT INTO persona_ontology(identity_id, kind, name,"
                    " definition, source_candidate_id, status, created_at)"
                    " VALUES(?,?,?,?,?, 'active', ?)",
                    (identity_id, cand["kind"], cand["name"], cand["definition"],
                     it["candidate_id"], db.now()))
                _mark_item(conn, it["id"], "confirmed")
                adopted += 1
            except Exception:
                _mark_item(conn, it["id"], "dismissed")
                failed += 1
        conn.execute(
            "UPDATE assembly_batches SET status='confirmed', confirmed_at=? WHERE id=?",
            (db.now(), b["id"]))
    conn.commit()
    jobs.emit(conn, None, "assembly.confirmed",
              {"adopted": adopted, "excluded": excluded,
               "skipped": skipped, "failed": failed, "batches": len(batches)})
    return {"ok": True, "adopted": adopted, "excluded": excluded,
            "skipped": skipped, "failed": failed}


def discard_assembly(conn, identity_id: int | None = None) -> dict:
    """Discard pending batches (optionally scoped to one persona): the user
    rejects the suggestions wholesale."""
    if identity_id is not None:
        n_items = conn.execute(
            "UPDATE assembly_items SET status='dismissed' WHERE status='pending'"
            " AND batch_id IN (SELECT id FROM assembly_batches WHERE identity_id=?)",
            (identity_id,)).rowcount
        n_batches = conn.execute(
            "UPDATE assembly_batches SET status='discarded' WHERE status='pending'"
            " AND identity_id=?", (identity_id,)).rowcount
    else:
        n_items = conn.execute(
            "UPDATE assembly_items SET status='dismissed' WHERE status='pending'"
        ).rowcount
        n_batches = conn.execute(
            "UPDATE assembly_batches SET status='discarded' WHERE status='pending'"
        ).rowcount
    conn.commit()
    jobs.emit(conn, None, "assembly.discarded",
              {"batches": n_batches, "items": n_items, "identity_id": identity_id})
    return {"ok": True, "batches": n_batches, "items": n_items}


def restore_assembly(conn, identity_id: int | None = None) -> dict:
    """Restore the most recently discarded assembly batch back to pending —
    undo a discard, or recover suggestions that a re-run archived (DM8).
    Only rows still in the 'dismissed' state are resurrected; rows that an
    old-version run physically deleted are gone and cannot be brought back."""
    q = "SELECT id, identity_id FROM assembly_batches WHERE status='discarded'"
    args: tuple = ()
    if identity_id is not None:
        q += " AND identity_id=?"
        args = (identity_id,)
    q += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(q, args).fetchone()
    if not row:
        return {"ok": False, "error": "没有可恢复的装配批次（历史批次已清理或从未丢弃）"}
    batch_id = row["id"]
    n = conn.execute(
        "UPDATE assembly_items SET status='pending' WHERE batch_id=? AND status='dismissed'",
        (batch_id,)).rowcount
    conn.execute("UPDATE assembly_batches SET status='pending' WHERE id=?",
                 (batch_id,))
    conn.commit()
    jobs.emit(conn, None, "assembly.restored",
              {"batch_id": batch_id, "items": n,
               "identity_id": row["identity_id"]})
    return {"ok": True, "batch_id": batch_id, "items": n,
            "identity_id": row["identity_id"]}


def dismiss_assembly_item(conn, item_id: int) -> bool:
    cur = conn.execute(
        "UPDATE assembly_items SET status='dismissed' WHERE id=? AND status='pending'",
        (item_id,))
    conn.commit()
    return cur.rowcount > 0


def _mark_item(conn, item_id: int, status: str) -> None:
    conn.execute("UPDATE assembly_items SET status=? WHERE id=?", (status, item_id))


# ---------------- frontend reads ----------------

def pending_summary(conn, identity_id: int | None = None) -> dict:
    """Pending suggestions (optionally scoped to one persona) + aggregate counts."""
    bq = ("SELECT b.id, b.identity_id, b.status, b.created_at, i.name AS identity_name"
          " FROM assembly_batches b LEFT JOIN identities i ON i.id=b.identity_id"
          " WHERE b.status='pending'")
    iq = ("SELECT ai.id, ai.batch_id, ai.candidate_id, ai.action, ai.category,"
          " ai.reason, c.name AS cand_name, c.definition AS cand_definition,"
          " c.status AS cand_status,"
          " (SELECT COUNT(*) FROM mentions m WHERE m.candidate_id=ai.candidate_id) AS mentions"
          " FROM assembly_items ai"
          " LEFT JOIN candidates c ON c.id=ai.candidate_id"
          " WHERE ai.status='pending'")
    args: tuple = ()
    if identity_id is not None:
        bq += " AND b.identity_id=?"
        iq += " AND ai.batch_id IN (SELECT id FROM assembly_batches WHERE identity_id=?)"
        args = (identity_id,)
    bq += " ORDER BY b.id DESC"
    iq += " ORDER BY ai.id"
    batches = [dict(r) for r in conn.execute(bq, args).fetchall()]
    rows = conn.execute(iq, args).fetchall()
    items = [dict(r) for r in rows]
    by_action: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for it in items:
        by_action[it["action"]] = by_action.get(it["action"], 0) + 1
        if it["category"]:
            by_category[it["category"]] = by_category.get(it["category"], 0) + 1
    # how many discarded batches are recoverable (DM8) — surfaced so the UI
    # can offer a "恢复上次装配" button when history exists.
    rq = "SELECT COUNT(*) c FROM assembly_batches WHERE status='discarded'"
    rargs: tuple = ()
    if identity_id is not None:
        rq += " AND identity_id=?"
        rargs = (identity_id,)
    restorable = conn.execute(rq, rargs).fetchone()["c"]
    return {
        "batches": batches,
        "items": items[:MAX_LIST],
        "total": len(items),
        "truncated": len(items) > MAX_LIST,
        "by_action": by_action,
        "by_category": by_category,
        "restorable": restorable,
    }


def persona_ontology_list(conn, identity_id: int | None = None) -> list[dict]:
    """The persona's own ontology 段 (copied/assembled entities)."""
    q = ("SELECT p.id, p.identity_id, p.kind, p.name, p.definition,"
         " p.source_candidate_id, p.status, p.created_at, i.name AS identity_name"
         " FROM persona_ontology p LEFT JOIN identities i ON i.id=p.identity_id")
    args: tuple = ()
    if identity_id is not None:
        q += " WHERE p.identity_id=?"
        args = (identity_id,)
    q += " ORDER BY p.id"
    return [dict(r) for r in conn.execute(q, args).fetchall()]
