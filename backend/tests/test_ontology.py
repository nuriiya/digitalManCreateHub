# -*- coding: utf-8 -*-
"""Ontology extraction + approval flow tests with a fake LLM nominator."""
import json
import threading

from app import jobs, ingest, ontology

EXTRACTION = json.dumps({
    "entities": [
        {"name": "报销系统", "type": "系统", "definition": "公司财务流程核心系统",
         "mentions": ["报销系统"]},
        {"name": "伪造实体", "type": "概念", "definition": "LLM幻觉",
         "mentions": ["这个mention原文里根本不存在"]},
        {"name": "类型越界实体", "type": "神秘类别", "definition": "x",
         "mentions": ["报销系统"]},
        {"name": "审批流程", "type": "流程", "definition": "报销单提交后流转",
         "mentions": ["审批流程"]},
    ],
    "relations": [
        {"source": "报销系统", "target": "审批流程", "type": "包含"},
        {"source": "不存在的实体", "target": "审批流程", "type": "引用"},
    ],
}, ensure_ascii=False)


def _ingest(env, workdir):
    job_id = jobs.create_job(env, "ingest", 0, str(workdir))
    ingest.ingest_workdir(env, job_id, str(workdir))


def _extract(env, fake_llm):
    fake_llm["extraction"] = lambda prompt: EXTRACTION
    job_id = jobs.create_job(env, "ontology", 0, "EDC-lite")
    ontology.run_extraction(env, job_id)
    return job_id


def test_gates_drop_fabricated_and_invalid(env, workdir, fake_llm):
    _ingest(env, workdir)
    _extract(env, fake_llm)
    names = [r["name"] for r in env.execute(
        "SELECT name FROM candidates").fetchall()]
    # good candidates stored
    assert "报销系统" in names and "审批流程" in names
    # fabricated evidence + closed-set violation dropped
    assert "伪造实体" not in names
    assert "类型越界实体" not in names
    # relation with dangling source not stored
    rels = env.execute("SELECT * FROM relations").fetchall()
    assert len(rels) == 1
    assert rels[0]["relation_type"] == "包含"


def test_mentions_link_to_chunk_spans(env, workdir, fake_llm):
    _ingest(env, workdir)
    _extract(env, fake_llm)
    row = env.execute(
        "SELECT m.text, m.span_start, m.span_end, c.text AS chunk_text"
        " FROM mentions m JOIN chunks c ON c.id=m.chunk_id"
        " JOIN candidates cd ON cd.id=m.candidate_id"
        " WHERE cd.name='报销系统'").fetchone()
    assert row is not None
    assert row["chunk_text"][row["span_start"]:row["span_end"]] == "报销系统"


def test_approve_reject_merge(env, workdir, fake_llm):
    _ingest(env, workdir)
    _extract(env, fake_llm)
    ids = {r["name"]: r["id"] for r in env.execute(
        "SELECT id, name FROM candidates").fetchall()}
    assert ontology.set_status(env, ids["报销系统"], "approved")
    assert ontology.set_status(env, ids["审批流程"], "approved")
    assert not ontology.set_status(env, ids["报销系统"], "hacked")  # closed set
    # merge: create a dup then merge it in
    cur = env.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', '报销系统v2', 'dup', 'pending', 0)")
    conn_dup = cur.lastrowid
    env.commit()
    assert ontology.merge_candidate(env, conn_dup, ids["报销系统"])
    row = env.execute("SELECT status, merged_into FROM candidates WHERE id=?",
                      (conn_dup,)).fetchone()
    assert row["status"] == "merged" and row["merged_into"] == ids["报销系统"]
    # graph: default view shows approved+pending, merged hidden
    g = ontology.graph(env)
    node_names = [n["name"] for n in g["nodes"]]
    assert "报销系统" in node_names and "报销系统v2" not in node_names
    edge = g["edges"][0]
    assert edge["source"] == ids["报销系统"] and edge["target"] == ids["审批流程"]
    assert not edge["dangling"]


def test_candidate_detail_shows_evidence(env, workdir, fake_llm):
    _ingest(env, workdir)
    _extract(env, fake_llm)
    cid = env.execute("SELECT id FROM candidates WHERE name='报销系统'").fetchone()["id"]
    d = ontology.candidate_detail(env, cid)
    assert d["candidate"]["kind"] == "entity"
    assert d["mentions"] and d["mentions"][0]["chunk_id"] > 0
    assert any(r["relation_type"] == "包含" for r in d["relations"])


def test_extraction_progress_events(env, workdir, fake_llm):
    _ingest(env, workdir)
    job_id = _extract(env, fake_llm)
    events = jobs.events_since(env, 0)
    types = [e["type"] for e in events if e["job_id"] == job_id]
    assert "job.started" in types and "ontology.done" in types
    done = [e["payload"] for e in events if e["type"] == "ontology.done"][0]
    assert done["rejected_evidence"] >= 1  # the fabricated mention was caught
    assert done["rejected_structure"] >= 1  # the bad type was caught


def test_all_llm_calls_failed_marks_job_failed(env, workdir, fake_llm):
    """Regression: 401/unreachable LLM must pause the ontology job loudly
    (user-visible error + recoverable via resume), never report 'done' with
    zero candidates (silent lie). Aligns with ingest auto-pause semantics."""
    _ingest(env, workdir)

    def _boom(prompt):
        raise RuntimeError("401 Authentication Fails")

    fake_llm["extraction"] = _boom
    job_id = jobs.create_job(env, "ontology", 0, "EDC-lite")
    ontology.run_extraction(env, job_id)
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "paused"
    assert "LLM" in (row["error"] or "")
    types = [e["type"] for e in jobs.events_since(env, 0) if e["job_id"] == job_id]
    assert "job.autopaused" in types
    # no silent 'done' with zero candidates: extraction never reached the end
    assert "ontology.done" not in types


def test_concurrent_extraction_handles_many_chunks(env, workdir, fake_llm):
    """8 files x 1 chunk: every chunk is LLM-extracted exactly once (concurrent
    scheduler, workers share nothing), all valid nominations stored, job done,
    checkpoint advances to total. Per-chunk dedupe collapses to unique names."""
    for i in range(7):
        (workdir / f"f{i}.md").write_text(
            f"{i}号报销系统的预算需要财务专员复核。审批流程覆盖所有报销单。",
            encoding="utf-8")
    _ingest(env, workdir)
    total = env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    assert total >= 8
    calls = []
    lock = threading.Lock()

    def _count(prompt):
        with lock:
            calls.append(1)
        return EXTRACTION

    fake_llm["extraction"] = _count
    job_id = jobs.create_job(env, "ontology", total, "EDC-lite")
    ontology.run_extraction(env, job_id)
    assert len(calls) == total  # every chunk exactly once, none re-run
    row = env.execute(
        "SELECT status, progress_current FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    assert row["status"] == "done" and row["progress_current"] == total
    names = [r["name"] for r in env.execute("SELECT name FROM candidates").fetchall()]
    assert "报销系统" in names and "审批流程" in names
    assert names.count("报销系统") == 1  # cross-chunk dedupe intact
    rels = env.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"]
    assert rels == 1  # the one valid relation, not duplicated per chunk


def test_rate_limit_backs_off_and_retries(env, workdir, fake_llm):
    """Transient 429 must NOT pause the job: scheduler halves the window and
    re-queues the chunk; once the throttle clears, everything extracts."""
    for i in range(3):
        (workdir / f"g{i}.md").write_text(
            f"{i}号报销系统的预算需要财务专员复核。审批流程覆盖所有报销单。",
            encoding="utf-8")
    _ingest(env, workdir)
    total = env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    calls = []
    lock = threading.Lock()

    def _flaky(prompt):
        with lock:
            calls.append(1)
            if len(calls) <= 2:
                raise RuntimeError("RateLimitError: 429 Too Many Requests")
        return EXTRACTION

    fake_llm["extraction"] = _flaky
    job_id = jobs.create_job(env, "ontology", total, "EDC-lite")
    ontology.run_extraction(env, job_id)
    row = env.execute(
        "SELECT status, progress_current FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    assert row["status"] == "done"  # 429 never paused the job
    assert row["progress_current"] == total
    assert len(calls) == total + 2  # 2 throttled calls retried, all chunks done


def test_adaptive_window_steps_down_on_failures(env, workdir, fake_llm):
    """Adaptive concurrency: failures (429) STEP the window DOWN by 1
    (4 -> 3 -> 2, not halved), floor 1; after OK_STREAK consecutive
    successes it steps back UP to the cap. Job never pauses on 429."""
    for i in range(17):
        (workdir / f"w{i}.md").write_text(
            f"{i}号报销系统的预算需要财务专员复核。审批流程覆盖所有报销单。",
            encoding="utf-8")
    _ingest(env, workdir)
    total = env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    assert total >= 18
    calls = []
    lock = threading.Lock()

    def _flaky(prompt):
        with lock:
            calls.append(1)
            n = len(calls)
        if n <= 2:
            raise RuntimeError("RateLimitError: 429 Too Many Requests")
        return EXTRACTION

    fake_llm["extraction"] = _flaky
    job_id = jobs.create_job(env, "ontology", total, "EDC-lite")
    ontology.run_extraction(env, job_id)
    row = env.execute(
        "SELECT status, progress_current FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    assert row["status"] == "done" and row["progress_current"] == total
    events = [e for e in jobs.events_since(env, 0)
              if e["type"] == "ontology.concurrency"]
    windows = [e["payload"]["window"] for e in events]
    # stepped down 4 -> 3 -> 2 (one per failure, NOT halved to 2 -> 1)
    assert windows[:2] == [3, 2]
    # >= OK_STREAK successes afterwards step it back up to the cap
    assert windows[-1] == 4
    assert "job.autopaused" not in [
        e["type"] for e in jobs.events_since(env, 0) if e["job_id"] == job_id]


def test_concurrent_llm_failure_pauses_job(env, workdir, fake_llm):
    """Non-429 LLM failure under concurrency still auto-pauses loudly (no
    silent rule-fallback flood, no fake 'done' with zero candidates)."""
    for i in range(3):
        (workdir / f"h{i}.md").write_text(
            f"{i}号报销系统的预算需要财务专员复核。审批流程覆盖所有报销单。",
            encoding="utf-8")
    _ingest(env, workdir)

    def _boom(prompt):
        raise RuntimeError("401 Authentication Fails")

    fake_llm["extraction"] = _boom
    job_id = jobs.create_job(env, "ontology", 0, "EDC-lite")
    ontology.run_extraction(env, job_id)
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "paused"
    assert "LLM" in (row["error"] or "")
    types = [e["type"] for e in jobs.events_since(env, 0) if e["job_id"] == job_id]
    assert "job.autopaused" in types
    assert "ontology.done" not in types


def test_unconfigured_llm_is_offline_mode_not_failure(env, workdir):
    """No base_url/key at all: rule extraction is the intended path, job done."""
    _ingest(env, workdir)
    job_id = jobs.create_job(env, "ontology", 0, "EDC-lite")
    ontology.run_extraction(env, job_id)
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"


def test_batch_delete_candidates(env, workdir, fake_llm):
    """Multi-select delete: candidates + their mentions + outgoing relations
    go together; unknown ids ignored; dangling-target relations survive as
    ghosts (never silently destroyed)."""
    _ingest(env, workdir)
    _extract(env, fake_llm)
    ids = {r["name"]: r["id"] for r in env.execute(
        "SELECT id, name FROM candidates").fetchall()}
    # sanity: one relation 报销系统 -> 审批流程 exists
    assert env.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"] == 1
    n_mentions = env.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"]
    assert n_mentions > 0
    # delete 报销系统 (source of the relation) + a bogus id
    deleted = ontology.delete_candidates(env, [ids["报销系统"], 99999])
    assert deleted == 1  # unknown id ignored
    names = [r["name"] for r in env.execute(
        "SELECT name FROM candidates").fetchall()]
    assert "报销系统" not in names and "审批流程" in names
    # its mentions and outgoing relation went with it
    left = env.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"]
    assert left < n_mentions
    assert env.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"] == 0
    # empty list is a no-op
    assert ontology.delete_candidates(env, []) == 0


def test_resume_continues_from_checkpoint(env, workdir, fake_llm):
    """Regression: resume must NOT re-LLM already-extracted chunks.

    Old behaviour: resume_job reset progress_current to 0 and run_extraction
    walked all chunks from the start (LLM re-called per chunk, ~90s each on
    DeepSeek - a paused 592-chunk job lost ~16h of work on every resume)."""
    (workdir / "b.md").write_text(
        "报销系统也支持差旅报销。差旅报销需要审批流程。", encoding="utf-8")
    _ingest(env, workdir)
    calls = {"n": 0}

    def _count(prompt):
        calls["n"] += 1
        return EXTRACTION

    fake_llm["extraction"] = _count
    total = env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    assert total >= 2
    job_id = jobs.create_job(env, "ontology", total, "EDC-lite")
    # simulate: 1 chunk extracted, then the job paused (auto or manual)
    env.execute(
        "UPDATE jobs SET progress_current=1, status='paused' WHERE id=?",
        (job_id,))
    env.commit()
    assert jobs.resume_job(env, job_id)
    # resume keeps the checkpoint instead of resetting it to 0
    row = env.execute(
        "SELECT progress_current, status FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    assert row["progress_current"] == 1 and row["status"] == "running"
    ontology.run_extraction(env, job_id)
    # only chunks AFTER the checkpoint were LLM-extracted
    assert calls["n"] == total - 1
    row = env.execute(
        "SELECT status, progress_current FROM jobs WHERE id=?",
        (job_id,)).fetchone()
    assert row["status"] == "done" and row["progress_current"] == total
    # checkpointed chunk's candidates are not duplicated by the resume
    names = [r["name"] for r in env.execute(
        "SELECT name FROM candidates").fetchall()]
    assert names.count("报销系统") == 1


def test_graph_batching_keeps_data_identical(env, workdir, fake_llm):
    """graph() now batches mentions / relations / target lookups (was ~13k
    queries on a 4k library). Verify the batched result still matches the old
    naive per-row computation exactly — perf fix must not change data."""
    from app import ontology
    _ingest(env, workdir)
    _extract(env, fake_llm)
    g = ontology.graph(env)
    assert g["nodes"] and g["edges"]
    ids = {n["id"] for n in g["nodes"]}

    for n in g["nodes"]:
        naive = env.execute(
            "SELECT COUNT(*) c FROM mentions WHERE candidate_id=?",
            (n["id"],)).fetchone()["c"]
        assert n["mentions"] == naive          # batched GROUP BY == per-row count

    for e in g["edges"]:
        assert e["source"] in ids              # only visible sources
        tgt = env.execute(
            "SELECT id FROM candidates WHERE kind='entity' AND name=?"
            " ORDER BY id LIMIT 1", (e["target_name"],)).fetchone()
        assert e["target"] == (tgt["id"] if tgt else None)
        assert e["dangling"] == ((e["target"] not in ids) if e["target"] else True)

    # dangling target (a relation to a name with no entity candidate) stays visible
    env.execute(
        "INSERT INTO relations(source_id, target_name, relation_type, chunk_id)"
        " VALUES(?, '不存在的目标', '指向', NULL)", (sorted(ids)[0],))
    env.commit()
    g2 = ontology.graph(env)
    dangling = [e for e in g2["edges"] if e["target_name"] == "不存在的目标"]
    assert dangling and dangling[0]["dangling"] is True
    assert dangling[0]["target"] is None


def test_graph_batching_keeps_data_identical(env, workdir, fake_llm):
    """graph() now batches mentions / relations / target lookups (was ~13k
    queries on a 4k library). Verify the batched result still matches the old
    naive per-row computation exactly — perf fix must not change data."""
    from app import ontology
    _ingest(env, workdir)
    _extract(env, fake_llm)
    g = ontology.graph(env)
    assert g["nodes"] and g["edges"]
    ids = {n["id"] for n in g["nodes"]}

    for n in g["nodes"]:
        naive = env.execute(
            "SELECT COUNT(*) c FROM mentions WHERE candidate_id=?",
            (n["id"],)).fetchone()["c"]
        assert n["mentions"] == naive          # batched GROUP BY == per-row count

    for e in g["edges"]:
        assert e["source"] in ids              # only visible sources
        tgt = env.execute(
            "SELECT id FROM candidates WHERE kind='entity' AND name=?"
            " ORDER BY id LIMIT 1", (e["target_name"],)).fetchone()
        assert e["target"] == (tgt["id"] if tgt else None)
        assert e["dangling"] == ((e["target"] not in ids) if e["target"] else True)

    # dangling target (a relation to a name with no entity candidate) stays visible
    env.execute(
        "INSERT INTO relations(source_id, target_name, relation_type, chunk_id)"
        " VALUES(?, '不存在的目标', '指向', NULL)", (sorted(ids)[0],))
    env.commit()
    g2 = ontology.graph(env)
    dangling = [e for e in g2["edges"] if e["target_name"] == "不存在的目标"]
    assert dangling and dangling[0]["dangling"] is True
    assert dangling[0]["target"] is None
