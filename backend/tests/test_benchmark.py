# -*- coding: utf-8 -*-
"""Benchmark tests: 四组对照 + GLM 判卷提名/代码终审 + 错误归因提名闭集校验 +
用户终审 merge / 版本回滚。LLM 全程只提名（fake 路由），确定性代码裁决。"""
import json


def _seed_persona(conn, entities=None):
    """identity + persona_ontology(带 source_candidate_id) + chunk/mention/quiz."""
    now = 0
    iid = conn.execute(
        "INSERT INTO identities(name, mission, description, keywords, status,"
        " created_at) VALUES('视觉定位算法研究员', '研究视觉定位', '', '[]',"
        " 'approved', ?)", (now,)).lastrowid
    conn.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status,"
        " created_at) VALUES(?, '多模态理解', '概念', '核心锚点', 'approved', ?)",
        (iid, now))
    doc = conn.execute(
        "INSERT INTO documents(name, path, content_hash, created_at)"
        " VALUES('doc', 'doc', 'h', ?)", (now,)).lastrowid
    chunk = conn.execute(
        "INSERT INTO chunks(doc_id, seq, text, summary, tags, content_hash)"
        " VALUES(?, 1, '视觉定位指根据自然语言描述在图像中定位目标区域。',"
        " '视觉定位摘要', '[]', 'ch')", (doc,)).lastrowid
    po_ids = {}
    for name, definition in (entities or [("视觉定位", "根据语言描述定位图中目标"),
                                          ("RefCOCO", "指代理解数据集")]):
        cand = conn.execute(
            "INSERT INTO candidates(kind, name, definition, status, created_at)"
            " VALUES('entity', ?, ?, 'approved', ?)",
            (name, definition, now)).lastrowid
        conn.execute(
            "INSERT INTO mentions(candidate_id, chunk_id, span_start, span_end,"
            " text) VALUES(?, ?, 0, 4, ?)", (cand, chunk, name))
        po = conn.execute(
            "INSERT INTO persona_ontology(identity_id, kind, name, definition,"
            " source_candidate_id, status, created_at)"
            " VALUES(?, 'entity', ?, ?, ?, 'active', ?)",
            (iid, name, definition, cand, now)).lastrowid
        po_ids[name] = po
    quiz = conn.execute(
        "INSERT INTO quiz(chunk_id, question, answer, evidence, created_at)"
        " VALUES(?, '什么是视觉定位？', '根据语言描述定位图中目标区域',"
        " '视觉定位指根据自然语言描述在图像中定位目标区域。', ?)",
        (chunk, now)).lastrowid
    conn.commit()
    return {"identity_id": iid, "chunk": chunk, "quiz": quiz, "po": po_ids}


# ---------------- deterministic verdict gates ----------------

def test_deterministic_verdict():
    from app.benchmark import _deterministic_verdict
    assert _deterministic_verdict("我不知道", "correct") == "refused"   # code overrides judge
    assert _deterministic_verdict("我的本体里没有这方面内容", "wrong") == "refused"
    assert _deterministic_verdict("正确回答", "correct") == "correct"    # closed set pass
    assert _deterministic_verdict("正确回答", "bogus") == "wrong"        # outside closed set
    assert _deterministic_verdict("", "correct") == "wrong"              # empty reply


def test_stats_and_conclusion():
    from app.benchmark import _stats, _conclusion
    results = []
    for arm in ("none", "ontology", "rag", "rag_ontology"):
        results.append({"quiz_id": 1, "arm": arm, "verdict": "correct"})
        results.append({"quiz_id": 2, "arm": arm, "verdict": "wrong"})
    stats = _stats(results)
    a = stats["arms"]
    assert a["none"]["accuracy"] == 50.0 and a["none"]["hallucination"] == 50.0
    assert stats["margins"]["total_pp"] == 0.0
    c = _conclusion(stats)
    assert "2 题" in c and "总增益 +0.0pp" in c and "G0 裸模型" in c
    assert "幻觉" in c and "准确率" in c   # conclusion must answer both asks

    # a clear win for rag / rag_ontology over the bare model
    results = ([{"quiz_id": 1, "arm": "none", "verdict": "wrong"},
                {"quiz_id": 1, "arm": "ontology", "verdict": "partial"},
                {"quiz_id": 1, "arm": "rag", "verdict": "correct"},
                {"quiz_id": 1, "arm": "rag_ontology", "verdict": "correct"}])
    stats = _stats(results)
    assert stats["margins"]["ontology_pp"] == 0.0    # G3 - G2 (both correct)
    assert stats["margins"]["rag_pp"] == 100.0       # G3 - G1 (ontology arm missed)
    assert stats["margins"]["total_pp"] == 100.0     # G3 - G0
    # bare model hallucinated on the single question; constrained arms did not
    c2 = _conclusion(stats)
    assert "幻觉 G0 裸模型 100.0%" in c2 and "（−100.0pp）" in c2


# ---------------- nomination closed-set validation ----------------

def test_validate_nominations():
    from app.benchmark import _validate_nominations
    allowed = {"视觉定位": 11, "RefCOCO": 22}
    active_names = set(allowed)
    issues = [
        {"name": "视觉定位", "action": "update", "reason": "定义有误",
         "suggested_definition": "修正后的定义"},          # ok
        {"name": "不存在本体", "action": "delete", "reason": "x"},   # unknown -> drop
        {"name": "RefCOCO", "action": "bogus", "reason": "x"},       # bad action -> drop
        {"name": "RefCOCO", "action": "update", "reason": "无修正定义"},  # no def -> annotate
        {"name": "视觉定位", "action": "delete", "reason": "矛盾"},   # dup, delete wins
        {"name": "RefCOCO", "action": "annotate", "reason": ""},     # no reason -> drop
        # add: a genuinely missing concept (must be new + carry a definition)
        {"name": "混杂偏差", "action": "add", "reason": "缺少该概念",
         "suggested_definition": "选择偏差的一种"},          # ok -> add
        {"name": "视觉定位", "action": "add", "reason": "已存在",  # dup active name -> drop
         "suggested_definition": "x"},
        {"name": "另一概念", "action": "add", "reason": "无定义"},  # no def -> drop
    ]
    out = _validate_nominations(issues, allowed, active_names)
    by_name = {o["name"]: o for o in out}
    assert set(by_name) == {"视觉定位", "RefCOCO", "混杂偏差"}
    assert by_name["视觉定位"]["action"] == "delete"          # priority: delete > update
    assert by_name["视觉定位"]["ontology_id"] == 11
    assert by_name["RefCOCO"]["action"] == "annotate"        # downgraded from update
    assert by_name["RefCOCO"]["note"]                        # reason doubles as note
    assert by_name["混杂偏差"]["action"] == "add"            # new concept survives
    assert by_name["混杂偏差"]["ontology_id"] is None        # add has no existing row
    assert by_name["混杂偏差"]["suggested_definition"] == "选择偏差的一种"
    assert _validate_nominations(None, allowed, active_names) == []
    assert _validate_nominations("garbage", allowed, active_names) == []


def test_select_quizzes_only_persona_linked(env):
    from app.benchmark import _select_quizzes
    seeded = _seed_persona(env)
    rows = _select_quizzes(env, seeded["identity_id"], 10)
    assert len(rows) == 1 and rows[0]["quiz_id"] == seeded["quiz"]
    # an unrelated identity sees nothing
    other = env.execute(
        "INSERT INTO identities(name, mission, keywords, status, created_at)"
        " VALUES('无关', '', '[]', 'approved', 0)").lastrowid
    assert _select_quizzes(env, other, 10) == []


# ---------------- end-to-end run (fake LLM router) ----------------

def test_run_benchmark_end_to_end(env, fake_llm):
    from app import benchmark, jobs

    seeded = _seed_persona(env)
    iid = seeded["identity_id"]

    def _ollama(messages):
        system = messages[0]["content"]
        if "本体约束" in system:
            return "视觉定位是语音识别技术的一种。"       # ontology arms hallucinate
        if "参考资料" in system:
            return "视觉定位是语音识别技术。"             # rag arm hallucinates
        return "我不知道，我没有这方面知识。"              # bare model refuses

    def _judge(messages):
        p = messages[-1]["content"]
        if "本体质量分析官" in p:
            return json.dumps({"issues": [
                {"name": "视觉定位", "action": "delete", "reason": "定义与原文矛盾"},
                {"name": "不存在本体", "action": "delete", "reason": "越权提名"},
                {"name": "混杂偏差", "action": "add", "reason": "缺少该概念",
                 "suggested_definition": "选择偏差的一种"},
            ]}, ensure_ascii=False)
        if "语音识别" in p:
            return '{"verdict": "wrong"}'
        if "我不知道" in p:
            return '{"verdict": "refused"}'
        return '{"verdict": "correct"}'

    fake_llm["ollama"] = _ollama
    fake_llm["judge"] = _judge
    # keep RAG deterministic without embeddings
    monkey_rag = [{"text": "视觉定位指根据自然语言描述在图像中定位目标区域。"}]
    original = benchmark._rag_search
    benchmark._rag_search = lambda conn, q, k: monkey_rag
    try:
        job_id = jobs.create_job(env, "benchmark", total=0, detail="t", ref_id=iid)
        benchmark.run_benchmark(env, job_id, iid, "fake-model", 5)
    finally:
        benchmark._rag_search = original

    row = env.execute("SELECT * FROM persona_benchmarks ORDER BY id DESC").fetchone()
    assert row["status"] == "done" and row["total"] == 1
    stats = json.loads(row["stats"])
    assert stats["arms"]["none"]["refusal"] == 100.0     # bare model refused
    assert stats["arms"]["ontology"]["hallucination"] == 100.0
    assert stats["arms"]["rag"]["hallucination"] == 100.0
    assert "1 题" in row["conclusion"]

    items = [dict(r) for r in env.execute(
        "SELECT arm, verdict FROM persona_benchmark_items").fetchall()]
    by_arm = {r["arm"]: r["verdict"] for r in items}
    assert by_arm["none"] == "refused"
    assert by_arm["ontology"] == "wrong"
    assert by_arm["rag"] == "wrong"
    assert by_arm["rag_ontology"] == "wrong"

    # failure attribution: only the closed-set nominations survive
    changes = [dict(r) for r in env.execute(
        "SELECT * FROM persona_ontology_changes WHERE status='pending'").fetchall()]
    by_name = {c["name"]: c for c in changes}
    assert len(changes) == 2
    assert by_name["视觉定位"]["action"] == "delete"
    assert by_name["混杂偏差"]["action"] == "add"          # missing concept -> add
    assert by_name["混杂偏差"]["kind"] == "entity"          # add records its kind
    assert by_name["混杂偏差"]["ontology_id"] is None       # no existing row to point at
    ev = json.loads(by_name["视觉定位"]["evidence"])
    assert ev[0]["quiz_id"] == seeded["quiz"]

    # summary shape for the card
    s = benchmark.benchmark_summary(env, iid)
    assert s["benchmark"]["id"] == row["id"] and s["changes"]
    assert s["versions"] == []


# ---------------- merge / rollback (user final approval) ----------------

def test_reject_change(env):
    from app import benchmark
    seeded = _seed_persona(env)
    now = 0
    bid = env.execute(
        "INSERT INTO persona_benchmarks(identity_id, model, judge, total,"
        " status, created_at) VALUES(?, 'm', 'j', 1, 'done', ?)",
        (seeded["identity_id"], now)).lastrowid
    cid = env.execute(
        "INSERT INTO persona_ontology_changes(identity_id, benchmark_id,"
        " ontology_id, name, action, reason, evidence, status, created_at)"
        " VALUES(?, 1, ?, '视觉定位', 'annotate', 'r', '[]', 'pending', ?)",
        (seeded["identity_id"], seeded["po"]["视觉定位"], now)).lastrowid
    env.commit()
    assert benchmark.reject_change(env, cid) is True
    assert benchmark.reject_change(env, cid) is False   # already handled
    assert env.execute("SELECT status FROM persona_ontology_changes"
                       " WHERE id=?", (cid,)).fetchone()["status"] == "rejected"


def test_merge_and_rollback(env):
    from app import benchmark
    seeded = _seed_persona(env)
    iid = seeded["identity_id"]
    now = 0
    bid = env.execute(
        "INSERT INTO persona_benchmarks(identity_id, model, judge, total,"
        " status, created_at) VALUES(?, 'm', 'j', 1, 'done', ?)",
        (iid, now)).lastrowid
    for name, action, extra in [
            ("视觉定位", "annotate", {"note": "定义与原文有偏差"}),
            ("RefCOCO", "update", {"suggested_definition": "指代理解基准数据集"}),
            ("RefCOCO", "delete", {})]:
        env.execute(
            "INSERT INTO persona_ontology_changes(identity_id, benchmark_id,"
            " ontology_id, name, action, suggested_definition, note, reason,"
            " evidence, status, created_at)"
            " VALUES(?,?,?,?,?,?,?, 'r', '[]', 'pending', ?)",
            (iid, bid, seeded["po"][name], name, action,
             extra.get("suggested_definition"), extra.get("note"), now))
    # add a missing concept entity (ontology_id NULL, kind='entity')
    env.execute(
        "INSERT INTO persona_ontology_changes(identity_id, benchmark_id,"
        " ontology_id, name, kind, action, suggested_definition, reason,"
        " evidence, status, created_at)"
        " VALUES(?,?,?,?, 'entity', 'add', ?, 'r', '[]', 'pending', ?)",
        (iid, bid, None, "混杂偏差", "选择偏差的一种", now))
    env.commit()

    # nothing to merge for an identity without pending changes
    empty = benchmark.merge_changes(env, iid + 100)
    assert empty["ok"] is False

    r = benchmark.merge_changes(env, iid)
    assert r["ok"] is True and r["version"] == 1
    assert r["applied"] == {"annotate": 1, "update": 1, "delete": 1, "add": 1}
    rows = {r_["name"]: dict(r_) for r_ in env.execute(
        "SELECT * FROM persona_ontology WHERE identity_id=?", (iid,)).fetchall()}
    assert "[测试提名] 定义与原文有偏差" in rows["视觉定位"]["note"]
    assert rows["RefCOCO"]["definition"] == "指代理解基准数据集"
    assert rows["RefCOCO"]["status"] == "deprecated"   # delete = soft delete
    # add: the new entity exists with its definition, is active, has no source
    assert rows["混杂偏差"]["definition"] == "选择偏差的一种"
    assert rows["混杂偏差"]["kind"] == "entity" and rows["混杂偏差"]["status"] == "active"
    assert rows["混杂偏差"]["source_candidate_id"] is None
    assert all(c["status"] == "merged" for c in env.execute(
        "SELECT status FROM persona_ontology_changes").fetchall())
    v1 = env.execute("SELECT * FROM persona_ontology_versions"
                     " WHERE version=1").fetchone()
    assert v1 and len(json.loads(v1["snapshot"])) == 2

    # user damages the ontology after merge, then rolls back to v1
    # (v1's snapshot was taken BEFORE the merge, so it restores pre-merge state)
    env.execute("UPDATE persona_ontology SET definition='被改坏的'"
                " WHERE identity_id=?", (iid,))
    env.commit()
    rb = benchmark.rollback_version(env, iid, v1["id"])
    assert rb["ok"] is True and rb["restored"] == 1 and rb["version"] == 2
    rows = {r_["name"]: dict(r_) for r_ in env.execute(
        "SELECT * FROM persona_ontology WHERE identity_id=?", (iid,)).fetchall()}
    assert rows["视觉定位"]["definition"] == "根据语言描述定位图中目标"  # v1 snapshot
    assert rows["视觉定位"]["note"] is None                            # pre-merge: no note
    assert rows["RefCOCO"]["definition"] == "指代理解数据集"           # pre-merge definition
    assert rows["RefCOCO"]["status"] == "active"                      # delete undone
    assert "混杂偏差" not in rows                                      # add undone by rollback
    assert env.execute("SELECT COUNT(*) c FROM persona_ontology_versions"
                       " WHERE identity_id=?", (iid,)).fetchone()["c"] == 2


def test_system_prompt_rag_block():
    from app import chat
    ctx = {"identity": {"name": "N", "mission": "M"},
           "anchors": [], "ontology": [], "relations": [],
           "rag": ["原文片段甲", "原文片段乙"]}
    s = chat._system_prompt(ctx, use_ontology=False)
    assert "【参考资料 · 原文片段】" in s and "原文片段甲" in s
    assert "资料里没有" in s          # rag-arm iron law 2
    assert "【参考资料" not in chat._system_prompt(
        {"identity": ctx["identity"], "anchors": [], "ontology": [],
         "relations": []}, use_ontology=False)
