# -*- coding: utf-8 -*-
"""Assembly tests: persona image -> anchor recall (0 LLM) + rule exclusion
(0 LLM) + GLM triage three gates + user's one-click confirm (copy adopted
candidates into persona_ontology). LLM only nominates; nothing is copied
until the user confirms (iron law)."""


def _insert_candidate(conn, name, status="pending", definition=None):
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', ?, ?, ?, 0)", (name, definition or "", status))
    conn.commit()
    return cur.lastrowid


def _insert_identity(conn, name="视觉定位算法研究员", mission="视觉定位算法设计与优化",
                     anchors=None):
    cur = conn.execute(
        "INSERT INTO identities(name, mission, description, keywords, status, created_at)"
        " VALUES(?, ?, '', '[]', 'approved', 0)", (name, mission))
    conn.commit()
    iid = cur.lastrowid
    for a in (anchors or []):
        conn.execute(
            "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
            " VALUES(?, ?, '概念', '', 'approved', 0)", (iid, a))
    conn.commit()
    return iid


# ---------------- L0 anchor recall (deterministic, 0 LLM) ----------------

def test_anchor_recall():
    from app.assembly import _anchor_recall
    anchors = ["注意力机制", "候选框生成", "视觉-语言融合"]
    assert _anchor_recall("注意力机制", anchors) is not None     # exact
    assert _anchor_recall("多头注意力机制", anchors) is not None  # anchor inside cand
    assert _anchor_recall("注意力机制设计", anchors) is not None  # anchor inside cand
    assert _anchor_recall("视觉语言融合", anchors) is None        # hyphen differs
    assert _anchor_recall("机制", anchors) is None                # 2-char substring too short
    assert _anchor_recall("A", anchors) is None                   # single char


# ---------------- GLM triage three gates ----------------

def test_validate_assembly_three_gates():
    from app.assembly import _validate_assembly
    id_map = {
        1: {"id": 1, "name": "Visual Grounding", "status": "pending", "is_anchor": False},
        2: {"id": 2, "name": "报销系统", "status": "approved", "is_anchor": False},
        3: {"id": 3, "name": "锚点概念", "status": "pending", "is_anchor": True},
    }
    data = {"suggestions": [
        {"id": 1, "action": "exclude", "category": "irrelevant", "reason": "noise"},
        # approved -> demoted to adopt (G3 conflict gate)
        {"id": 2, "action": "exclude", "category": "irrelevant", "reason": "noise"},
        # anchor -> demoted to adopt (G3)
        {"id": 3, "action": "exclude", "category": "irrelevant", "reason": "noise"},
        # dup id -> dropped (G2)
        {"id": 1, "action": "adopt", "category": "core", "reason": "dup"},
        # unknown id -> dropped (G2)
        {"id": 99, "action": "exclude", "category": "irrelevant", "reason": "x"},
        # bad action -> dropped (G1 closed set)
        {"id": 1, "action": "bogus", "category": "core", "reason": "x"},
    ]}
    out = _validate_assembly(data, id_map)
    assert len(out) == 3
    by_id = {o["candidate_id"]: o for o in out}
    assert by_id[1]["action"] == "exclude"
    assert by_id[2]["action"] == "adopt"   # approved protected
    assert by_id[3]["action"] == "adopt"   # anchor protected


def test_validate_assembly_bad_category_nulled():
    from app.assembly import _validate_assembly
    id_map = {1: {"id": 1, "name": "X", "status": "pending", "is_anchor": False}}
    data = {"suggestions": [
        {"id": 1, "action": "adopt", "category": "bogus", "reason": "x"},
    ]}
    out = _validate_assembly(data, id_map)
    assert len(out) == 1 and out[0]["category"] is None


# ---------------- confirm (copy adopted -> persona_ontology) ----------------

def test_confirm_assembly_copies_adopted(env):
    from app import assembly
    conn = env
    iid = _insert_identity(conn)
    a = _insert_candidate(conn, "注意力机制")
    b = _insert_candidate(conn, "0.4")
    c = _insert_candidate(conn, "视觉定位")
    bid = assembly._create_batch(conn, iid)
    assembly._insert_items(conn, bid, [
        {"candidate_id": a, "action": "adopt", "category": "core", "reason": "锚点召回"},
        {"candidate_id": b, "action": "exclude", "category": "irrelevant", "reason": "规则"},
        {"candidate_id": c, "action": "adopt", "category": "core", "reason": "GLM"},
    ])
    r = assembly.confirm_assembly(conn)
    assert r["ok"]
    assert r["adopted"] == 2 and r["excluded"] == 1
    rows = conn.execute(
        "SELECT name, source_candidate_id FROM persona_ontology WHERE identity_id=?",
        (iid,)).fetchall()
    names = {x["name"] for x in rows}
    assert names == {"注意力机制", "视觉定位"}
    assert {x["source_candidate_id"] for x in rows} == {a, c}
    assert conn.execute(
        "SELECT status FROM assembly_batches WHERE id=?", (bid,)).fetchone()["status"] == "confirmed"


def test_confirm_assembly_skips_existing(env):
    from app import assembly
    conn = env
    iid = _insert_identity(conn)
    a = _insert_candidate(conn, "注意力机制")
    conn.execute(
        "INSERT INTO persona_ontology(identity_id, kind, name, definition,"
        " source_candidate_id, status, created_at) VALUES(?, 'entity', ?, '', ?, 'active', 0)",
        (iid, "注意力机制", a))
    conn.commit()
    bid = assembly._create_batch(conn, iid)
    assembly._insert_items(conn, bid, [
        {"candidate_id": a, "action": "adopt", "category": "core", "reason": "锚点召回"},
    ])
    r = assembly.confirm_assembly(conn)
    assert r["ok"] and r["skipped"] == 1 and r["adopted"] == 0


def test_confirm_assembly_empty(env):
    from app import assembly
    r = assembly.confirm_assembly(env)
    assert not r["ok"]


# ---------------- run_assembly end-to-end (fake judge) ----------------

def test_run_assembly_end_to_end(env, fake_llm):
    from app import assembly, jobs
    conn = env
    iid = _insert_identity(conn, anchors=["注意力机制"])
    _insert_candidate(conn, "注意力机制")       # L0 anchor recall -> adopt + core
    _insert_candidate(conn, "多头注意力机制")   # L0 anchor recall -> adopt + core
    _insert_candidate(conn, "0.4")             # L1 rule -> exclude + irrelevant
    _insert_candidate(conn, "视觉定位")         # L2 GLM -> adopt
    _insert_candidate(conn, "实验数据")         # L2 GLM -> exclude

    def judge(messages):
        return ('{"suggestions": ['
                '{"id": 4, "action": "adopt", "category": "core", "reason": "核心"},'
                '{"id": 5, "action": "exclude", "category": "irrelevant", "reason": "噪音"}]}')

    fake_llm["judge"] = judge
    job_id = jobs.create_job(conn, "assemble", total=0, ref_id=iid)
    assembly.run_assembly(conn, job_id, iid)
    items = conn.execute(
        "SELECT ai.action FROM assembly_items ai"
        " JOIN assembly_batches b ON b.id=ai.batch_id"
        " WHERE b.identity_id=?", (iid,)).fetchall()
    actions = [i["action"] for i in items]
    assert actions.count("adopt") == 3
    assert actions.count("exclude") == 2
    # nothing copied yet (nomination only)
    assert conn.execute("SELECT COUNT(*) c FROM persona_ontology").fetchone()["c"] == 0
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"] == "done"


def test_run_assembly_resume_idempotent(env, fake_llm):
    """A re-run must archive the previous pending batch and never duplicate
    the deterministic L0/L1 items in the fresh pending batch (regression:
    anchor recall grew 35->41 and rule exclusion 156->239 on every resume)."""
    from app import assembly, jobs
    conn = env
    iid = _insert_identity(conn, anchors=["注意力机制"])
    _insert_candidate(conn, "注意力机制")       # L0 anchor -> adopt + core
    _insert_candidate(conn, "0.4")             # L1 rule  -> exclude
    _insert_candidate(conn, "视觉定位")         # L2 GLM   -> adopt

    def judge(messages):
        return ('{"suggestions": ['
                '{"id": 3, "action": "adopt", "category": "core", "reason": "核心"}]}')

    fake_llm["judge"] = judge
    assembly.run_assembly(conn, jobs.create_job(conn, "assemble", total=0, ref_id=iid), iid)
    assembly.run_assembly(conn, jobs.create_job(conn, "assemble", total=0, ref_id=iid), iid)

    pending = conn.execute(
        "SELECT id FROM assembly_batches WHERE identity_id=? AND status='pending'",
        (iid,)).fetchall()
    assert len(pending) == 1  # exactly one fresh pending batch (old one archived)

    actions = [r["action"] for r in conn.execute(
        "SELECT ai.action FROM assembly_items ai"
        " JOIN assembly_batches b ON b.id=ai.batch_id"
        " WHERE b.identity_id=? AND ai.status='pending'",
        (iid,)).fetchall()]
    assert actions.count("adopt") == 2    # 1 anchor + 1 GLM (not duplicated to 4)
    assert actions.count("exclude") == 1  # 1 rule (not duplicated to 2)


def test_run_assembly_archives_not_deletes(env, fake_llm):
    """A re-run archives (not deletes) the previous batch: its items stay as
    'dismissed' rows so restore_assembly can bring them back (DM8)."""
    from app import assembly, jobs
    conn = env
    iid = _insert_identity(conn, anchors=["注意力机制"])
    _insert_candidate(conn, "注意力机制")   # L0 anchor -> adopt
    _insert_candidate(conn, "0.4")          # L1 rule  -> exclude

    fake_llm["judge"] = lambda m: '{"suggestions": []}'
    assembly.run_assembly(conn, jobs.create_job(conn, "assemble", total=0, ref_id=iid), iid)
    first_batch = conn.execute(
        "SELECT id FROM assembly_batches WHERE identity_id=? AND status='pending'",
        (iid,)).fetchone()["id"]

    # second run archives the first batch
    assembly.run_assembly(conn, jobs.create_job(conn, "assemble", total=0, ref_id=iid), iid)

    archived_items = conn.execute(
        "SELECT COUNT(*) c FROM assembly_items WHERE batch_id=? AND status='dismissed'",
        (first_batch,)).fetchone()["c"]
    assert archived_items == 2  # preserved, not physically deleted

    # restore brings the archived batch back to pending
    r = assembly.restore_assembly(conn, iid)
    assert r["ok"] and r["batch_id"] == first_batch and r["items"] == 2
    assert conn.execute(
        "SELECT status FROM assembly_batches WHERE id=?", (first_batch,)
    ).fetchone()["status"] == "pending"


def test_restore_assembly_recovers_discarded(env):
    """Discard then restore: dismissed items return to pending (undo discard)."""
    from app import assembly
    conn = env
    iid = _insert_identity(conn)
    a = _insert_candidate(conn, "注意力机制")
    b = _insert_candidate(conn, "0.4")
    bid = assembly._create_batch(conn, iid)
    assembly._insert_items(conn, bid, [
        {"candidate_id": a, "action": "adopt", "category": "core", "reason": "x"},
        {"candidate_id": b, "action": "exclude", "category": "irrelevant", "reason": "x"},
    ])
    assembly.discard_assembly(conn, iid)
    assert conn.execute("SELECT status FROM assembly_batches WHERE id=?",
                        (bid,)).fetchone()["status"] == "discarded"
    assert conn.execute("SELECT COUNT(*) c FROM assembly_items WHERE batch_id=? AND status='dismissed'",
                        (bid,)).fetchone()["c"] == 2

    r = assembly.restore_assembly(conn, iid)
    assert r["ok"] and r["items"] == 2
    assert conn.execute("SELECT status FROM assembly_batches WHERE id=?",
                        (bid,)).fetchone()["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) c FROM assembly_items WHERE batch_id=? AND status='pending'",
                        (bid,)).fetchone()["c"] == 2


def test_restore_assembly_nothing_to_restore(env):
    from app import assembly
    r = assembly.restore_assembly(env, 99999)
    assert not r["ok"]


def test_run_assembly_no_identity(env, fake_llm):
    from app import assembly, jobs
    conn = env
    _insert_candidate(conn, "视觉定位")
    job_id = jobs.create_job(conn, "assemble", total=0, ref_id=99999)
    assembly.run_assembly(conn, job_id, 99999)  # identity does not exist
    assert conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()["status"] == "failed"


def test_run_assembly_resume_keeps_checkpoint(env, fake_llm):
    """Pause→resume must NOT reset progress: a resumed run keeps the pending
    batch (L0/L1 pre-items already stored), does not re-insert them, and does
    not re-run already-committed GLM batches (regression: every resume reset
    progress_current to 0 and re-ran all GLM batches + re-seeded L0/L1)."""
    from app import assembly, jobs
    conn = env
    iid = _insert_identity(conn, anchors=["注意力机制"])
    _insert_candidate(conn, "注意力机制")   # L0 anchor -> adopt (id 1)
    _insert_candidate(conn, "0.4")          # L1 rule  -> exclude (id 2)
    _insert_candidate(conn, "视觉定位")      # L2 GLM   -> adopt (id 3)

    calls = {"n": 0}
    def judge(messages):
        calls["n"] += 1
        return ('{"suggestions": ['
                '{"id": 3, "action": "adopt", "category": "core", "reason": "核心"}]}')

    fake_llm["judge"] = judge
    job_id = jobs.create_job(conn, "assemble", total=0, ref_id=iid)
    assembly.run_assembly(conn, job_id, iid)
    assert calls["n"] == 1

    # resume: same job row, progress_current already == nb == 1
    assembly.run_assembly(conn, job_id, iid)
    assert calls["n"] == 1  # committed GLM batch not re-run

    pending = conn.execute(
        "SELECT id FROM assembly_batches WHERE identity_id=? AND status='pending'",
        (iid,)).fetchall()
    assert len(pending) == 1  # exactly one pending batch (not discarded/re-created)
    actions = [r["action"] for r in conn.execute(
        "SELECT ai.action FROM assembly_items ai"
        " JOIN assembly_batches b ON b.id=ai.batch_id WHERE b.identity_id=?",
        (iid,)).fetchall()]
    assert actions.count("adopt") == 2    # 1 anchor + 1 GLM (not duplicated)
    assert actions.count("exclude") == 1  # 1 rule (not duplicated)


def test_run_assembly_resume_from_mid_batch(env, fake_llm, monkeypatch):
    """Resume continues from the checkpoint: with progress_current=N, only the
    remaining L2 batches are dispatched (batch N..end), not 0..end."""
    from app import assembly, jobs
    import app.assembly as asm
    conn = env
    monkeypatch.setattr(asm, "GLM_BATCH", 2)  # force >1 batch

    iid = _insert_identity(conn, anchors=["注意力机制"])
    _insert_candidate(conn, "注意力机制")   # L0 anchor -> pre_item (id 1)
    for n in ["概念一", "概念二", "概念三", "概念四"]:
        _insert_candidate(conn, n)          # 4 L2 candidates -> nb=2

    job_id = jobs.create_job(conn, "assemble", total=0, ref_id=iid)
    # simulate paused mid-run: batch 0 already committed, pre_items persisted
    conn.execute("UPDATE jobs SET progress_current=1, progress_total=2 WHERE id=?",
                 (job_id,))
    conn.commit()
    bid = assembly._create_batch(conn, iid)
    assembly._insert_items(conn, bid, [
        {"candidate_id": 1, "action": "adopt", "category": "core",
         "reason": "锚点召回：与锚点「注意力机制」同名"}])

    calls = {"n": 0}
    def judge(messages):
        calls["n"] += 1
        return '{"suggestions": []}'
    fake_llm["judge"] = judge

    assembly.run_assembly(conn, job_id, iid)  # resume from batch 1

    assert calls["n"] == 1  # only batch 1 dispatched, batch 0 skipped


# ---------------- create / update identity (user-defined persona) ----------------

def test_create_identity_seeds_to_anchors_and_ontology(env):
    from app import identity
    conn = env
    a = _insert_candidate(conn, "注意力机制", definition="关注机制")
    b = _insert_candidate(conn, "候选框生成", definition="生成候选框")
    iid = identity.create_identity(conn, "视觉定位研究员", "视觉定位算法设计与优化",
                                   seed_candidate_ids=[a, b])
    assert iid is not None
    row = conn.execute("SELECT name, status FROM identities WHERE id=?",
                       (iid,)).fetchone()
    assert row["status"] == "approved" and row["name"] == "视觉定位研究员"
    anchors = conn.execute("SELECT name, status FROM anchors WHERE identity_id=?",
                           (iid,)).fetchall()
    assert {x["name"] for x in anchors} == {"注意力机制", "候选框生成"}
    assert all(x["status"] == "approved" for x in anchors)
    po = conn.execute("SELECT name, source_candidate_id FROM persona_ontology"
                      " WHERE identity_id=?", (iid,)).fetchall()
    assert {x["name"] for x in po} == {"注意力机制", "候选框生成"}
    assert {x["source_candidate_id"] for x in po} == {a, b}


def test_create_identity_bad_name(env):
    from app import identity
    conn = env
    assert identity.create_identity(conn, "", "使命") is None
    assert identity.create_identity(conn, "名字", "") is not None  # mission optional


def test_update_identity_lightweight(env):
    from app import identity
    conn = env
    iid = identity.create_identity(conn, "旧名字", "旧使命")
    assert identity.update_identity(conn, iid, {"name": "新名字", "mission": "新使命"})
    row = conn.execute("SELECT name, mission FROM identities WHERE id=?",
                       (iid,)).fetchone()
    assert row["name"] == "新名字" and row["mission"] == "新使命"
    assert not identity.update_identity(conn, iid, {})  # nothing to update


def test_multi_persona_confirm_scoped(env):
    """Multiple personas coexist; a scoped confirm only copies one persona's
    adopted candidates (multi-persona regression guard)."""
    from app import assembly, identity
    conn = env
    a = identity.create_identity(conn, "数字人A", "使命A", seed_candidate_ids=[])
    b = identity.create_identity(conn, "数字人B", "使命B", seed_candidate_ids=[])
    cand = _insert_candidate(conn, "共享概念")
    ba = assembly._create_batch(conn, a)
    bb = assembly._create_batch(conn, b)
    assembly._insert_items(conn, ba, [{"candidate_id": cand, "action": "adopt",
                                       "category": "core", "reason": "x"}])
    assembly._insert_items(conn, bb, [{"candidate_id": cand, "action": "adopt",
                                       "category": "core", "reason": "x"}])
    r = assembly.confirm_assembly(conn, a)
    assert r["ok"] and r["adopted"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM persona_ontology WHERE identity_id=?",
                        (a,)).fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM persona_ontology WHERE identity_id=?",
                        (b,)).fetchone()["c"] == 0


def test_pending_summary_scoped_by_identity(env):
    """Regression: pending_summary(identity_id) passed two bindings to queries
    that each use one placeholder, raising sqlite3.ProgrammingError (500 on the
    assembly card). Scoping must return only the target persona's pending
    batch/items."""
    from app import assembly, identity
    conn = env
    a = identity.create_identity(conn, "数字人A", "使命A", seed_candidate_ids=[])
    b = identity.create_identity(conn, "数字人B", "使命B", seed_candidate_ids=[])
    ca = _insert_candidate(conn, "概念A")
    cb = _insert_candidate(conn, "概念B")
    ba = assembly._create_batch(conn, a)
    bb = assembly._create_batch(conn, b)
    assembly._insert_items(conn, ba, [{"candidate_id": ca, "action": "adopt",
                                       "category": "core", "reason": "x"}])
    assembly._insert_items(conn, bb, [{"candidate_id": cb, "action": "adopt",
                                       "category": "core", "reason": "x"}])

    # scoped: must not raise, and must return only A's batch
    s = assembly.pending_summary(conn, a)
    assert [x["identity_id"] for x in s["batches"]] == [a]
    assert s["total"] == 1
    assert s["items"][0]["cand_name"] == "概念A"

    # unscoped: both
    s_all = assembly.pending_summary(conn)
    assert {x["identity_id"] for x in s_all["batches"]} == {a, b}
    assert s_all["total"] == 2
