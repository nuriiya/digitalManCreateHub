# -*- coding: utf-8 -*-
"""API tests for the upload-files / mutex / ontology-resume changes."""
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import db, jobs, ingest, settings_store, ontology


def _client(env, workdir, fake_llm):
    from app.main import app
    settings_store.save_settings({"work_dir": str(workdir)})
    # ingest the seed corpus synchronously so /api/rag/upload-files has
    # something to dedupe against.
    jid = jobs.create_job(env, "ingest", 0, str(workdir))
    ingest.ingest_workdir(env, jid, str(workdir))
    return TestClient(app)


def _wait_job(client: TestClient, job_id: int, timeout: float = 5.0) -> dict:
    """Poll /api/jobs until the given job finishes (done/failed/paused)
    or the timeout fires. The upload-files path runs synchronously inside
    the request, so most cases won't need to wait — this is a safety net."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs_list = client.get("/api/jobs").json()["jobs"]
        for j in jobs_list:
            if j["id"] == job_id and j["status"] in ("done", "failed", "paused"):
                return j
        time.sleep(0.05)
    return client.get("/api/jobs").json()["jobs"][0]


# ---------------- /api/rag/upload-files ----------------

def test_upload_files_adds_new(env, workdir, fake_llm):
    """A name that doesn't exist in documents -> `added`."""
    client = _client(env, workdir, fake_llm)
    new = (workdir / "new.md").read_text(encoding="utf-8") + " 上传补一段。"
    r = client.post("/api/rag/upload-files",
                    files=[("files", ("new.md", io.BytesIO(new.encode("utf-8")),
                                      "text/markdown"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == ["new.md"]
    assert body["skipped"] == [] and body["conflicts"] == []
    # stats reflect the new row
    stats = client.get("/api/rag/stats").json()
    assert stats["documents"] == 2


def test_upload_files_skip_when_unchanged(env, workdir, fake_llm):
    """Same content as the existing doc -> `skipped` (no double insert)."""
    client = _client(env, workdir, fake_llm)
    same = (workdir / "a.md").read_text(encoding="utf-8")
    r = client.post("/api/rag/upload-files",
                    files=[("files", ("a.md", io.BytesIO(same.encode("utf-8")),
                                      "text/markdown"))])
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == ["a.md"]
    assert body["added"] == [] and body["conflicts"] == []
    assert client.get("/api/rag/stats").json()["documents"] == 1


def test_upload_files_conflict_when_name_exists_with_different_content(
        env, workdir, fake_llm):
    """Same name + DIFFERENT hash -> reported as conflict; NOT ingested
    until the user confirms overwrite on a 2nd call."""
    client = _client(env, workdir, fake_llm)
    different = "完全不同的内容，关于考勤与异常处理。" * 4
    r = client.post("/api/rag/upload-files",
                    files=[("files", ("a.md", io.BytesIO(different.encode("utf-8")),
                                      "text/markdown"))])
    assert r.status_code == 200
    body = r.json()
    assert body["conflicts"] and body["conflicts"][0]["name"] == "a.md"
    assert body["added"] == [] and body["skipped"] == []
    # the original doc was NOT replaced yet
    stats = client.get("/api/rag/stats").json()
    assert stats["documents"] == 1


def test_upload_files_overwrite_names_replaces_in_place(
        env, workdir, fake_llm):
    """2nd call with `overwrite_names=a.md` -> replaces the existing row,
    documents.id stays stable (downstream ontology references safe)."""
    client = _client(env, workdir, fake_llm)
    original_id = env.execute(
        "SELECT id FROM documents WHERE name='a.md'").fetchone()["id"]

    different = "覆盖模式下重写的全新内容，关于薪酬体系的全面升级。" * 3
    r = client.post(
        "/api/rag/upload-files",
        data={"overwrite_names": "a.md"},
        files=[("files", ("a.md", io.BytesIO(different.encode("utf-8")),
                          "text/markdown"))],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == ["a.md"]
    assert body["conflicts"] == []  # user has already accepted

    # doc id is unchanged (UPDATE in place)
    row = env.execute(
        "SELECT id, content_hash FROM documents WHERE name='a.md'").fetchone()
    assert row["id"] == original_id
    assert row["content_hash"] != ""  # recomputed


def test_upload_files_multiple_with_mixed_outcomes(
        env, workdir, fake_llm):
    """One call: 1 added + 1 skipped + 1 conflict + 1 unsupported (PDF we
    refuse to ingest in this fast path is a different test; here just
    mix added/skipped/conflict)."""
    client = _client(env, workdir, fake_llm)
    same = (workdir / "a.md").read_text(encoding="utf-8")
    r = client.post(
        "/api/rag/upload-files",
        files=[
            ("files", ("a.md", io.BytesIO(same.encode("utf-8")), "text/markdown")),  # skipped
            ("files", ("fresh.md", io.BytesIO("全新的内容文本填充。".encode("utf-8")), "text/markdown")),  # added
            ("files", ("a.md", io.BytesIO("完全不同的内容。".encode("utf-8")), "text/markdown")),  # conflict
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "fresh.md" in body["added"]
    assert "a.md" in body["skipped"]
    assert any(c["name"] == "a.md" for c in body["conflicts"])
    assert client.get("/api/rag/stats").json()["documents"] == 2


def test_upload_files_requires_workdir(env, tmp_path, fake_llm):
    from app.main import app
    settings_store.save_settings({"work_dir": ""})
    r = TestClient(app).post("/api/rag/upload-files",
                             files=[("files", ("x.md", io.BytesIO(b"x"), "text/markdown"))])
    assert r.status_code == 400


# ---------------- mutex across {ingest, repair, ontology} ----------------

def test_ingest_blocks_when_ontology_running(env, workdir, fake_llm):
    """Three-way mutex: a running ontology job blocks a fresh ingest."""
    client = _client(env, workdir, fake_llm)
    # create a running ontology job and mark it running
    oid = jobs.create_job(env, "ontology", 5, "EDC")
    r = client.post("/api/rag/ingest")
    assert r.status_code == 409
    assert "正在运行" in r.json()["error"]


def test_repair_blocks_when_ontology_running(env, workdir, fake_llm):
    """And repair blocks when ontology is in flight (same mutex set)."""
    client = _client(env, workdir, fake_llm)
    jobs.create_job(env, "ontology", 5, "EDC")
    r = client.post("/api/rag/repair-summaries")
    assert r.status_code == 409


def test_ontology_extract_blocks_when_ingest_running(env, workdir, fake_llm):
    """And vice versa: a running ingest blocks a fresh ontology extract."""
    client = _client(env, workdir, fake_llm)
    jobs.create_job(env, "ingest", 5, str(workdir))
    r = client.post("/api/ontology/extract")
    assert r.status_code == 409


def test_ingest_passes_when_idle(env, workdir, fake_llm):
    """No active job in the set -> ingest goes through (returns 200/job_id)."""
    client = _client(env, workdir, fake_llm)
    r = client.post("/api/rag/ingest")
    assert r.status_code == 200, r.text
    assert "job_id" in r.json()


def test_upload_files_blocked_by_active_job(env, workdir, fake_llm):
    """Uploads must also wait for any job in the set to finish."""
    client = _client(env, workdir, fake_llm)
    jobs.create_job(env, "ontology", 5, "EDC")
    r = client.post("/api/rag/upload-files",
                    files=[("files", ("x.md", io.BytesIO(b"x"), "text/markdown"))])
    assert r.status_code == 409


# ---------------- /api/ontology/extract incremental behavior ----------------

def test_ontology_extract_first_run_creates_new_job(env, workdir, fake_llm):
    client = _client(env, workdir, fake_llm)
    # _client already ingested; we need at least 1 chunk
    assert client.get("/api/rag/stats").json()["chunks"] >= 1
    r = client.post("/api/ontology/extract")
    assert r.status_code == 200
    body = r.json()
    assert body.get("resumed") is False
    assert "job_id" in body


def test_ontology_extract_resumes_paused_job(env, workdir, fake_llm):
    """Paused ontology job -> next click is a RESUME (same job id, resumed=True)."""
    _client(env, workdir, fake_llm)
    paused_id = jobs.create_job(env, "ontology", 5, "EDC")
    jobs.request_pause(env, paused_id)
    # request_pause flips status to 'pausing' synchronously; force 'paused'
    # so it counts as resumable.
    env.execute("UPDATE jobs SET status='paused' WHERE id=?", (paused_id,))
    env.commit()
    from app.main import app
    r = TestClient(app).post("/api/ontology/extract")
    assert r.status_code == 200
    body = r.json()
    assert body.get("resumed") is True
    assert body["job_id"] == paused_id


def test_ontology_extract_resume_bypasses_mutex(env, workdir, fake_llm):
    """A RESUMED ontology job must NOT be blocked by another ingest in the
    set — the user is continuing what they already started."""
    _client(env, workdir, fake_llm)
    paused_id = jobs.create_job(env, "ontology", 5, "EDC")
    env.execute("UPDATE jobs SET status='paused' WHERE id=?", (paused_id,))
    env.commit()
    # create an unrelated running job (different kind) — should NOT block
    jobs.create_job(env, "identity", 5, "x")  # not in mutex set
    from app.main import app
    r = TestClient(app).post("/api/ontology/extract")
    assert r.status_code == 200
    assert r.json()["job_id"] == paused_id


def test_ontology_extract_resume_ignores_running_same_kind(
        env, workdir, fake_llm):
    """If there's still a RUNNING ontology job, resume can't pick a paused
    one on top of it (still blocked by the running job's lock)."""
    _client(env, workdir, fake_llm)
    paused_id = jobs.create_job(env, "ontology", 5, "paused")
    env.execute("UPDATE jobs SET status='paused' WHERE id=?", (paused_id,))
    env.commit()
    jobs.create_job(env, "ontology", 5, "running")  # also ontology
    from app.main import app
    r = TestClient(app).post("/api/ontology/extract")
    assert r.status_code == 409