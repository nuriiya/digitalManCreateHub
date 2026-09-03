# -*- coding: utf-8 -*-
"""Ontology decomposition (EDC-lite): LLM nominates, code adjudicates.

Pipeline per chunk:
  1. open extraction (V4-Flash) -> JSON candidates with mentions
  2. GATE structure (closed enums / types / lengths)      - deterministic
  3. GATE evidence (mention text must literally occur in chunk text, span kept)
  4. GATE semantic (dedupe against existing candidates)   - deterministic
  5. store as *pending* candidates -> user approves/merges/rejects

LLM output is NEVER trusted directly: a fabricated mention that does not
literally appear in the chunk is dropped on the spot.
"""
import json
import re

from . import db, jobs, llm

ENTITY_TYPES = {"概念", "角色", "系统", "流程", "规则", "对象", "其他"}
MAX_NAME_LEN = 32
MAX_DEFINITION_LEN = 300


# ---------------- gate 1: structure ----------------

def validate_structure(cand: dict) -> tuple[bool, str]:
    """Closed-set enums + type/length checks for one nominated candidate."""
    if not isinstance(cand, dict):
        return False, "not an object"
    name = cand.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "missing name"
    if len(name.strip()) > MAX_NAME_LEN:
        return False, "name too long"
    etype = cand.get("type")
    if etype is not None and etype not in ENTITY_TYPES:
        return False, f"type not in closed set: {etype}"
    definition = cand.get("definition")
    if definition is not None:
        if not isinstance(definition, str):
            return False, "definition not a string"
        if len(definition) > MAX_DEFINITION_LEN:
            return False, "definition too long"
    mentions = cand.get("mentions")
    if mentions is not None:
        if not isinstance(mentions, list) or not mentions:
            return False, "mentions must be a non-empty list"
        for m in mentions:
            if not isinstance(m, str) or not m.strip():
                return False, "mention must be non-empty string"
    return True, ""


# ---------------- gate 2: evidence ----------------

def locate_mention(mention: str, chunk_text: str) -> tuple[int, int] | None:
    """Evidence gate: span must match the chunk text literally.
    Returns (start, end) or None (fabricated evidence -> candidate rejected)."""
    if not mention or not chunk_text:
        return None
    m = mention.strip()
    idx = chunk_text.find(m)
    if idx == -1:
        return None
    return (idx, idx + len(m))


# ---------------- gate 3: semantics (dedupe) ----------------

def find_duplicate(conn, kind: str, name: str, exclude_id: int | None = None) -> dict | None:
    row = conn.execute(
        "SELECT id, kind, name, definition, status FROM candidates"
        " WHERE kind=? AND name=?", (kind, name.strip())).fetchone()
    if row is None:
        return None
    if exclude_id is not None and row["id"] == exclude_id:
        return None
    return dict(row)


# ---------------- extraction ----------------

# Concurrent extraction: parallel LLM calls per job. Starts at CONCURRENCY
# workers and ADAPTIVELY STEPS DOWN (-1 per failure: HTTP 429 or a failed
# call that fell back to rule mode), floor 1, and steps back up (+1) only
# after OK_STREAK consecutive successes - failures cost throughput, but a
# persistent failure never pauses the job unless it exhausts rate retries.
CONCURRENCY = 4
MAX_RATE_RETRIES = 3
OK_STREAK = 8
# per-chunk extraction cap: refined extraction, not exhaustive enumeration.
# The prompt asks for quality over quantity; this slice is the deterministic
# backstop (the LLM is never trusted to self-limit alone).
MAX_EXTRACT = 20

def _extract_prompt(chunk_text: str, anchors: str = "") -> str:
    return (
        (anchors or "")
        + "你是本体提名器。精炼提取：只抽取最有价值的候选实体与关系，"
        "合并重复与近义项，去芜存菁；实体与关系各不超过 20 个。"
        "mentions 必须是文本中**逐字出现**的原文片段（这是硬性要求，"
        "不允许改写、翻译或概括）。\n"
        "同时请针对该文本内容出 2~3 道考题（用于之后检验提取出的本体是否够用），"
        "考题的 evidence 必须是文本中**逐字出现**的原文片段。\n"
        "严格按 JSON 输出：\n"
        '{"entities": [{"name": "...", "type": "概念|角色|系统|流程|规则|对象|其他",'
        ' "definition": "...", "mentions": ["原文片段", ...]}],\n'
        ' "relations": [{"source": "实体名", "target": "实体名", "type": "..."}],\n'
        ' "quiz": [{"q": "检验问题", "a": "预期答案", "evidence": "原文片段"}]}\n'
        "若文本中没有可抽取内容，输出 {\"entities\": [], \"relations\": [], \"quiz\": []}。\n\n"
        "文本：\n" + chunk_text)


def extract_chunk(chunk_text: str, anchors: str = "") -> tuple[dict, bool]:
    """LLM nomination only.

    Returns (result, llm_ok): llm_ok=False means the LLM was configured but
    the call failed (rule fallback used) - callers must surface that."""
    if not llm.llm_configured():
        return _rule_extract(chunk_text), True  # unconfigured = intended offline mode
    try:
        data = llm.extract_json(llm.chat([{"role": "user",
                                           "content": _extract_prompt(chunk_text, anchors)}]))
        if isinstance(data, dict):
            return ({"entities": (data.get("entities") or [])[:MAX_EXTRACT],
                     "relations": (data.get("relations") or [])[:MAX_EXTRACT],
                     "quiz": data.get("quiz") or []}, True)
        if isinstance(data, list):  # model returned a bare array
            return ({"entities": data[:MAX_EXTRACT], "relations": [], "quiz": []},
                    True)
    except llm.LLMError:
        raise  # configured LLM unreachable -> caller auto-pauses (iron law 2)
    except Exception:
        return _rule_extract(chunk_text), False  # call failed (401/network/...)
    # LLM replied but JSON unparseable -> rule fallback, call itself was fine
    return _rule_extract(chunk_text), True


def _rule_extract(chunk_text: str) -> dict:
    """Offline fallback nominator: quoted terms 《...》/「...」/\"...\" plus
    entity-defining sentences (X是Y). Deterministic, no LLM."""
    entities = []
    seen = set()
    for m in re.finditer(r'[《「"]([^《》「"]{2,20})[》」"]', chunk_text):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        entities.append({"name": name, "type": "概念",
                         "definition": f"文档中以引号标注的术语：{name}",
                         "mentions": [name]})
    for m in re.finditer(r'([\u4e00-\u9fa5A-Za-z0-9]{2,16})(?:是|是指|指的是)([\u4e00-\u9fa5A-Za-z0-9]{2,40})', chunk_text):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        entities.append({"name": name, "type": "概念",
                         "definition": f"{chunk_text[m.start():m.start()+60]}",
                         "mentions": [name]})
    return {"entities": entities[:MAX_EXTRACT], "relations": []}


def run_extraction(conn, job_id: int) -> None:
    """Ontology job: nominate + validate + store pending, CONCURRENCY workers.

    Concurrency model (thread-safe by construction):
      - workers ONLY call the LLM + run the pure gates (zero DB access: their
        llm.* telemetry is buffered and drained by the scheduler - writing
        the shared conn from worker threads corrupted transaction
        boundaries and could crash on conn close)
      - the scheduler thread owns ALL writes (candidates/mentions/relations,
        progress, events) - single-writer discipline on the shared
        db.get_conn() singleton
      - results commit IN chunk order (head-of-line): progress_current stays
        a strict checkpoint. A fast chunk 9 finishing before slow chunk 2
        waits in the results map; progress only advances past chunk 2 when
        chunk 2 is committed. Resume therefore never skips un-extracted
        chunks (committing by completion count would corrupt the checkpoint)
      - adaptive window: starts at CONCURRENCY (4); every failure (HTTP 429
        or a call that fell back to rule mode) steps the in-flight window
        DOWN by 1 (floor 1); after OK_STREAK consecutive successes it steps
        back UP by 1 (cap CONCURRENCY). 429s additionally re-queue the chunk
        (<= MAX_RATE_RETRIES); other hard LLM failures still auto-pause
        loudly (iron law 2)
      - pause/delete is checked at each commit boundary; in-flight workers
        are cancelled (not started ones) / abandoned (running ones) and the
        checkpoint stays at the last committed chunk

    Resume = chunk checkpoint (DM7): progress_current records how many
    chunks (in doc_id,seq order) are already extracted - the scheduler
    dispatches only chunks after it, so a paused job continues at chunk N+1
    instead of re-LLM-ing everything (~90s/chunk, 592 chunks). Dedupe stays
    as a second safety net, not the resume mechanism."""
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as futures_wait

    from . import identity as identity_mod

    rows = conn.execute("SELECT id, text FROM chunks ORDER BY doc_id, seq").fetchall()
    total = len(rows)
    jrow = conn.execute(
        "SELECT progress_current FROM jobs WHERE id=?", (job_id,)).fetchone()
    # clamp: chunks may have been deleted/re-ingested since the checkpoint
    start = min(int(jrow["progress_current"] or 0), total) if jrow else 0
    jobs.update_progress(conn, job_id, start, total)
    jobs.emit(conn, job_id, "ontology.start",
              {"chunks": total, "resumed_from": start} if start
              else {"chunks": total})

    # anchor pre-screening: approved anchors of approved identities guide the
    # extraction (GUIDANCE, not a whitelist). Read once here (scheduler thread);
    # resume re-reads, so newly approved anchors apply to not-yet-extracted
    # chunks without re-LLM-ing already-done ones.
    anchors = identity_mod.anchor_block(conn)
    if anchors:
        jobs.emit(conn, job_id, "ontology.anchors",
                  {"note": "已注入已批准锚点（引导非白名单）"})

    added, rejected_structure, rejected_evidence, dupes = 0, 0, 0, 0
    quiz_added = 0
    llm_failures = 0
    pending = deque(range(start + 1, total + 1))  # 1-based chunk indexes
    results: dict[int, tuple[dict, bool]] = {}   # idx -> (result, llm_ok)
    inflight: dict = {}                          # future -> idx
    retries: dict[int, int] = {}                 # idx -> 429 retry count
    max_w = CONCURRENCY
    ok_streak = 0

    def _step_window(new_w: int, why: str) -> None:
        """Adaptive concurrency: -1 per failure (floor 1), +1 per OK_STREAK
        successes (cap CONCURRENCY). Change is surfaced as an event."""
        nonlocal max_w
        new_w = max(1, min(CONCURRENCY, new_w))
        if new_w != max_w:
            max_w = new_w
            jobs.emit(conn, job_id, "ontology.concurrency",
                      {"window": max_w, "reason": why})

    pool = ThreadPoolExecutor(max_workers=CONCURRENCY,
                              thread_name_prefix="ont-extract")

    def _worker(idx: int):
        jobs.set_current_job(job_id)  # llm.* telemetry attributes to this job
        # buffer mode: workers NEVER write the shared conn themselves - the
        # scheduler drains their telemetry (single-writer discipline; also
        # keeps an abandoned worker harmless after a pause/conn close)
        jobs.set_telemetry_buffer(True)
        try:
            return extract_chunk(rows[idx - 1]["text"], anchors)
        finally:
            jobs.set_telemetry_buffer(False)

    def _commit(idx: int, r) -> None:
        """Store one chunk's nominations (scheduler thread only)."""
        nonlocal added, rejected_structure, rejected_evidence, dupes, quiz_added
        result, llm_ok = results.pop(idx)
        chunk_text = r["text"]
        for ent in result.get("entities") or []:
            ok, reason = validate_structure(ent)
            if not ok:
                rejected_structure += 1
                continue
            mentions = ent.get("mentions") or [ent["name"]]
            spans = []
            for men in mentions:
                span = locate_mention(men, chunk_text)
                if span:
                    spans.append((span[0], span[1], men.strip()))
            if not spans:
                rejected_evidence += 1
                continue  # fabricated evidence - dropped, LLM never trusted
            if find_duplicate(conn, "entity", ent["name"]):
                dupes += 1
                continue
            cur = conn.execute(
                "INSERT INTO candidates(kind, name, definition, status, created_at)"
                " VALUES('entity', ?, ?, 'pending', ?)",
                (ent["name"].strip(), (ent.get("definition") or "").strip(), db.now()))
            conn.commit()
            cand_id = cur.lastrowid
            for s, e, t in spans:
                conn.execute(
                    "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end, text)"
                    " VALUES(?,?,?,?,?)", (cand_id, r["id"], s, e, t))
            conn.commit()
            added += 1
        # relations: only if both names exist as entity candidates
        for rel in result.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            src, tgt = rel.get("source"), rel.get("target")
            rtype = (rel.get("type") or "").strip()
            if not (src and tgt and rtype):
                continue
            ok, _ = validate_structure({"name": src})
            ok2, _ = validate_structure({"name": tgt})
            if not (ok and ok2):
                rejected_structure += 1
                continue
            src_row = conn.execute("SELECT id FROM candidates WHERE kind='entity' AND name=?",
                                   (src.strip(),)).fetchone()
            tgt_row = conn.execute("SELECT id FROM candidates WHERE kind='entity' AND name=?",
                                   (tgt.strip(),)).fetchone()
            if not (src_row and tgt_row):
                continue  # dangling relation: not an error, just not storable
            existing = conn.execute(
                "SELECT id FROM relations WHERE source_id=? AND target_name=? AND relation_type=?",
                (src_row["id"], tgt.strip(), rtype)).fetchone()
            if existing:
                dupes += 1
                continue
            conn.execute(
                "INSERT INTO relations(source_id, target_name, relation_type, chunk_id)"
                " VALUES(?,?,?,?)", (src_row["id"], tgt.strip(), rtype, r["id"]))
            conn.commit()
            added += 1
        # quiz generated in the SAME llm call: evidence gate (literal span),
        # invalid questions are dropped whole - the LLM is never trusted
        for qz in (result.get("quiz") or [])[:3]:
            if not isinstance(qz, dict):
                continue
            q = str(qz.get("q") or "").strip()
            a = str(qz.get("a") or "").strip()
            ev = str(qz.get("evidence") or "").strip()
            if not (q and a and ev):
                continue
            if not locate_mention(ev, chunk_text):
                rejected_evidence += 1
                continue  # fabricated evidence -> question dropped
            conn.execute(
                "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
                " VALUES(?,?,?,?,?)", (r["id"], q, a, ev, db.now()))
            conn.commit()
            quiz_added += 1

    try:
        head = start + 1
        while head <= total:
            # 1. top up the in-flight window at the current concurrency
            while pending and len(inflight) < max_w:
                idx = pending.popleft()
                inflight[pool.submit(_worker, idx)] = idx
            # 2. wait for completions (any order); tick often so 429 feedback
            #    from the fast ones reaches the scheduler promptly
            done, _ = (futures_wait(list(inflight), return_when=FIRST_COMPLETED,
                                    timeout=0.5) if inflight else (set(), None))
            for f in done:
                idx = inflight.pop(f)
                try:
                    result, llm_ok = f.result()
                except llm.RateLimited as e:
                    retries[idx] = retries.get(idx, 0) + 1
                    _step_window(max_w - 1,
                                 f"429 限流（第 {retries[idx]} 次重试）")
                    ok_streak = 0
                    if retries[idx] <= MAX_RATE_RETRIES:
                        pending.appendleft(idx)  # requeue at lower concurrency
                        continue
                    jobs.auto_pause(conn, job_id,
                                    f"LLM 连续被限流（429）{retries[idx]} 次，已暂停：{e}")
                    return
                except llm.LLMError as e:
                    # configured LLM unreachable -> pause loudly instead of
                    # silently degrading all chunks to rule quality (law 2/3)
                    jobs.auto_pause(conn, job_id,
                                    f"LLM 访问失败已自动暂停：{e}。点击恢复可重试，或检查模型设置。")
                    return
                if not llm_ok:
                    llm_failures += 1
                    _step_window(max_w - 1, "LLM 调用失败（规则兜底）")
                    ok_streak = 0
                else:
                    ok_streak += 1
                    if ok_streak >= OK_STREAK and max_w < CONCURRENCY:
                        _step_window(max_w + 1, f"连续 {OK_STREAK} 次成功")
                        ok_streak = 0
                results[idx] = (result, llm_ok)
            # 2.5 flush buffered worker telemetry - scheduler is the only
            # thread that ever writes the shared connection
            jobs.drain_telemetry(conn)
            # 3. commit strictly in order (head-of-line) - keeps the
            #    checkpoint sound; pause/delete checked at the boundary
            while head in results:
                jobs.poll_control(conn, job_id)
                _commit(head, rows[head - 1])
                jobs.update_progress(conn, job_id, head)  # checkpoint advance
                head += 1
                if (head - 1) % 5 == 0 or head - 1 == total:
                    jobs.emit(conn, job_id, "ontology.progress",
                              {"done": head - 1, "total": total, "added": added})
    finally:
        # normal end: everything already consumed, nothing to wait for.
        # pause/cancel: cancel not-yet-started tasks; running ones finish on
        # their daemon threads and their results are simply dropped (checkpoint
        # already points past them - they re-extract on resume, <=CONCURRENCY
        # wasted LLM calls per pause, which matches 'wait for in-flight work')
        pool.shutdown(wait=False, cancel_futures=True)
        jobs.drain_telemetry(conn)  # last worker telemetry before done/pause

    jobs.emit(conn, job_id, "ontology.done", {
        "added": added, "rejected_structure": rejected_structure,
        "rejected_evidence": rejected_evidence, "duplicates": dupes,
        "quiz_added": quiz_added,
        "llm_failures": llm_failures, "total": total})
    if total and llm_failures == total:
        # every LLM call failed (e.g. 401 bad key, unreachable endpoint):
        # a "done" with zero candidates would be a silent lie (iron law 2)
        jobs.finish_job(conn, job_id, ok=False, error=(
            f"LLM 调用全部失败（{llm_failures}/{total} 次）——请到设置页检查"
            " base_url / token（点「保存并测试连通」），修复后点任务的「继续」重跑"))
        return
    jobs.finish_job(conn, job_id, ok=True)
    # phase A: deterministic rule clean (0 LLM) auto-runs after extraction —
    # it only NOMINATES a pending batch; nothing is deleted until the user
    # confirms. Advisory: a failure here must never fail the extraction.
    try:
        from . import orchestration
        orchestration.run_rule_clean(conn)
    except Exception:
        pass
    # exam phase: auto-triggered after a successful extraction (skipped when
    # no quiz survived the evidence gate); also runnable manually via API
    _auto_trigger_exam(conn, quiz_added)


def _auto_trigger_exam(conn, quiz_total: int) -> None:
    """After extraction finishes OK, grade the candidates with the generated
    quiz. fail-only pending candidates are deleted automatically afterwards;
    everything else stays for the user's final approval (iron law: the user
    is the only final judge - auto-deletion only removes deterministic
    failures, never makes approvals)."""
    if not quiz_total:
        return
    if jobs.active_job(conn, "exam"):
        return  # one exam at a time (running or paused)
    exam_job = jobs.create_job(conn, "exam", total=quiz_total,
                               detail="审批前考核（提取完成后自动触发）")
    jobs.emit(conn, None, "exam.auto_started", {"job_id": exam_job})
    jobs.run_in_background(exam_job, run_exam)


# ---------------- exam phase v2 (A/B ablation, dual-LLM) ----------------

def _subgraph_text(cands: list[dict], relations: list[dict]) -> str:
    """Deterministic subgraph rendering for the closed-book generator.

    Rendered as readable fact lines (not a bare triple dump): entities with
    definitions plus typed relations. The generator reasons over these lines
    and must not invent anything outside them."""
    parts = []
    for c in cands:
        parts.append(f"- {c['name']}（{c['kind']}）：{c['definition'] or '（无定义）'}")
    for r in relations:
        parts.append(f"- {r['source_name']} --[{r['relation_type']}]--> {r['target_name']}")
    return "\n".join(parts) if parts else "（无）"


def _gen_prompt(question: str, subgraph: str) -> str:
    """LLM-1 closed-book prompt (with ontology subgraph, raw text withheld)."""
    return (
        "下面有一道关于某段文本的考题，以及从该文本提取出的本体子图。\n"
        f"考题：{question}\n\n本体子图：\n{subgraph}\n\n"
        "请**只基于上述本体子图**回答考题（不要编造子图之外的实体或事实）。\n"
        "若子图信息不足，enough 置为 false，并给出基于子图的最佳尝试。\n"
        "used 必须使用子图中**确切**的实体名。\n"
        "严格按 JSON 输出：\n"
        '{"answer": "...", "used": ["实体名", ...], "enough": true}\n')


def _gen_prompt_ablated(question: str) -> str:
    """LLM-1 closed-book prompt WITHOUT the subgraph (ablation arm)."""
    return (
        "下面有一道关于某段文本的考题。\n"
        f"考题：{question}\n\n请直接回答，不要编造。\n"
        "严格按 JSON 输出：\n"
        '{"answer": "...", "used": [], "enough": true}\n')


def _judge_prompt(question: str, answer: str, chunk_text: str) -> str:
    """LLM-2 open-book judge: is the answer grounded in the raw text?"""
    return (
        "你是答案判别器：判断下面的答案是否被给定的原文上下文支持。\n\n"
        "原文上下文：\n---\n" + chunk_text + "\n---\n\n"
        f"考题：{question}\n\n待判答案：\n{answer}\n\n"
        "判定规则：\n"
        "- 答案断言能在原文中找到依据 -> supported\n"
        "- 答案与原文矛盾 -> contradicted\n"
        "- 原文无法证实或证伪 -> unknown\n"
        "严格按 JSON 输出：\n"
        '{"verdict": "supported|contradicted|unknown", "reason": "一句中文说明"}\n')


def _closed_set(used: list, cand_names: set[str]) -> list[str]:
    """Gate 1 (语义关): used names must be inside the closed candidate set."""
    out = []
    for u in used or []:
        if isinstance(u, str) and u.strip() in cand_names and u.strip() not in out:
            out.append(u.strip())
    return out


def _answer_mapping(answer: str, cand_names: set[str]) -> list[str]:
    """Gate 2 (映射关): ontology nodes whose exact name literally occurs in the
    answer text. Guards against the judge being fooled by a plausible answer
    that never touches the ontology. Longest names first for specificity."""
    hits = []
    for name in sorted(cand_names, key=lambda s: (len(s), s), reverse=True):
        if len(name) >= 2 and name in answer and name not in hits:
            hits.append(name)
    return hits


_JUDGE_TEXT = {"supported": "答案与原文一致",
               "contradicted": "答案与原文矛盾",
               "unknown": "原文无法证实或证伪"}


def finalize_run(mode: str, gen_reply: dict | None, judge_reply: dict | None,
                 cand_names: set[str]) -> dict:
    """Deterministic adjudication of one arm (with_ontology | ablated).

    Three gates (LLM-2 nomination is NEVER the final word):
      G1 语义关 - `used` must be inside the closed candidate set
      G2 映射关 - (with_ontology) the answer text must map back to >=1
                  ontology node - prevents the judge being "套话" by a
                  plausible answer that ignores the ontology
      G3 判分关 - LLM-2 verdict (supported/contradicted/unknown) converted
                  to pass/fail by this function

    Returns {verdict, enough, used, mapped, judge_verdict, reason, issue}.
    issue ∈ retrieval|ontology|context|unused|none - the fail attribution
    so "本体结构问题" and "检索问题" are tracked as distinct bugs."""
    if not isinstance(gen_reply, dict):
        return {"verdict": "fail", "enough": 1, "used": [], "mapped": [],
                "judge_verdict": "", "reason": "生成器输出不可解析", "issue": "ontology"}
    answer = str(gen_reply.get("answer") or "")
    enough = bool(gen_reply.get("enough", True))
    used = _closed_set(gen_reply.get("used") or [], cand_names)
    mapped = _answer_mapping(answer, cand_names)
    jv = ""
    if isinstance(judge_reply, dict):
        jv = str(judge_reply.get("verdict") or "")
        jv = jv if jv in ("supported", "contradicted", "unknown") else ""
    reason = _JUDGE_TEXT.get(jv, "判别器输出不可解析" if judge_reply else "判别器未返回")

    if mode == "ablated":
        # ablation arm: judge-only - ontology gates do not apply
        verdict = "pass" if jv == "supported" else "fail"
        return {"verdict": verdict, "enough": 1 if enough else 0, "used": [],
                "mapped": [], "judge_verdict": jv, "reason": reason, "issue": "none"}

    # with_ontology arm
    # retrieval / context / unused are NOT the candidates' fault: their mapped
    # lists are emptied so exam_results gets no fail rows for them and the
    # G-07 auto-delete never punishes a candidate for a coverage/judge issue.
    if not enough:
        return {"verdict": "fail", "enough": 0, "used": used, "mapped": [],
                "judge_verdict": jv, "reason": "本体子图不足以回答（检索/覆盖问题）",
                "issue": "retrieval"}
    if jv == "supported":
        if not mapped:
            return {"verdict": "fail", "enough": 1, "used": used, "mapped": [],
                    "judge_verdict": jv,
                    "reason": "答案正确但未引用任何本体实体（本体未起作用）",
                    "issue": "unused"}
        return {"verdict": "pass", "enough": 1, "used": used, "mapped": mapped,
                "judge_verdict": jv, "reason": reason, "issue": "none"}
    if jv == "contradicted":
        return {"verdict": "fail", "enough": 1, "used": used, "mapped": mapped,
                "judge_verdict": jv, "reason": "答案与原文矛盾（本体误导或生成错误）",
                "issue": "ontology"}
    return {"verdict": "fail", "enough": 1, "used": used, "mapped": [],
            "judge_verdict": jv, "reason": "原文无法证实或证伪（上下文不足）",
            "issue": "context"}


def run_exam(conn, job_id: int) -> None:
    """Exam job v2: A/B ablation with a dual-LLM (generator + judge).

    For each quiz:
      arm A (with_ontology): generator (V4-Flash) sees question + subgraph
      arm B (ablated):       generator sees the question only
      both answers are judged by GLM 5.2 (llm2) against the raw chunk text
    Deterministic gates then adjudicate pass/fail (LLM never judges itself).

    Outputs:
      exam_runs    - one row per arm: the A/B evidence for the frontend
      exam_results - candidate-level pass/fail for the with_ontology arm,
                     derived from the *mapped* entities (gate 2), keeping the
                     approval-gate discipline and the badge UI working.

    Fail attribution (issue): retrieval / ontology / context / unused - so
    "本体结构问题" and "检索问题" are tracked as distinct bugs."""
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as futures_wait

    if not llm.llm_configured():
        jobs.finish_job(conn, job_id, ok=False,
                        error="考核需要配置生成模型（V4-Flash）")
        return
    if not llm.llm2_configured():
        jobs.finish_job(conn, job_id, ok=False,
                        error="考核需要配置判别模型（GLM）——异源交叉核验，禁止同源自判")
        return
    rows = conn.execute(
        "SELECT q.id, q.chunk_id, q.question, q.answer, q.evidence, c.text"
        " FROM quiz q JOIN chunks c ON c.id=q.chunk_id"
        " ORDER BY q.chunk_id, q.id").fetchall()
    total = len(rows)
    jrow = conn.execute("SELECT progress_current FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
    start = min(int(jrow["progress_current"] or 0), total) if jrow else 0
    jobs.update_progress(conn, job_id, start, total)
    jobs.emit(conn, job_id, "exam.start",
              {"questions": total, "resumed_from": start} if start
              else {"questions": total})

    # pre-fetch per-question subgraph (scheduler thread = single reader of
    # the shared conn); questions whose chunk produced no candidates are
    # skipped - nothing to grade there
    units: list[tuple[dict, list[dict], set[str], list[dict]]] = []
    skipped = 0
    for r in rows:
        cands = [dict(c) for c in conn.execute(
            "SELECT DISTINCT c.id, c.name, c.kind, c.definition FROM candidates c"
            " JOIN mentions m ON m.candidate_id=c.id"
            " WHERE m.chunk_id=? AND c.status IN ('pending','approved')",
            (r["chunk_id"],)).fetchall()]
        if not cands:
            skipped += 1
            continue
        cids = [c["id"] for c in cands]
        rels = [dict(x) for x in conn.execute(
            "SELECT r.source_id, r.relation_type, r.target_name, c.name AS source_name"
            " FROM relations r LEFT JOIN candidates c ON c.id=r.source_id"
            " WHERE r.chunk_id=? OR r.source_id IN (%s)"
            % ",".join("?" * len(cids)), (r["chunk_id"], *cids)).fetchall()]
        units.append((dict(r), cands, {c["name"] for c in cands}, rels))
    total_eff = len(units)
    pending = deque(range(start, total_eff))  # unit indexes (0-based)
    verdicts = {"pass": 0, "fail": 0}
    issues = {"retrieval": 0, "ontology": 0, "context": 0, "unused": 0}
    llm_failures = 0
    results: dict[int, tuple[tuple | None, bool]] = {}
    inflight: dict = {}
    retries: dict[int, int] = {}
    max_w, ok_streak = CONCURRENCY, 0

    def _step_window(new_w: int, why: str) -> None:
        nonlocal max_w
        new_w = max(1, min(CONCURRENCY, new_w))
        if new_w != max_w:
            max_w = new_w
            jobs.emit(conn, job_id, "exam.concurrency",
                      {"window": max_w, "reason": why})

    pool = ThreadPoolExecutor(max_workers=CONCURRENCY,
                              thread_name_prefix="ont-exam")

    def _worker(i: int):
        q, cands, names, rels = units[i]
        jobs.set_current_job(job_id)
        jobs.set_telemetry_buffer(True)
        try:
            subgraph = _subgraph_text(cands, rels)
            gen_with = llm.extract_json(llm.chat(
                [{"role": "user",
                  "content": _gen_prompt(q["question"], subgraph)}]))
            gen_abl = llm.extract_json(llm.chat(
                [{"role": "user",
                  "content": _gen_prompt_ablated(q["question"])}]))
            jv_with = llm.extract_json(llm.chat2(
                [{"role": "user", "content": _judge_prompt(
                    q["question"],
                    str((gen_with or {}).get("answer") or ""), q["text"])}]))
            jv_abl = llm.extract_json(llm.chat2(
                [{"role": "user", "content": _judge_prompt(
                    q["question"],
                    str((gen_abl or {}).get("answer") or ""), q["text"])}]))
            return (gen_with, gen_abl, jv_with, jv_abl), True
        finally:
            jobs.set_telemetry_buffer(False)

    def _commit(i: int) -> None:
        q, cands, names, rels = units[i]
        payload, _ok = results.pop(i)
        gen_with, gen_abl, jv_with, jv_abl = (payload or (None, None, None, None))
        fw = finalize_run("with_ontology", gen_with, jv_with, names)
        fa = finalize_run("ablated", gen_abl, jv_abl, names)
        now = db.now()
        # idempotent re-grade: replace previous runs for this question
        conn.execute("DELETE FROM exam_runs WHERE quiz_id=?", (q["id"],))
        conn.execute("DELETE FROM exam_results WHERE quiz_id=?", (q["id"],))
        for mode, f, gen in (("with_ontology", fw, gen_with),
                             ("ablated", fa, gen_abl)):
            conn.execute(
                "INSERT INTO exam_runs(quiz_id, job_id, mode, answer, used_names,"
                " verdict, enough, judge_verdict, issue, reason, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (q["id"], job_id, mode,
                 str((gen or {}).get("answer") or ""),
                 json.dumps(f["mapped"], ensure_ascii=False),
                 f["verdict"], f["enough"], f["judge_verdict"],
                 f["issue"], f["reason"], now))
        by_name = {c["name"]: c["id"] for c in cands}
        for nm in fw["mapped"]:
            if nm in by_name:
                conn.execute(
                    "INSERT INTO exam_results(quiz_id, candidate_id, verdict,"
                    " created_at) VALUES(?,?,?,?)",
                    (q["id"], by_name[nm], fw["verdict"], now))
        conn.commit()
        verdicts[fw["verdict"]] += 1
        if fw["issue"] != "none":
            issues[fw["issue"]] += 1

    try:
        head = start
        while head < total_eff:
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
                    if retries[i] <= MAX_RATE_RETRIES:
                        pending.appendleft(i)
                        continue
                    jobs.auto_pause(conn, job_id,
                                    f"考核连续被限流（429）{retries[i]} 次，已暂停：{e}")
                    return
                except llm.LLMError as e:
                    jobs.auto_pause(conn, job_id,
                                    f"LLM 访问失败已自动暂停：{e}。点击恢复可重试。")
                    return
                if not llm_ok:
                    llm_failures += 1
                    _step_window(max_w - 1, "LLM 调用失败")
                    ok_streak = 0
                else:
                    ok_streak += 1
                    if ok_streak >= OK_STREAK and max_w < CONCURRENCY:
                        _step_window(max_w + 1, f"连续 {OK_STREAK} 次成功")
                        ok_streak = 0
                results[i] = (result, llm_ok)
            jobs.drain_telemetry(conn)
            while head in results:
                jobs.poll_control(conn, job_id)
                _commit(head)
                jobs.update_progress(conn, job_id, head + 1)
                head += 1
                if head % 10 == 0 or head == total_eff:
                    jobs.emit(conn, job_id, "exam.progress",
                              {"done": head, "total": total_eff, **verdicts,
                               "issues": issues})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        jobs.drain_telemetry(conn)

    # --- auto-remove hard failures (approval-gate discipline, G-07) ---
    # Deterministic rule: a PENDING candidate that was mapped into a failing
    # answer (fail>0) and never passed a single question (pass==0) is a
    # wrong/misleading extraction -> deleted directly. Mixed signals (fail>0
    # but also pass>0) are contradictory -> kept as 存疑 for the user's final
    # call. Approved candidates are past the gate and never auto-deleted
    # (user approval outranks the exam suggestion).
    fail_rows = conn.execute(
        "SELECT c.id, c.name FROM candidates c"
        " JOIN exam_results er ON er.candidate_id=c.id"
        " WHERE er.verdict='fail' AND c.status='pending'"
        " GROUP BY c.id"
        " HAVING (SELECT COUNT(*) FROM exam_results er2"
        "         WHERE er2.candidate_id=c.id AND er2.verdict='pass')=0"
    ).fetchall()
    auto_deleted = 0
    if fail_rows:
        names = [r["name"] for r in fail_rows]
        auto_deleted = delete_candidates(conn, [r["id"] for r in fail_rows])
        jobs.emit(conn, job_id, "exam.auto_deleted",
                  {"count": auto_deleted, "names": names[:60],
                   "rule": "fail>0 且从未通过（pass==0）的待审候选已直接删除"})

    jobs.emit(conn, job_id, "exam.done",
              {"graded": verdicts, "issues": issues,
               "skipped_no_candidates": skipped,
               "auto_deleted": auto_deleted,
               "llm_failures": llm_failures, "total": total_eff})
    jobs.finish_job(conn, job_id, ok=True)


def exam_stats(conn) -> dict:
    """Aggregates for the frontend: quiz count + verdict totals + A/B ablation.

    `pass/fail/missing` keep the candidate-level semantics (exam_results,
    with_ontology arm) so the existing badge UI keeps working; `ablation`
    carries the A/B comparison (with_ontology vs ablated) + fail attribution.
    """
    quiz_total = conn.execute("SELECT COUNT(*) c FROM quiz").fetchone()["c"]
    by_v = conn.execute(
        "SELECT verdict, COUNT(*) c FROM exam_results GROUP BY verdict").fetchall()
    verdicts = {r["verdict"]: r["c"] for r in by_v}
    runs = {}
    for r in conn.execute(
            "SELECT mode, verdict, COUNT(*) c FROM exam_runs"
            " GROUP BY mode, verdict"):
        runs.setdefault(r["mode"], {})[r["verdict"]] = r["c"]
    with_ = runs.get("with_ontology", {})
    abl = runs.get("ablated", {})
    with_pass, with_fail = with_.get("pass", 0), with_.get("fail", 0)
    abl_pass, abl_fail = abl.get("pass", 0), abl.get("fail", 0)
    issues = {r["issue"]: r["c"] for r in conn.execute(
        "SELECT issue, COUNT(*) c FROM exam_runs"
        " WHERE mode='with_ontology' AND issue!='none' GROUP BY issue")}
    return {
        "quiz_total": quiz_total, "pass": verdicts.get("pass", 0),
        "fail": verdicts.get("fail", 0), "missing": verdicts.get("missing", 0),
        "ablation": {
            "with_ontology_pass": with_pass, "with_ontology_fail": with_fail,
            "ablated_pass": abl_pass, "ablated_fail": abl_fail,
            "gain": with_pass - abl_pass,   # 本体边际价值：+为正增益，-为有害注入
            "issues": issues,
        },
    }


def _exam_by_candidate(conn) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT candidate_id, verdict, COUNT(*) c FROM exam_results"
        " GROUP BY candidate_id, verdict").fetchall()
    out: dict[int, dict] = {}
    for r in rows:
        d = out.setdefault(r["candidate_id"], {"pass": 0, "fail": 0, "missing": 0})
        d[r["verdict"]] = r["c"]
    return out


# ---------------- approval ----------------

def set_status(conn, candidate_id: int, status: str) -> bool:
    if status not in ("approved", "rejected", "pending"):
        return False
    cur = conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, candidate_id))
    conn.commit()
    return cur.rowcount > 0


def merge_candidate(conn, candidate_id: int, into_id: int) -> bool:
    """Merge cand into an approved target: mentions move over, source -> merged."""
    src = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    dst = conn.execute("SELECT * FROM candidates WHERE id=?", (into_id,)).fetchone()
    if not src or not dst or src["id"] == dst["id"]:
        return False
    conn.execute("UPDATE mentions SET candidate_id=? WHERE candidate_id=?",
                 (into_id, candidate_id))
    conn.execute("UPDATE candidates SET status='merged', merged_into=? WHERE id=?",
                 (into_id, candidate_id))
    # point relations at the surviving entity
    conn.execute("UPDATE relations SET source_id=? WHERE source_id=?",
                 (into_id, candidate_id))
    conn.commit()
    return True


def delete_candidates(conn, ids: list[int]) -> int:
    """Hard-delete candidates + their evidence (mentions) + outgoing relations.

    Batch approval-housekeeping: user multi-selects useless nominations in the
    ontology page and removes them in one call. Relations that only NAME a
    deleted candidate as target become dangling (ghost) instead of being
    silently removed - evidence trails are never destroyed in the background.
    Returns the number actually deleted (unknown ids ignored)."""
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id FROM candidates WHERE id IN ({marks})", ids).fetchall()
    found = [r["id"] for r in rows]
    if not found:
        return 0
    qmarks = ",".join("?" for _ in found)
    conn.execute(f"DELETE FROM mentions WHERE candidate_id IN ({qmarks})", found)
    conn.execute(f"DELETE FROM relations WHERE source_id IN ({qmarks})", found)
    conn.execute(f"DELETE FROM candidates WHERE id IN ({qmarks})", found)
    conn.commit()
    return len(found)


# ---------------- graph ----------------

def graph(conn, include_status: list[str] | None = None) -> dict:
    """Nodes + edges for the frontend SVG graph.
    Default view: approved + pending (rejected/merged hidden)."""
    statuses = include_status or ["approved", "pending"]
    marks = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT id, kind, name, definition, status, merged_into FROM candidates"
        f" WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()
    exam = _exam_by_candidate(conn)
    nodes = []
    id_set = {r["id"] for r in rows}
    for r in rows:
        mention_count = conn.execute(
            "SELECT COUNT(*) c FROM mentions WHERE candidate_id=?", (r["id"],)).fetchone()["c"]
        nodes.append({"id": r["id"], "kind": r["kind"], "name": r["name"],
                      "definition": r["definition"], "status": r["status"],
                      "merged_into": r["merged_into"], "mentions": mention_count,
                      "exam": exam.get(r["id"])})
    edges = []
    for r in rows:
        rels = conn.execute(
            "SELECT id, target_name, relation_type FROM relations WHERE source_id=?",
            (r["id"],)).fetchall()
        for rel in rels:
            tgt_row = conn.execute(
                "SELECT id, status FROM candidates WHERE kind='entity' AND name=?",
                (rel["target_name"],)).fetchone()
            tgt_id = tgt_row["id"] if tgt_row else None
            edges.append({"id": rel["id"], "source": r["id"], "target": tgt_id,
                          "target_name": rel["target_name"],
                          "relation_type": rel["relation_type"],
                          "dangling": tgt_id not in id_set if tgt_id else True})
    return {"nodes": nodes, "edges": edges, "exam": exam_stats(conn)}


def candidate_detail(conn, candidate_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        return None
    mentions = conn.execute(
        "SELECT m.id, m.chunk_id, m.span_start, m.span_end, m.text, c.doc_id, c.seq"
        " FROM mentions m JOIN chunks c ON c.id=m.chunk_id WHERE m.candidate_id=?"
        " ORDER BY m.chunk_id", (candidate_id,)).fetchall()
    relations = conn.execute(
        "SELECT id, target_name, relation_type, chunk_id FROM relations"
        " WHERE source_id=?", (candidate_id,)).fetchall()
    dupes = conn.execute(
        "SELECT id, name FROM candidates WHERE kind=? AND name=? AND id<>?",
        (row["kind"], row["name"], candidate_id)).fetchall()
    exam = conn.execute(
        "SELECT e.verdict, e.quiz_id, q.question, q.answer, q.evidence"
        " FROM exam_results e JOIN quiz q ON q.id=e.quiz_id"
        " WHERE e.candidate_id=? ORDER BY e.id DESC", (candidate_id,)).fetchall()
    return {"candidate": dict(row), "mentions": [dict(m) for m in mentions],
            "relations": [dict(r) for r in relations],
            "similar": [dict(d) for d in dupes],
            "exam": [dict(x) for x in exam]}


def approve_all_stats(conn) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) c FROM candidates GROUP BY status").fetchall()
    return {r["status"]: r["c"] for r in rows}
