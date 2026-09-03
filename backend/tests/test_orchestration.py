# -*- coding: utf-8 -*-
"""Orchestration tests: deterministic rule clean (0 LLM) + GLM triage gates
(three deterministic gates) + user's one-click confirm. LLM only nominates;
nothing lands until the user confirms (iron law)."""


def _insert_candidate(conn, name, status="pending", definition=None):
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', ?, ?, ?, 0)", (name, definition or "", status))
    conn.commit()
    return cur.lastrowid


# ---------------- rule classification (deterministic, 0 LLM) ----------------

def test_classify_rule_noise():
    from app.orchestration import _classify_rule
    # R1 pure number/symbol
    assert _classify_rule("0.4")[0] == "delete"
    assert _classify_rule("1,000")[0] == "delete"
    assert _classify_rule("10034–10043")[0] == "delete"
    # R2 single char
    assert _classify_rule("O")[0] == "delete"
    # R3 citation
    assert _classify_rule("Bochkovskiy et al.")[0] == "delete"
    assert _classify_rule("Bochkovskiy, Wang, and Liao 2020")[0] == "delete"
    # real entities must survive (no false positives)
    assert _classify_rule("Visual Grounding") is None
    assert _classify_rule("ResNet-50") is None
    assert _classify_rule("F1 score") is None
    assert _classify_rule("报销系统") is None


# ---------------- case-duplicate merge (R4) ----------------

def test_case_dup_merges(env):
    from app.orchestration import _case_dup_merges
    conn = env
    a = _insert_candidate(conn, "Visual Grounding")
    _insert_candidate(conn, "visual grounding")
    _insert_candidate(conn, "Visual grounding")
    merges = _case_dup_merges(conn, set())
    assert len(merges) == 2  # 3 same-lowercase names -> 2 merges
    targets = {m["merge_into"] for m in merges}
    assert len(targets) == 1  # all merge into the same canonical
    # skip_ids respected
    merges2 = _case_dup_merges(conn, {a})
    assert all(m["candidate_id"] != a for m in merges2)


def test_run_rule_clean_creates_batch(env):
    from app import orchestration
    conn = env
    _insert_candidate(conn, "0.4")             # R1 delete
    _insert_candidate(conn, "O")               # R2 delete
    _insert_candidate(conn, "Foo et al.")      # R3 delete
    _insert_candidate(conn, "Visual Grounding")
    _insert_candidate(conn, "visual grounding")  # R4 merge
    r = orchestration.run_rule_clean(conn)
    assert r["batch_id"] is not None
    assert r["deletes"] == 3 and r["merges"] == 1
    # items are pending only, nothing landed
    items = conn.execute("SELECT action, status FROM orchestration_items").fetchall()
    assert all(i["status"] == "pending" for i in items)
    assert any(i["action"] == "delete" for i in items)
    assert any(i["action"] == "merge" for i in items)
    # no candidate was actually deleted/merged yet
    assert conn.execute(
        "SELECT COUNT(*) c FROM candidates WHERE kind='entity'").fetchone()["c"] == 5


# ---------------- GLM triage three gates ----------------

def test_validate_glm_three_gates():
    from app.orchestration import _validate_glm
    id_map = {
        1: {"id": 1, "name": "Visual Grounding", "status": "pending", "is_anchor": False},
        2: {"id": 2, "name": "报销系统", "status": "approved", "is_anchor": False},
        3: {"id": 3, "name": "锚点概念", "status": "pending", "is_anchor": True},
    }
    data = {"suggestions": [
        {"id": 1, "action": "delete", "category": "irrelevant", "reason": "noise"},
        # approved -> demoted to keep (G3 conflict gate)
        {"id": 2, "action": "delete", "category": "irrelevant", "reason": "noise"},
        # anchor -> demoted to keep (G3)
        {"id": 3, "action": "delete", "category": "irrelevant", "reason": "noise"},
        # dup id -> dropped (G2)
        {"id": 1, "action": "keep", "category": "core", "reason": "dup"},
        # unknown id -> dropped (G2)
        {"id": 99, "action": "delete", "category": "irrelevant", "reason": "x"},
        # bad action -> dropped (G1 closed set)
        {"id": 1, "action": "bogus", "category": "core", "reason": "x"},
        # merge to self -> dropped (G2)
        {"id": 1, "action": "merge", "category": None, "reason": "x", "merge_into": 1},
    ]}
    out = _validate_glm(data, id_map)
    assert len(out) == 3
    by_id = {o["candidate_id"]: o for o in out}
    assert by_id[1]["action"] == "delete"
    assert by_id[2]["action"] == "keep"   # approved protected
    assert by_id[3]["action"] == "keep"   # anchor protected


def test_validate_glm_valid_merge():
    from app.orchestration import _validate_glm
    id_map = {
        1: {"id": 1, "name": "visual grounding", "status": "pending", "is_anchor": False},
        2: {"id": 2, "name": "Visual Grounding", "status": "pending", "is_anchor": False},
    }
    data = {"suggestions": [
        {"id": 1, "action": "merge", "category": None, "reason": "dup", "merge_into": 2},
    ]}
    out = _validate_glm(data, id_map)
    assert len(out) == 1 and out[0]["action"] == "merge"
    assert out[0]["merge_into"] == 2


# ---------------- confirm (user's one-click final adjudication) ----------------

def test_confirm_all_executes(env):
    from app import orchestration
    conn = env
    a = _insert_candidate(conn, "visual grounding")
    b = _insert_candidate(conn, "Visual Grounding")
    c = _insert_candidate(conn, "0.4")
    d = _insert_candidate(conn, "报销系统")
    bid = orchestration._create_batch(conn, "glm")
    orchestration._insert_items(conn, bid, [
        {"candidate_id": a, "action": "merge", "category": "marginal",
         "reason": "dup", "merge_into": b},
        {"candidate_id": c, "action": "delete", "category": "irrelevant",
         "reason": "noise", "merge_into": None},
        {"candidate_id": d, "action": "keep", "category": "core",
         "reason": "keep", "merge_into": None},
    ])
    r = orchestration.confirm_all(conn)
    assert r["ok"]
    assert r["merged"] == 1 and r["deleted"] == 1 and r["kept"] == 1
    # a merged into b, c deleted, d untouched
    assert conn.execute("SELECT status FROM candidates WHERE id=?", (a,)).fetchone()["status"] == "merged"
    assert conn.execute("SELECT COUNT(*) c FROM candidates WHERE id=?", (c,)).fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM candidates WHERE id=?", (d,)).fetchone()["status"] == "pending"
    # batch now confirmed
    assert conn.execute("SELECT status FROM orchestration_batches WHERE id=?", (bid,)).fetchone()["status"] == "confirmed"


def test_confirm_empty(env):
    from app import orchestration
    r = orchestration.confirm_all(env)
    assert not r["ok"]


# ---------------- GLM orchestration end-to-end (fake judge) ----------------

def test_run_glm_orchestration_end_to_end(env, fake_llm):
    from app import orchestration, jobs
    conn = env
    _insert_candidate(conn, "Visual Grounding")
    _insert_candidate(conn, "0.4")

    def judge(messages):
        return ('{"suggestions": ['
                '{"id": 1, "action": "delete", "category": "irrelevant", "reason": "noise"},'
                '{"id": 2, "action": "delete", "category": "irrelevant", "reason": "noise"}]}')

    fake_llm["judge"] = judge
    job_id = jobs.create_job(conn, "orchestrate", total=0)
    orchestration.run_glm_orchestration(conn, job_id)
    # suggestions stored as pending glm items
    items = conn.execute(
        "SELECT oi.action, b.source FROM orchestration_items oi"
        " JOIN orchestration_batches b ON b.id=oi.batch_id"
        " WHERE b.source='glm'").fetchall()
    assert len(items) == 2
    assert all(i["action"] == "delete" for i in items)
    # job finished ok
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"] == "done"
