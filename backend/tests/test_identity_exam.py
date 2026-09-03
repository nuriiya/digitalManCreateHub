# -*- coding: utf-8 -*-
"""Identity pre-screening (anchors) + quiz/exam phase tests (fake LLM)."""
import json
import time

from app import jobs, ingest, ontology, identity, db

NOMINATION = json.dumps({
    "identities": [
        {"name": "财务制度顾问", "mission": "解答报销与预算制度问题",
         "description": "面向公司财务制度", "keywords": ["报销", "预算"],
         "anchors": [
             {"name": "报销系统", "type": "系统", "definition": "公司财务流程核心系统"},
             {"name": "", "type": "概念", "definition": "空名应被结构关拒绝"},
             {"name": "审批流程", "type": "神秘类别", "definition": "类型越界转其他"},
         ]},
        {"name": "财务制度顾问", "mission": "重名应被去重丢弃"},
    ]}, ensure_ascii=False)

QUIZ_EXTRACT = json.dumps({
    "entities": [{"name": "报销系统", "type": "系统", "definition": "财务核心",
                  "mentions": ["报销系统"]}],
    "relations": [],
    "quiz": [
        {"q": "报销单提交后进入什么？", "a": "审批流程", "evidence": "审批流程"},
        {"q": "伪造问题", "a": "假答案", "evidence": "原文里根本没有这段话"},
    ]}, ensure_ascii=False)


def _ingest(env, workdir):
    jid = jobs.create_job(env, "ingest", 0, str(workdir))
    ingest.ingest_workdir(env, jid, str(workdir))


def _wait_exam_done(env, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            row = env.execute(
                "SELECT status FROM jobs WHERE kind='exam'"
                " ORDER BY id DESC").fetchone()
        except Exception:
            row = None
        if row and row["status"] in ("done", "failed"):
            return row["status"]
        time.sleep(0.05)
    raise AssertionError("auto exam job did not finish in time")


# ---------------- step 1: deterministic stats ----------------

def test_high_freq_words_deterministic(env, workdir, fake_llm):
    _ingest(env, workdir)
    words = identity.high_freq_words(env)
    assert words
    names = [w for w, _ in words]
    assert "报销" in names  # occurs in both 报销系统 and 报销单
    assert all(c >= 1 for _, c in words)
    tags = identity.tag_stats(env)
    assert tags  # ingest stored tags for the chunk


# ---------------- step 2: nomination ----------------

def test_nomination_stores_pending_identities(env, workdir, fake_llm):
    _ingest(env, workdir)
    fake_llm["reply"] = lambda messages: NOMINATION
    jid = jobs.create_job(env, "identity", 1, "nomination")
    identity.run_nomination(env, jid)
    idents = identity.list_identities(env)
    assert len(idents) == 1  # duplicate name deduped
    assert idents[0]["name"] == "财务制度顾问"
    assert idents[0]["status"] == "pending"
    assert idents[0]["keywords"] == ["报销", "预算"]
    by_name = {a["name"]: a for a in idents[0]["anchors"]}
    assert set(by_name) == {"报销系统", "审批流程"}  # empty name dropped
    assert by_name["审批流程"]["type"] == "其他"  # closed-set violation -> 其他
    assert all(a["status"] == "pending" for a in idents[0]["anchors"])


def test_nomination_without_llm_fails_loudly(env, workdir, fake_llm):
    _ingest(env, workdir)
    from app import llm, settings_store
    settings_store.save_settings({"llm": {"base_url": "", "api_key": ""}})
    llm.clear_fake_chat()
    jid = jobs.create_job(env, "identity", 1, "nomination")
    identity.run_nomination(env, jid)
    row = env.execute("SELECT status, error FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed" and "LLM" in row["error"]


# ---------------- step 3/4: approval + anchor injection ----------------

def test_anchor_injection_guides_extraction(env, workdir, fake_llm):
    _ingest(env, workdir)
    fake_llm["reply"] = lambda messages: NOMINATION
    jid = jobs.create_job(env, "identity", 1, "nomination")
    identity.run_nomination(env, jid)
    iid = env.execute("SELECT id FROM identities").fetchone()["id"]
    aid = env.execute("SELECT id FROM anchors WHERE name='报销系统'").fetchone()["id"]
    assert identity.set_identity_status(env, iid, "approved")
    assert identity.set_anchor_status(env, aid, "approved")

    prompts = []
    def _ext(prompt):
        prompts.append(prompt)
        return '{"entities": [], "relations": [], "quiz": []}'
    fake_llm["extraction"] = _ext
    ojid = jobs.create_job(env, "ontology", 0, "EDC")
    ontology.run_extraction(env, ojid)

    assert prompts
    assert "报销系统" in prompts[0]
    assert "锚点" in prompts[0] and "不是白名单" in prompts[0]  # guidance, not whitelist

    # rejected anchors / rejected identity are NOT injected
    assert identity.set_anchor_status(env, aid, "rejected")
    assert identity.anchor_block(env) == ""


def test_anchor_crud_edit_and_add(env, workdir, fake_llm):
    _ingest(env, workdir)
    cur = env.execute(
        "INSERT INTO identities(name, mission, description, keywords, status,"
        " created_at) VALUES('测试身份', '', '', '[]', 'approved', 0)")
    env.commit()
    iid = cur.lastrowid
    aid = identity.add_anchor(env, iid, {"name": "新锚点", "type": "概念",
                                         "definition": "手动添加"})
    assert aid
    assert identity.update_anchor(env, aid, {"definition": "改过的定义"})
    row = env.execute("SELECT name, definition FROM anchors WHERE id=?",
                      (aid,)).fetchone()
    assert row["name"] == "新锚点" and row["definition"] == "改过的定义"
    assert not identity.update_anchor(env, aid, {"type": "非法类型"})  # closed set
    assert identity.add_anchor(env, 999, {"name": "x"}) is None  # no such identity


# ---------------- quiz generation (same extraction call) ----------------

def test_quiz_evidence_gate_and_auto_exam(env, workdir, fake_llm):
    _ingest(env, workdir)
    fake_llm["extraction"] = lambda prompt: QUIZ_EXTRACT
    # generator (LLM-1) answers closed-book; the answer text must map back to
    # the ontology node "报销系统" (gate 2); judge (LLM-2) defaults to supported
    fake_llm["reply"] = lambda messages: json.dumps(
        {"answer": "报销系统进入审批流程", "used": ["报销系统"], "enough": True},
        ensure_ascii=False)
    ojid = jobs.create_job(env, "ontology", 0, "EDC")
    ontology.run_extraction(env, ojid)

    quiz = env.execute("SELECT * FROM quiz").fetchall()
    assert len(quiz) == 1  # fabricated-evidence question dropped whole
    assert quiz[0]["answer"] == "审批流程"

    # auto exam job was created after extraction and finished
    assert _wait_exam_done(env) == "done"
    stats = ontology.exam_stats(env)
    assert stats["quiz_total"] == 1
    assert stats["pass"] == 1 and stats["fail"] == 0
    # A/B ablation recorded: both arms pass here (judge default supported)
    assert stats["ablation"]["with_ontology_pass"] == 1
    assert stats["ablation"]["ablated_pass"] == 1

    # graph payload carries per-candidate exam aggregates
    g = ontology.graph(env)
    node = [n for n in g["nodes"] if n["name"] == "报销系统"][0]
    assert node["exam"] == {"pass": 1, "fail": 0, "missing": 0}
    assert g["exam"]["quiz_total"] == 1

    # candidate detail lists the graded question
    d = ontology.candidate_detail(env, node["id"])
    assert d["exam"] and d["exam"][0]["verdict"] == "pass"
    assert d["exam"][0]["question"] == "报销单提交后进入什么？"


def test_no_auto_exam_without_quiz(env, workdir, fake_llm):
    _ingest(env, workdir)
    fake_llm["extraction"] = lambda prompt: '{"entities": [], "relations": []}'
    ojid = jobs.create_job(env, "ontology", 0, "EDC")
    ontology.run_extraction(env, ojid)
    assert env.execute(
        "SELECT COUNT(*) c FROM jobs WHERE kind='exam'").fetchone()["c"] == 0


# ---------------- deterministic adjudication (finalize_run) ----------------

def test_finalize_run_matrix(env, fake_llm):
    """三关判分矩阵：LLM-2 判别只是提名，finalize_run 确定性终审。"""
    names = {"报销系统", "审批流程"}
    # pass: enough + judge supported + answer maps back to >=1 ontology node
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "报销系统进入审批流程", "used": ["报销系统"], "enough": True},
        {"verdict": "supported", "reason": "原文一致"}, names)
    assert f["verdict"] == "pass" and f["issue"] == "none"
    assert set(f["mapped"]) == {"审批流程", "报销系统"}  # answer-text mapping
    # gate 1: used names outside the closed set are dropped
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "报销系统", "used": ["报销系统", "幻觉候选"], "enough": True},
        {"verdict": "supported"}, names)
    assert f["used"] == ["报销系统"] and f["verdict"] == "pass"
    # retrieval: subgraph insufficient -> fail, candidate NOT punished (mapped=[])
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "报销系统", "used": ["报销系统"], "enough": False},
        {"verdict": "supported"}, names)
    assert f["verdict"] == "fail" and f["issue"] == "retrieval"
    assert f["mapped"] == []
    # ontology: judge contradicts -> fail, mapped candidates ARE punished
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "报销系统", "used": ["报销系统"], "enough": True},
        {"verdict": "contradicted"}, names)
    assert f["verdict"] == "fail" and f["issue"] == "ontology"
    assert f["mapped"] == ["报销系统"]
    # context: judge unknown -> fail, candidates NOT punished
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "报销系统", "used": ["报销系统"], "enough": True},
        {"verdict": "unknown"}, names)
    assert f["verdict"] == "fail" and f["issue"] == "context"
    assert f["mapped"] == []
    # unused: correct answer but never touches the ontology -> fail
    f = ontology.finalize_run(
        "with_ontology",
        {"answer": "完全正确的答案", "used": [], "enough": True},
        {"verdict": "supported"}, names)
    assert f["verdict"] == "fail" and f["issue"] == "unused"
    # ablation arm: judge-only, no ontology gates
    f = ontology.finalize_run(
        "ablated", {"answer": "报销系统", "used": [], "enough": True},
        {"verdict": "supported"}, names)
    assert f["verdict"] == "pass"
    f = ontology.finalize_run(
        "ablated", {"answer": "报销系统", "used": [], "enough": True},
        {"verdict": "contradicted"}, names)
    assert f["verdict"] == "fail"
    # unparseable generator output -> fail
    assert ontology.finalize_run(
        "with_ontology", None, {"verdict": "supported"}, names)["verdict"] == "fail"


def test_run_exam_ablation_and_idempotent(env, workdir, fake_llm):
    _ingest(env, workdir)
    chunk = env.execute("SELECT id, text FROM chunks LIMIT 1").fetchone()
    cur = env.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', '报销系统', '财务核心', 'pending', 0)")
    cid = cur.lastrowid
    env.execute(
        "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end, text)"
        " VALUES(?,?,?,?,?)", (cid, chunk["id"], 0, 4, "报销系统"))
    env.execute(
        "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
        " VALUES(?,?,?,?,0)", (chunk["id"], "报销单提交后进入什么？",
                               "审批流程", "审批流程"))
    env.commit()

    # round 1: subgraph insufficient (enough=false) -> with_ontology fail
    # (retrieval), candidate NOT punished (mapped emptied); ablated arm judged
    # supported by GLM -> ablated pass. A/B evidence recorded in exam_runs.
    fake_llm["reply"] = lambda messages: json.dumps(
        {"answer": "报销系统", "used": ["报销系统"], "enough": False},
        ensure_ascii=False)
    jid = jobs.create_job(env, "exam", 1, "grading")
    ontology.run_exam(env, jid)
    stats = ontology.exam_stats(env)
    assert stats["pass"] == 0 and stats["fail"] == 0  # no candidate punished
    assert stats["ablation"]["with_ontology_pass"] == 0
    assert stats["ablation"]["ablated_pass"] == 1
    assert stats["ablation"]["gain"] == -1
    runs = env.execute(
        "SELECT mode, verdict FROM exam_runs WHERE quiz_id=?"
        " ORDER BY mode", (env.execute("SELECT id FROM quiz LIMIT 1").fetchone()["id"],)).fetchall()
    assert [(r["mode"], r["verdict"]) for r in runs] == [
        ("ablated", "pass"), ("with_ontology", "fail")]
    assert env.execute("SELECT COUNT(*) c FROM exam_results").fetchone()["c"] == 0

    # round 2: good answer -> pass; idempotent re-grade REPLACES old rows
    fake_llm["reply"] = lambda messages: json.dumps(
        {"answer": "报销系统", "used": ["报销系统"], "enough": True},
        ensure_ascii=False)
    jid2 = jobs.create_job(env, "exam", 1, "grading")
    ontology.run_exam(env, jid2)
    stats = ontology.exam_stats(env)
    assert stats["pass"] == 1 and stats["fail"] == 0
    assert stats["ablation"]["with_ontology_pass"] == 1
    rows = env.execute("SELECT verdict FROM exam_results").fetchall()
    assert len(rows) == 1  # old verdict replaced, not duplicated
    assert env.execute("SELECT COUNT(*) c FROM exam_runs").fetchone()["c"] == 2


def test_exam_requires_glm_judge(env, workdir, fake_llm):
    """判别器未配置时考核必须报错（禁止同源自判），而不是静默降级。"""
    _ingest(env, workdir)
    chunk = env.execute("SELECT id, text FROM chunks LIMIT 1").fetchone()
    cur = env.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', '报销系统', '财务核心', 'pending', 0)")
    env.execute(
        "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end, text)"
        " VALUES(?,?,?,?,?)", (cur.lastrowid, chunk["id"], 0, 4, "报销系统"))
    env.execute(
        "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
        " VALUES(?,?,?,?,0)", (chunk["id"], "报销单提交后进入什么？",
                               "审批流程", "审批流程"))
    env.commit()
    # strip llm2 -> run_exam must fail loudly
    from app import settings_store
    settings_store.save_settings({"llm2": {"api_key": "", "base_url": ""}})
    jid = jobs.create_job(env, "exam", 1, "grading")
    ontology.run_exam(env, jid)
    row = env.execute("SELECT status, error FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "failed"
    assert "判别模型" in (row["error"] or "")


def test_extraction_cap_refined_20(env, fake_llm):
    """提取上限=精炼提取：entities/relations 各最多 20（代码确定性截断兜底）。"""
    text = "《报销系统》是公司财务流程的核心系统。员工提交报销单后进入审批流程。"
    many = {
        "entities": [{"name": f"实体{i:02d}", "type": "概念", "definition": "d",
                      "mentions": ["报销系统"]} for i in range(30)],
        "relations": [{"source": f"实体{i:02d}", "target": f"实体{(i + 1) % 20:02d}",
                       "type": "依赖"} for i in range(25)],
        "quiz": [],
    }
    prompts = []
    fake_llm["extraction"] = lambda prompt: (prompts.append(prompt) or
                                             json.dumps(many, ensure_ascii=False))
    result, ok = ontology.extract_chunk(text, "")
    assert ok
    assert len(result["entities"]) == 20 and len(result["relations"]) == 20
    assert "精炼提取" in prompts[0] and "不超过 20" in prompts[0]
    assert "宁多勿漏" not in prompts[0]


def test_exam_auto_deletes_fail_only_pending(env, workdir, fake_llm):
    """考核后：fail 且从未通过的待审候选直接删除；已批准候选与未被用到的保留。"""
    _ingest(env, workdir)
    chunk = env.execute("SELECT id, text FROM chunks LIMIT 1").fetchone()

    def _mk(name, status="pending"):
        cur = env.execute(
            "INSERT INTO candidates(kind, name, definition, status, created_at)"
            " VALUES('entity',?,?,?,0)", (name, "def", status))
        env.execute(
            "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end, text)"
            " VALUES(?,?,?,?,?)", (cur.lastrowid, chunk["id"], 0, len(name), name))
        return cur.lastrowid

    _mk("坏候选")                       # mapped into wrong answer -> fail -> pending -> deleted
    _mk("好候选")                       # unused -> stays
    _mk("已批准候选", status="approved")  # fail but approved -> user outranks exam -> stays

    env.execute(
        "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
        " VALUES(?,?,?,?,0)", (chunk["id"], "核心系统是什么？", "报销系统", "报销系统"))
    env.commit()
    # generator (LLM-1) maps both candidates into a wrong answer; judge (LLM-2,
    # GLM) contradicts it -> fail(ontology) -> both get exam_results fail rows
    fake_llm["reply"] = lambda messages: json.dumps(
        {"answer": "坏候选和已批准候选", "used": ["坏候选", "已批准候选"],
         "enough": True}, ensure_ascii=False)
    fake_llm["judge"] = lambda messages: json.dumps(
        {"verdict": "contradicted", "reason": "与原文矛盾"}, ensure_ascii=False)
    jid = jobs.create_job(env, "exam", 1, "grading")
    ontology.run_exam(env, jid)

    remaining = {r["name"] for r in env.execute("SELECT name FROM candidates").fetchall()}
    assert "坏候选" not in remaining  # hard failure -> auto-deleted
    assert "好候选" in remaining and "已批准候选" in remaining
    assert env.execute(
        "SELECT COUNT(*) c FROM events WHERE type='exam.auto_deleted'"
    ).fetchone()["c"] == 1


def test_choose_identity_deletes_others(env):
    """选择数字人模板：批准指定身份并级联删除其余身份。"""
    now = time.time()
    cur = env.execute(
        "INSERT INTO identities(name, mission, status, created_at)"
        " VALUES(?,?,?,?)", ("A", "ma", "pending", now))
    a_id = cur.lastrowid
    cur = env.execute(
        "INSERT INTO identities(name, mission, status, created_at)"
        " VALUES(?,?,?,?)", ("B", "mb", "pending", now))
    b_id = cur.lastrowid
    env.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
        " VALUES(?,?,?,?,?,?)", (b_id, "b-anchor", "概念", "d", "pending", now))
    env.commit()

    r = identity.choose_identity(env, a_id)
    assert r == {"approved_id": a_id, "deleted": 1}
    assert env.execute(
        "SELECT status FROM identities WHERE id=?", (a_id,)).fetchone()["status"] == "approved"
    assert env.execute("SELECT COUNT(*) c FROM identities").fetchone()["c"] == 1
    assert env.execute("SELECT COUNT(*) c FROM anchors").fetchone()["c"] == 0

    # unknown id returns None
    assert identity.choose_identity(env, 99999) is None


def test_delete_identity_cascades_anchors(env):
    """identity.delete_identity: 拒绝=物理删除，连带删除锚点。"""
    from app import identity
    now = db.now()
    cur = env.execute(
        "INSERT INTO identities(name, mission, description, keywords, status, created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("视觉接地专家", "m", "d", "[]", "pending", now))
    id1 = cur.lastrowid
    cur = env.execute(
        "INSERT INTO identities(name, mission, description, keywords, status, created_at)"
        " VALUES(?,?,?,?,?,?)",
        ("语义分割专家", "m", "d", "[]", "pending", now))
    id2 = cur.lastrowid
    env.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
        " VALUES(?,?,?,?,?,?)", (id1, "BERT", "概念", "d", "approved", now))
    env.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
        " VALUES(?,?,?,?,?,?)", (id2, "掩码", "概念", "d", "approved", now))
    env.commit()

    # 删除存在的 id，连锚点 cascade
    assert identity.delete_identity(env, id1) is True
    assert env.execute("SELECT COUNT(*) c FROM identities").fetchone()["c"] == 1
    assert env.execute("SELECT COUNT(*) c FROM anchors").fetchone()["c"] == 1  # 仅剩 id2 的
    remaining = env.execute("SELECT id FROM identities").fetchone()
    assert remaining["id"] == id2

    # 不存在的 id 返回 False
    assert identity.delete_identity(env, 99999) is False


def test_exam_mixed_signals_kept_as_doubt(env, workdir, fake_llm):
    """矛盾信号（既通过又不及格）的候选不自动删除，保留为存疑待用户终审。"""
    _ingest(env, workdir)
    chunk = env.execute("SELECT id, text FROM chunks LIMIT 1").fetchone()
    cur = env.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity','报销系统','财务核心','pending',0)")
    cid = cur.lastrowid
    env.execute(
        "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end, text)"
        " VALUES(?,?,?,?,?)", (cid, chunk["id"], 0, 4, "报销系统"))
    for q, a in (("报销单提交后进入什么？", "审批流程"),
                 ("预算超支时谁复核？", "财务专员")):
        env.execute(
            "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
            " VALUES(?,?,?,?,0)", (chunk["id"], q, a, a))
    env.commit()

    def _reply(messages):
        content = messages[-1]["content"]
        good = "报销单" in content  # question about 报销单 answered right
        return json.dumps({"answer": ("报销系统进入审批流程" if good
                                      else "报销系统胡说八道"),
                           "used": ["报销系统"], "enough": True},
                          ensure_ascii=False)

    def _judge(messages):
        content = messages[-1]["content"]
        # judge by the answer text only（原文上下文含"进入审批流程"，不能用全串）
        answer_part = content.split("待判答案：")[-1]
        return json.dumps(
            {"verdict": "supported" if "进入审批流程" in answer_part else "contradicted",
             "reason": "x"}, ensure_ascii=False)

    fake_llm["reply"] = _reply
    fake_llm["judge"] = _judge
    jid = jobs.create_job(env, "exam", 2, "grading")
    ontology.run_exam(env, jid)

    assert env.execute("SELECT COUNT(*) c FROM candidates").fetchone()["c"] == 1
    stats = ontology.exam_stats(env)
    assert stats["quiz_total"] == 2 and stats["pass"] == 1 and stats["fail"] == 1
    assert stats["missing"] == 0
    assert stats["ablation"]["with_ontology_pass"] == 1
    assert stats["ablation"]["with_ontology_fail"] == 1
    assert env.execute(
        "SELECT COUNT(*) c FROM events WHERE type='exam.auto_deleted'"
    ).fetchone()["c"] == 0
