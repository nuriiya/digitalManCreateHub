# -*- coding: utf-8 -*-
"""Job control (pause/resume/delete) + concurrency guard + LLM call events."""
import json

import pytest

from app import db, jobs, ingest, llm


LONG_DOC = "。".join(
    f"第{i}段：财务报销流程中，报销单据需要经过多级审批与预算核对" for i in range(80))


# ---------------- poll_control ----------------

def test_poll_control_pause(env):
    job_id = jobs.create_job(env, "ingest", 10, "x")
    assert jobs.request_pause(env, job_id) is True
    with pytest.raises(jobs.JobPaused):
        jobs.poll_control(env, job_id)
    row = env.execute("SELECT status, cancel_flag FROM jobs WHERE id=?",
                      (job_id,)).fetchone()
    assert row["status"] == "paused" and row["cancel_flag"] == 0
    evs = [e["type"] for e in jobs.events_since(env, 0, job_id=job_id)]
    assert "job.paused" in evs


def test_poll_control_delete_running(env):
    job_id = jobs.create_job(env, "ingest", 10, "x")
    assert jobs.request_delete(env, job_id) == "deleting"
    with pytest.raises(jobs.JobCancelled):
        jobs.poll_control(env, job_id)
    # job row AND its events are purged
    assert env.execute("SELECT COUNT(*) c FROM jobs WHERE id=?",
                       (job_id,)).fetchone()["c"] == 0
    assert env.execute("SELECT COUNT(*) c FROM events WHERE job_id=?",
                       (job_id,)).fetchone()["c"] == 0


def test_delete_finished_job_immediate(env):
    job_id = jobs.create_job(env, "ingest", 10, "x")
    jobs.finish_job(env, job_id, ok=True)
    assert jobs.request_delete(env, job_id) == "deleted"
    assert env.execute("SELECT COUNT(*) c FROM jobs WHERE id=?",
                       (job_id,)).fetchone()["c"] == 0


def test_delete_missing(env):
    assert jobs.request_delete(env, 999) == "missing"


# ---------------- pause/resume end-to-end on the ingest pipeline ----------------

def test_ingest_pause_then_resume(env, tmp_path, fake_llm):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "big.md").write_text(LONG_DOC, encoding="utf-8")
    job_id = jobs.create_job(env, "ingest", 0, str(d))

    calls = {"n": 0}

    def reply(messages):
        prompt = messages[-1]["content"]
        if "文档摘要助手" in prompt:
            calls["n"] += 1
            if calls["n"] == 1:
                jobs.request_pause(env, job_id)
            return '{"summary": "测试段摘要", "tags": ["测试"]}'
        return "测试整篇摘要"

    fake_llm["reply"] = reply

    # first run pauses at the chunk boundary after chunk #1
    with pytest.raises(jobs.JobPaused):
        ingest.ingest_workdir(env, job_id, str(d))
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (job_id,)).fetchone()["status"] == "paused"
    # partial doc is kept (chunks present, doc_summary empty) for re-ingest
    doc = env.execute("SELECT doc_summary FROM documents").fetchone()
    assert doc is not None and doc["doc_summary"] == ""
    assert env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] >= 1

    # resume: same job row re-runs; complete doc would be skipped, partial re-ingested
    assert jobs.resume_job(env, job_id) is True
    ingest.ingest_workdir(env, job_id, str(d))
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (job_id,)).fetchone()["status"] == "done"
    doc = env.execute("SELECT doc_summary FROM documents").fetchone()
    assert doc["doc_summary"]  # completed this time


def test_resume_only_from_recoverable_states(env):
    job_id = jobs.create_job(env, "ingest", 0, "x")
    assert jobs.resume_job(env, job_id) is False  # running -> not resumable
    jobs.finish_job(env, job_id, ok=False, error="boom")
    assert jobs.resume_job(env, job_id) is True   # failed -> resumable


# ---------------- LLM call events ----------------

def test_llm_notify_event(env):
    job_id = jobs.create_job(env, "ingest", 0, "x")
    jobs._threadlocal.job_id = job_id
    try:
        llm._notify({"model": "v4-flash", "prompt_len": 42,
                     "prompt_preview": "你是文档摘要助手…"})
        llm._notify({"model": "v4-flash", "reply_preview": "测试整篇摘要",
                     "type": "llm.reply"})
    finally:
        jobs._threadlocal.job_id = None
    evs = jobs.events_since(env, 0, job_id=job_id)
    by_type = {e["type"]: e["payload"] for e in evs}
    assert "llm.call" in by_type
    assert by_type["llm.call"]["prompt_preview"].startswith("你是文档摘要助手")
    assert "llm.reply" in by_type
    assert by_type["llm.reply"]["reply_preview"] == "测试整篇摘要"


def test_llm_notify_no_job_is_silent(env):
    jobs._threadlocal.job_id = None
    llm._notify({"model": "m"})  # must not raise / must not write
    assert env.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 0


def test_llm_notify_never_raises(env):
    job_id = jobs.create_job(env, "ingest", 0, "x")
    jobs._threadlocal.job_id = job_id
    try:
        llm._notify({"model": object()})  # unserializable payload
    finally:
        jobs._threadlocal.job_id = None


# ---------------- API-level: concurrency guard + control endpoints ----------------

def test_api_trigger_guard_409(env, workdir, fake_llm):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import settings_store
    settings_store.save_settings({"work_dir": str(workdir)})
    # a leftover running ingest job (e.g. double-triggered) must block a new one
    jobs.create_job(env, "ingest", 0, str(workdir))
    client = TestClient(app)
    r = client.post("/api/rag/ingest")
    assert r.status_code == 409
    assert "正在运行" in r.json()["error"]


def test_api_pause_resume_delete(env, workdir, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import settings_store
    settings_store.save_settings({"work_dir": str(workdir)})
    # resume normally spawns a bg thread; patch it out so the test stays
    # deterministic (we drive the pipeline synchronously below)
    monkeypatch.setattr(jobs, "run_in_background", lambda job_id, fn, *a: None)
    job_id = jobs.create_job(env, "ingest", 0, str(workdir))
    client = TestClient(app)

    # pause a running job
    r = client.post(f"/api/jobs/{job_id}/pause")
    assert r.status_code == 200
    with pytest.raises(jobs.JobPaused):
        jobs.poll_control(env, job_id)
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "paused"

    # events endpoint filters by job
    evs = client.get(f"/api/events?job_id={job_id}").json()["events"]
    assert evs and all(e["job_id"] == job_id for e in evs)

    # resume re-runs the pipeline synchronously here (thread runs in bg in prod)
    r = client.post(f"/api/jobs/{job_id}/resume")
    assert r.status_code == 200
    ingest.ingest_workdir(env, job_id, str(workdir))
    assert client.get("/api/jobs").json()["jobs"][0]["status"] == "done"

    # delete removes it entirely
    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 200 and r.json()["action"] == "deleted"
    assert client.get("/api/jobs").json()["jobs"] == []
    assert client.post("/api/jobs/999/pause").status_code == 400
    assert client.delete("/api/jobs/999").status_code == 404


# ---------------- stale-job recovery on startup ----------------

def test_recover_stale_jobs_creation_time_boundary(env):
    # a running job created BEFORE the restart (old process) -> failed
    old = jobs.create_job(env, "ingest", 0, "old process")
    # a running job created AFTER the restart (this process) -> untouched
    new = jobs.create_job(env, "ontology", 0, "this process")
    env.execute("UPDATE jobs SET created_at=? WHERE id=?", (1000.0, old))
    env.execute("UPDATE jobs SET created_at=? WHERE id=?", (5000.0, new))
    env.commit()
    assert jobs.recover_stale_jobs(env, started_at=3000.0) == 1
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (old,)).fetchone()["status"] == "failed"
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (new,)).fetchone()["status"] == "running"

    # paused jobs are user-paused, NOT stale — left resumable across restart
    paused = jobs.create_job(env, "exam", 0, "paused")
    env.execute("UPDATE jobs SET status='paused', created_at=? WHERE id=?",
                (1000.0, paused))
    env.commit()
    assert jobs.recover_stale_jobs(env, started_at=3000.0) == 0
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (paused,)).fetchone()["status"] == "paused"


def test_recover_stale_benchmark_updates_link_table(env):
    env.execute("INSERT INTO identities(name, created_at) VALUES('t', ?)",
                (db.now(),))
    ident_id = env.execute("SELECT id FROM identities").fetchone()["id"]
    job_id = jobs.create_job(env, "benchmark", 0, "bench", ref_id=ident_id)
    env.execute("UPDATE jobs SET created_at=? WHERE id=?", (1000.0, job_id))
    env.execute(
        "INSERT INTO persona_benchmarks"
        " (identity_id, job_id, model, judge, total, status, created_at)"
        " VALUES(?,?,?,?,?,'running',?)",
        (ident_id, job_id, "qwen2.5:7b-32k", "llm2/GLM", 15, db.now()))
    env.commit()
    assert jobs.recover_stale_jobs(env, started_at=3000.0) == 1
    assert env.execute("SELECT status FROM jobs WHERE id=?",
                       (job_id,)).fetchone()["status"] == "failed"
    b = env.execute("SELECT status, error FROM persona_benchmarks WHERE job_id=?",
                    (job_id,)).fetchone()
    assert b["status"] == "failed"
    assert b["error"] == "interrupted (service restart)"


# ---------------- mutex across a set of kinds ----------------

def test_active_job_in_set_empty_kinds(env):
    """Empty kind set = nothing to check; no row can ever match."""
    assert jobs.active_job_in_set(env, set()) is None
    assert jobs.active_job_in_set(env, []) is None


def test_active_job_in_set_returns_most_recent(env):
    """Among several running/paused jobs of different kinds in the set,
    the most recently created wins (so the user-facing message points to
    whichever task is actually blocking them)."""
    a = jobs.create_job(env, "ingest", 5, "first")
    b = jobs.create_job(env, "repair", 5, "second")
    c = jobs.create_job(env, "ontology", 5, "third")
    assert jobs.active_job_in_set(env, {"ingest", "repair", "ontology"})["id"] == c["id"]
    assert jobs.active_job_in_set(env, {"ingest"})["id"] == a["id"]
    assert jobs.active_job_in_set(env, {"repair"})["id"] == b["id"]


def test_active_job_in_set_excludes_other_kinds(env):
    """Kinds not in the set are ignored (e.g. identity job running must not
    block ingest/repair/ontology gating)."""
    jobs.create_job(env, "identity", 5, "nominate")
    assert jobs.active_job_in_set(env, {"ingest", "repair", "ontology"}) is None
    assert jobs.active_job_in_set(env, {"benchmark"}) is None


def test_active_job_in_set_excludes_finished(env):
    """Failed/cancelled/done jobs don't block (only running/paused do)."""
    job_id = jobs.create_job(env, "ontology", 5, "x")
    env.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
    env.commit()
    assert jobs.active_job_in_set(env, {"ingest", "repair", "ontology"}) is None


def test_active_job_in_set_catches_paused(env):
    """A paused ontology job still blocks ingest/repair (don't let the user
    start a fresh ingest while a paused one waits to resume)."""
    p = jobs.create_job(env, "ontology", 5, "x")
    jobs.request_pause(env, p["id"])
    hit = jobs.active_job_in_set(env, {"ingest", "repair", "ontology"})
    assert hit is not None and hit["id"] == p["id"]


# ---------------- latest_resumable_job ----------------

def test_latest_resumable_job_picks_paused(env):
    a = jobs.create_job(env, "ontology", 5, "first")
    b = jobs.create_job(env, "ontology", 5, "second")
    jobs.request_pause(env, b["id"])
    hit = jobs.latest_resumable_job(env, "ontology")
    assert hit is not None and hit["id"] == b["id"]


def test_latest_resumable_job_picks_failed_over_done(env):
    """Failed is resumable; done is not (it completed successfully)."""
    finished = jobs.create_job(env, "ontology", 5, "done")
    env.execute("UPDATE jobs SET status='done' WHERE id=?",
                (finished["id"],))
    failed = jobs.create_job(env, "ontology", 5, "fail")
    env.execute("UPDATE jobs SET status='failed' WHERE id=?",
                (failed["id"],))
    env.commit()
    hit = jobs.latest_resumable_job(env, "ontology")
    assert hit is not None and hit["id"] == failed["id"]


def test_latest_resumable_job_running_not_resumable(env):
    """A still-running job is not resumable (it IS the running thing)."""
    r = jobs.create_job(env, "ontology", 5, "x")
    assert jobs.latest_resumable_job(env, "ontology") is None
    # and still blocks the gate
    assert jobs.active_job_in_set(env, {"ingest", "repair", "ontology"})["id"] == r["id"]


def test_latest_resumable_job_other_kinds_ignored(env):
    """An identity paused job must not be picked as a resumable ontology job."""
    jobs.create_job(env, "identity", 5, "x")
    other = jobs.create_job(env, "identity", 5, "y")
    jobs.request_pause(env, other["id"])
    assert jobs.latest_resumable_job(env, "ontology") is None
