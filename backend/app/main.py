# -*- coding: utf-8 -*-
"""rag_mvp backend entry: FastAPI app + REST + WS event stream + static dist."""
import asyncio
import hashlib
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db, jobs, settings_store, ingest, loaders, ontology, orchestration, assembly, llm, embedding, identity, chat, auth, mcp, actions, pipeline


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Job kinds that touch the documents/chunks tables: must not run
# concurrently with each other. (Mutex is per-kind by default; we extend
# to a set so e.g. an ontology job blocks a fresh ingest and vice versa.)
_MUTEX_KINDS = {"ingest", "repair", "ontology"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = db.get_conn()
    auth.ensure_admin(conn)
    recovered = jobs.recover_stale_jobs(conn, db.now())
    if recovered:
        jobs.emit(conn, None, "system.recovered", {"jobs": recovered})
    yield


app = FastAPI(title="rag-mvp", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# ---------------- auth ----------------
# Every /api/* route requires a valid bearer token except the login endpoint.
# Static frontend files (non-/api) stay open so the login page can load.
_PUBLIC_API_PATHS = {"/api/auth/login"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in _PUBLIC_API_PATHS:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        user = auth.verify_token(token)
        if not user:
            return JSONResponse({"detail": "未认证或登录已过期"}, status_code=401)
        request.state.username, request.state.role = user
    return await call_next(request)


# ---------------- auth routes ----------------

class LoginBody(BaseModel):
    username: str
    password: str


class PasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    conn = db.get_conn()
    user = auth.authenticate(conn, body.username, body.password)
    if not user:
        return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
    token = auth.issue_token(user["username"], user["role"])
    return {"token": token, "username": user["username"], "role": user["role"]}


@app.post("/api/auth/password")
def auth_change_password(body: PasswordBody, request: Request):
    if len(body.new_password) < 6:
        return JSONResponse({"detail": "新密码至少 6 位"}, status_code=400)
    conn = db.get_conn()
    ok = auth.change_password(conn, request.state.username,
                              body.old_password, body.new_password)
    if not ok:
        return JSONResponse({"detail": "原密码错误"}, status_code=400)
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout():
    # Stateless tokens: logout is a client-side drop. Endpoint kept for symmetry.
    return {"ok": True}


# ---------------- mcp sandbox ----------------

class McpCreate(BaseModel):
    name: str
    description: str = ""
    transport: str = "http"
    image: str = ""
    command: str = ""
    port: int = 0


@app.get("/api/mcp/servers")
def mcp_list():
    return {"servers": mcp.list_servers(db.get_conn())}


@app.post("/api/mcp/servers")
def mcp_create(body: McpCreate):
    ok, msg = mcp.create_server(db.get_conn(), body.name, body.description,
                                body.transport, body.image, body.command, body.port)
    if not ok:
        return JSONResponse({"detail": msg}, status_code=400)
    return {"ok": True, "id": int(msg)}


@app.delete("/api/mcp/servers/{server_id}")
def mcp_delete(server_id: int):
    mcp.delete_server(db.get_conn(), server_id)
    return {"ok": True}


@app.post("/api/mcp/servers/{server_id}/start")
def mcp_start(server_id: int):
    ok, msg = mcp.start_server(db.get_conn(), server_id)
    if not ok:
        return JSONResponse({"detail": msg}, status_code=400)
    return {"ok": True, "container": msg}


@app.post("/api/mcp/servers/{server_id}/stop")
def mcp_stop(server_id: int):
    mcp.stop_server(db.get_conn(), server_id)
    return {"ok": True}


# ---------------- settings ----------------

class LlmPatch(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class EmbedPatch(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    dim: int | None = None


class SettingsPatch(BaseModel):
    work_dir: str | None = None
    llm: LlmPatch | None = None
    llm2: LlmPatch | None = None
    embedding: EmbedPatch | None = None


@app.get("/api/settings")
def get_settings():
    s = settings_store.load_settings()
    return {
        "settings": settings_store.mask_settings(s),
        "llm_configured": llm.llm_configured(),
        "llm2_configured": llm.llm2_configured(),
        "llm_errors": settings_store.validate_llm_config(s["llm"]) if not llm.llm_configured() else [],
        "llm2_errors": settings_store.validate_llm_config(s["llm2"]) if not llm.llm2_configured() else [],
    }


@app.put("/api/settings")
def put_settings(patch: SettingsPatch):
    merged = settings_store.save_settings(patch.model_dump(exclude_none=True))
    return {"settings": settings_store.mask_settings(merged),
            "llm_configured": llm.llm_configured(),
            "llm2_configured": llm.llm2_configured()}


@app.post("/api/settings/test-llm")
def test_llm():
    """Real connectivity test against the configured V4-Flash endpoint."""
    s = settings_store.load_settings()["llm"]
    errors = settings_store.validate_llm_config(s)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
    try:
        out = llm.chat([{"role": "user", "content": "回复两个字：连通"}], temperature=0.0)
        return {"ok": True, "reply": (out or "").strip()[:50], "model": s["model"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/settings/test-llm2")
def test_llm2():
    """Real connectivity test against the configured GLM (judge) endpoint."""
    s = settings_store.load_settings()["llm2"]
    errors = settings_store.validate_llm_config(s)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
    try:
        out = llm.chat2([{"role": "user", "content": "回复两个字：连通"}], temperature=0.0)
        return {"ok": True, "reply": (out or "").strip()[:50], "model": s["model"]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.post("/api/settings/test-embedding")
def test_embedding():
    s = settings_store.load_settings()["embedding"]
    if s["provider"] != "ollama":
        return {"ok": True, "provider": "hash", "note": "本地 hash 降级模式，零依赖"}
    return embedding.check_ollama(s["base_url"], s["model"])


# ---------------- jobs & events ----------------

@app.post("/api/rag/ingest")
def trigger_ingest(path: str | None = None):
    """Trigger a scan-and-ingest job. By default scans settings.work_dir;
    an explicit `path` query parameter overrides it (used by the upload
    dialog when the user picks a custom folder via webkitdirectory).
    Concurrency: blocked by any running/paused ingest/repair/ontology job."""
    s = settings_store.load_settings()
    target = path or s["work_dir"]
    if not target:
        return JSONResponse({"error": "work_dir not set"}, status_code=400)
    conn = db.get_conn()
    active = jobs.active_job_in_set(conn, _MUTEX_KINDS)
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发，避免并发写库冲突"},
            status_code=409)
    job_id = jobs.create_job(conn, "ingest", total=0, detail=target)
    jobs.run_in_background(job_id, ingest.ingest_workdir, target)
    return {"job_id": job_id}


@app.post("/api/rag/repair-summaries")
def trigger_repair():
    """Re-run LLM summaries for chunks stuck with rule-fallback quality
    (e.g. network-blip victims of the old silent-degrade code)."""
    conn = db.get_conn()
    if not llm.llm_configured():
        return JSONResponse({"error": "LLM 未配置 - 规则兜底是设计行为，无需修复"},
                            status_code=400)
    active = jobs.active_job_in_set(conn, _MUTEX_KINDS)
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "repair", total=0, detail="rule-fallback 修复")
    jobs.run_in_background(job_id, ingest.run_summary_repair)
    return {"job_id": job_id}


@app.post("/api/ontology/extract")
def trigger_ontology():
    """Start (or RESUME) the ontology extraction task.

    User-decided 2026-09-04 semantics:
      - If a paused / failed / cancelled ontology job exists, this click
        is a RESUME: progress_current is preserved, the next not-yet-seen
        chunk gets picked up. We bypass the mutex gate for the resume
        branch (the job was already running before, the user is just
        continuing what they started).
      - Otherwise create a new job sized to the current chunks table.
      - Concurrency: a still-RUNNING ontology job blocks (409); any
        running ingest/repair also blocks (409) — fresh ingest must wait
        for ontology to drain, otherwise the chunk set changes underneath
        the extractor.
    """
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "no chunks - run ingest first"}, status_code=400)

    resumable = jobs.latest_resumable_job(conn, "ontology")
    if resumable:
        # RESUME path: bypass the mutex gate. The job is already known
        # to be in 'paused' / 'failed' / 'cancelled' state so it can't be
        # the running one blocking the set. We just spin up the worker
        # again with the same id so progress persists.
        if not jobs.resume_job(conn, resumable["id"]):
            return JSONResponse(
                {"error": f"无法续跑本体任务 #{resumable['id']}（状态：{resumable['status']}）"},
                status_code=400)
        jobs.run_in_background(resumable["id"], ontology.run_extraction)
        return {"job_id": resumable["id"], "resumed": True}

    # FRESH job path: enforce the three-way mutex.
    active = jobs.active_job_in_set(conn, _MUTEX_KINDS)
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "ontology", total=n, detail="EDC-lite extraction")
    jobs.run_in_background(job_id, ontology.run_extraction)
    return {"job_id": job_id, "resumed": False}


# ---------------- one-click pipeline (数字人创建台) ----------------

class PipelineBody(BaseModel):
    path: str | None = None
    identity_id: int | None = None


def run_pipeline(conn, job_id: int, path: str, identity_id: int | None) -> None:
    """一键流水线：添加资料 → 本体提取 → 装配，三步串行（各自是子 job）。

    Each step spawns a real sub-job and waits for it; a step that does not
    reach 'done' aborts the pipeline with a step-scoped error. If no persona
    is given, step 3 picks the first approved one (or skips with a notice)."""
    # step 1: 添加资料
    jobs.update_progress(conn, job_id, 0, 3)
    jobs.emit(conn, job_id, "pipeline.step", {"step": 1, "name": "添加资料", "status": "running"})
    ing_job = jobs.create_job(conn, "ingest", 0, detail=path, parent_id=job_id)
    jobs.run_in_background(ing_job, ingest.ingest_workdir, path)
    st = jobs.wait_job(conn, ing_job)
    if st != "done":
        jobs.finish_job(conn, job_id, ok=False, error=f"步骤1「添加资料」{st}")
        return

    # step 2: 本体提取
    jobs.update_progress(conn, job_id, 1, 3)
    jobs.emit(conn, job_id, "pipeline.step", {"step": 2, "name": "本体提取", "status": "running"})
    n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    ont_job = jobs.create_job(conn, "ontology", n, detail="EDC-lite extraction", parent_id=job_id)
    jobs.run_in_background(ont_job, ontology.run_extraction)
    st = jobs.wait_job(conn, ont_job)
    if st != "done":
        jobs.finish_job(conn, job_id, ok=False, error=f"步骤2「本体提取」{st}")
        return

    # step 3: 装配
    if identity_id is None:
        row = conn.execute(
            "SELECT id FROM identities WHERE status='approved' ORDER BY id LIMIT 1"
        ).fetchone()
        identity_id = row["id"] if row else None
    if identity_id is None:
        jobs.emit(conn, job_id, "pipeline.step",
                  {"step": 3, "name": "装配", "status": "skipped",
                   "reason": "无已批准数字人"})
        jobs.update_progress(conn, job_id, 3, 3)
        jobs.finish_job(conn, job_id, ok=True)
        return
    jobs.update_progress(conn, job_id, 2, 3)
    jobs.emit(conn, job_id, "pipeline.step", {"step": 3, "name": "装配", "status": "running"})
    asm_job = jobs.create_job(conn, "assemble", 0, detail="数字人本体装配",
                              ref_id=identity_id, parent_id=job_id)
    jobs.run_in_background(asm_job, assembly.run_assembly, identity_id)
    st = jobs.wait_job(conn, asm_job)
    if st != "done":
        jobs.finish_job(conn, job_id, ok=False, error=f"步骤3「装配」{st}")
        return

    jobs.update_progress(conn, job_id, 3, 3)
    jobs.finish_job(conn, job_id, ok=True)


@app.post("/api/ontology/pipeline")
def trigger_pipeline(body: PipelineBody):
    """一键流水线入口（数字人创建台）：添加资料 → 本体提取 → 装配。"""
    conn = db.get_conn()
    s = settings_store.load_settings()
    path = body.path or s["work_dir"]
    if not path:
        return JSONResponse({"error": "work_dir 未设置"}, status_code=400)
    active = jobs.active_job_in_set(conn, _MUTEX_KINDS | {"assemble", "pipeline"})
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），请先等待完成"},
            status_code=409)
    job_id = jobs.create_job(conn, "pipeline", 3,
                             detail="一键流水线：添加资料 → 本体提取 → 装配",
                             ref_id=body.identity_id)
    jobs.run_in_background(job_id, run_pipeline, path, body.identity_id)
    return {"job_id": job_id}


@app.post("/api/rag/upload-files")
async def rag_upload_files(
    files: list[UploadFile] = File(...),
    overwrite_names: str = Form(default=""),
):
    """Receive uploaded files (drop-zone + webkitdirectory both feed this),
    save each into settings.work_dir (uuid-suffixed name to avoid stomping
    on an existing file with the same basename), and per-file ingest with
    the SAME name-dedup rule as the scan-and-ingest path.

    Two-step interaction:
      - 1st call (overwrite_names empty): for each file compute content_hash
        and look up by name. Files that match (same hash + complete) are
        classified `skipped`; files whose name exists but hash differs are
        classified `conflict` (NOT ingested). Files whose name is new are
        ingested and classified `added`. Response carries the lists so the
        UI can render a per-file log and a confirmation dialog.
      - 2nd call (overwrite_names = CSV of user-confirmed conflicts): the
        client posts the same files AGAIN with the names the user agreed
        to overwrite; this call ingests them in REPLACE-IN-PLACE mode
        (keeps documents.id stable).
    """
    s = settings_store.load_settings()
    work_dir = s["work_dir"]
    if not work_dir:
        return JSONResponse({"error": "work_dir not set"}, status_code=400)
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    overwrite_set = {n.strip() for n in overwrite_names.split(",") if n.strip()}

    # Three-way mutex: don't pour new chunks into a DB another job is writing.
    conn = db.get_conn()
    active = jobs.active_job_in_set(conn, _MUTEX_KINDS)
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再上传"},
            status_code=409)

    added: list[str] = []
    skipped: list[str] = []
    conflicts: list[dict] = []  # [{name, reason}] - awaiting user decision
    errors: list[dict] = []

    job_id = jobs.create_job(conn, "ingest", total=len(files),
                             detail=f"upload-files · {len(files)} files")
    try:
        jobs.set_current_job(job_id)
        for f in files:
            raw = await f.read()
            raw_name = Path(f.filename or "upload").name  # strip any path parts
            # Save with uuid suffix to avoid clobbering the existing copy in
            # work_dir; the dedup check below is by NAME (not path), so the
            # suffix doesn't matter — we always look up the row by raw_name.
            stamp = uuid.uuid4().hex[:8]
            target = work_path / f"{stamp}_{raw_name}"
            target.write_bytes(raw)
            try:
                doc = loaders.load_text(target)
            except Exception as e:
                errors.append({"name": raw_name, "error": str(e)})
                target.unlink(missing_ok=True)
                jobs.emit(conn, job_id, "ingest.file_error",
                          {"file": raw_name, "error": str(e)})
                continue

            chash = _sha256(doc["text"])
            existing = conn.execute(
                "SELECT id, content_hash, doc_summary FROM documents WHERE name=?",
                (raw_name,)).fetchone()

            if existing and existing["content_hash"] == chash \
                    and (existing["doc_summary"] or "").strip():
                # Identical, complete: skip (file_unchanged).
                target.unlink(missing_ok=True)
                skipped.append(raw_name)
                jobs.emit(conn, job_id, "ingest.file_unchanged",
                          {"file": raw_name, "doc_id": existing["id"]})
                continue

            if existing and raw_name not in overwrite_set:
                # Name conflict: don't ingest until the user decides.
                # Drop the temp copy; the second call will re-save + ingest.
                target.unlink(missing_ok=True)
                conflicts.append({"name": raw_name, "doc_id": existing["id"],
                                  "reason": "already_extracted_different_content"})
                jobs.emit(conn, job_id, "ingest.file_conflict",
                          {"file": raw_name, "doc_id": existing["id"]})
                continue

            # Either: no existing row, OR user has confirmed `overwrite` for it.
            status, _doc_id, _n = ingest._ingest_one_file(conn, job_id, doc, 0)
            if status == "error":
                errors.append({"name": raw_name, "error": "ingest failed (see event log)"})
                target.unlink(missing_ok=True)
            elif status == "replaced":
                added.append(raw_name)
                jobs.emit(conn, job_id, "ingest.file_overwritten",
                          {"file": raw_name})
            else:  # "added"
                added.append(raw_name)
            # cleanup the temp copy on disk now that ingest has the chunks
            target.unlink(missing_ok=True)

        jobs.update_progress(conn, job_id, len(files))
        jobs.emit(conn, job_id, "ingest.upload_done",
                  {"added": added, "skipped": skipped,
                   "conflicts": conflicts, "errors": errors,
                   "overwrite_applied": bool(overwrite_set)})
        jobs.finish_job(conn, job_id, ok=True)
        return {
            "job_id": job_id,
            "added": added,
            "skipped": skipped,
            "conflicts": conflicts,
            "errors": errors,
        }
    except jobs.JobPaused:
        jobs.auto_pause(conn, job_id, "upload-files 被用户暂停")
        raise
    except Exception as e:  # noqa: BLE001
        jobs.emit(conn, job_id, "ingest.upload_error", {"error": str(e)})
        jobs.finish_job(conn, job_id, ok=False, error=str(e))
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        jobs.set_current_job(None)


@app.get("/api/jobs")
def list_jobs():
    rows = db.get_conn().execute(
        "SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    return {"jobs": [jobs.job_to_dict(r) for r in rows]}


# ---------------- job control: pause / resume / delete ----------------

@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: int):
    if not jobs.request_pause(db.get_conn(), job_id):
        return JSONResponse({"error": "job not found or not running"}, status_code=400)
    return {"ok": True, "id": job_id, "status": "pausing"}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: int):
    conn = db.get_conn()
    if not jobs.resume_job(conn, job_id):
        return JSONResponse(
            {"error": "job not found or not in paused/failed/cancelled state"},
            status_code=400)
    row = conn.execute("SELECT kind, detail, ref_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row["kind"] == "ingest":
        if not row["detail"]:
            return JSONResponse({"error": "job detail (work_dir) missing"}, status_code=400)
        jobs.run_in_background(job_id, ingest.ingest_workdir, row["detail"])
    elif row["kind"] == "repair":
        jobs.run_in_background(job_id, ingest.run_summary_repair)
    elif row["kind"] == "ontology":
        jobs.run_in_background(job_id, ontology.run_extraction)
    elif row["kind"] == "identity":
        jobs.run_in_background(job_id, identity.run_nomination)
    elif row["kind"] == "exam":
        jobs.run_in_background(job_id, ontology.run_exam)
    elif row["kind"] == "orchestrate":
        jobs.run_in_background(job_id, orchestration.run_glm_orchestration)
    elif row["kind"] == "assemble":
        jobs.run_in_background(job_id, assembly.run_assembly, row["ref_id"])
    else:
        return JSONResponse({"error": f"unknown job kind: {row['kind']}"}, status_code=400)
    return {"ok": True, "id": job_id, "status": "running"}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    action = jobs.request_delete(db.get_conn(), job_id)
    if action == "missing":
        return JSONResponse({"error": "job not found"}, status_code=404)
    return {"ok": True, "id": job_id, "action": action}


@app.get("/api/events")
def get_events(since: int = 0, job_id: int | None = None):
    return {"events": jobs.events_since(db.get_conn(), since, job_id=job_id)}


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket, since: int = 0, token: str = ""):
    """Reconnect-safe event stream: client sends last seq, we push increments."""
    if not auth.verify_token(token):
        await ws.close(code=4401)
        return
    await ws.accept()
    last = since
    try:
        while True:
            events = jobs.events_since(db.get_conn(), last)
            if events:
                last = events[-1]["seq"]
                await ws.send_text(json.dumps({"events": events}, ensure_ascii=False))
            else:
                # allow client to update last seq mid-stream
                try:
                    msg = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
                    data = json.loads(msg)
                    if "since" in data:
                        last = int(data["since"])
                except asyncio.TimeoutError:
                    pass
    except WebSocketDisconnect:
        return


# ---------------- rag preview ----------------

@app.get("/api/rag/stats")
def rag_stats():
    conn = db.get_conn()
    out = ingest.stats(conn)
    out.update(ontology.approve_all_stats(conn))
    return out


@app.get("/api/rag/documents")
def rag_documents():
    return {"documents": ingest.list_documents(db.get_conn())}


@app.get("/api/rag/chunks")
def rag_chunks(page: int = 1, page_size: int = 20, doc_id: int | None = None):
    return ingest.list_chunks(db.get_conn(), page, page_size, doc_id)


@app.get("/api/rag/chunks/{chunk_id}")
def rag_chunk(chunk_id: int):
    c = ingest.get_chunk(db.get_conn(), chunk_id)
    if not c:
        return JSONResponse({"error": "not found"}, status_code=404)
    return c


@app.delete("/api/rag/documents/{doc_id}")
def rag_delete_document(doc_id: int):
    if not ingest.delete_document(db.get_conn(), doc_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "rag.document_deleted", {"id": doc_id})
    return {"ok": True, "id": doc_id}


@app.delete("/api/rag/chunks/{chunk_id}")
def rag_delete_chunk(chunk_id: int):
    if not ingest.delete_chunks(db.get_conn(), [chunk_id]):
        return JSONResponse({"error": "not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "rag.chunk_deleted", {"ids": [chunk_id]})
    return {"ok": True, "id": chunk_id}


class ChunkIds(BaseModel):
    ids: list[int]


@app.post("/api/rag/chunks/batch-delete")
def rag_batch_delete_chunks(body: ChunkIds):
    n = ingest.delete_chunks(db.get_conn(), body.ids)
    jobs.emit(db.get_conn(), None, "rag.chunk_deleted", {"count": n})
    return {"ok": True, "deleted": n}


class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    tag: str | None = None


@app.post("/api/rag/search")
def rag_search(q: SearchQuery):
    return {"results": ingest.search(db.get_conn(), q.query, q.top_k, q.tag)}


# ---------------- ontology graph & approval ----------------

@app.get("/api/ontology/graph")
def ontology_graph():
    return ontology.graph(db.get_conn())


@app.get("/api/ontology/tags")
def ontology_tags():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT t, COUNT(*) c FROM (SELECT unnest(tags) t FROM candidates)"
        " x GROUP BY t ORDER BY c DESC, t").fetchall()
    used = [{"tag": r["t"], "count": r["c"]} for r in rows]
    return {"preset": sorted(ontology.TAG_PRESET), "used": used}


@app.get("/api/ontology/candidates/{candidate_id}")
def ontology_candidate(candidate_id: int):
    d = ontology.candidate_detail(db.get_conn(), candidate_id)
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


class StatusPatch(BaseModel):
    status: str


@app.post("/api/ontology/candidates/{candidate_id}/status")
def ontology_set_status(candidate_id: int, patch: StatusPatch):
    if not ontology.set_status(db.get_conn(), candidate_id, patch.status):
        return JSONResponse({"error": "invalid status or id"}, status_code=400)
    jobs.emit(db.get_conn(), None, "ontology.candidate_updated",
              {"id": candidate_id, "status": patch.status})
    return {"ok": True}


class MergePatch(BaseModel):
    into: int


@app.post("/api/ontology/candidates/{candidate_id}/merge")
def ontology_merge(candidate_id: int, patch: MergePatch):
    if not ontology.merge_candidate(db.get_conn(), candidate_id, patch.into):
        return JSONResponse({"error": "merge failed (ids must exist and differ)"},
                            status_code=400)
    jobs.emit(db.get_conn(), None, "ontology.candidate_merged",
              {"id": candidate_id, "into": patch.into})
    return {"ok": True}


class CandidateIds(BaseModel):
    ids: list[int]


@app.post("/api/ontology/candidates/batch-delete")
def ontology_batch_delete(body: CandidateIds):
    """Multi-select housekeeping: hard-delete nominated candidates (and their
    evidence + outgoing relations) in one call."""
    if not body.ids:
        return JSONResponse({"error": "ids must be non-empty"}, status_code=400)
    n = ontology.delete_candidates(db.get_conn(), body.ids)
    jobs.emit(db.get_conn(), None, "ontology.candidates_deleted",
              {"ids": body.ids, "count": n})
    return {"ok": True, "deleted": n}


# ---------------- identity pre-screening (anchors) ----------------

@app.post("/api/ontology/identities/nominate")
def trigger_identity_nomination():
    """Identity nomination job: high-freq stats (0 LLM) -> one LLM call ->
    3~5 identities x anchors, all stored pending for user approval."""
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "no chunks - run ingest first"}, status_code=400)
    active = jobs.active_job(conn, "identity")
    if active:
        return JSONResponse(
            {"error": f"身份提名任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "identity", total=1, detail="identity nomination")
    jobs.run_in_background(job_id, identity.run_nomination)
    return {"job_id": job_id}


@app.get("/api/ontology/identities")
def list_identities():
    return {"identities": identity.list_identities(db.get_conn())}


@app.post("/api/ontology/identities/{identity_id}/status")
def set_identity_status(identity_id: int, patch: StatusPatch):
    if not identity.set_identity_status(db.get_conn(), identity_id, patch.status):
        return JSONResponse({"error": "invalid status or id"}, status_code=400)
    jobs.emit(db.get_conn(), None, "identity.status",
              {"id": identity_id, "status": patch.status})
    return {"ok": True}


@app.post("/api/ontology/identities/{identity_id}/choose")
def choose_identity(identity_id: int):
    """Choose this digital-person template: approve it and delete all others.

    The discarded identities (and their anchors) are removed by cascade,
    leaving exactly one approved template to guide extraction."""
    result = identity.choose_identity(db.get_conn(), identity_id)
    if result is None:
        return JSONResponse({"error": "identity not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "identity.chosen",
              {"approved_id": identity_id, "deleted": result["deleted"]})
    return {"ok": True, "approved_id": identity_id, "deleted": result["deleted"]}


@app.delete("/api/ontology/identities/{identity_id}")
def delete_identity(identity_id: int):
    """Hard-delete a rejected template. Anchors cascade. Used by the UI
    '拒绝' button (rejection == removal, not a soft status)."""
    if not identity.delete_identity(db.get_conn(), identity_id):
        return JSONResponse({"error": "identity not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "identity.deleted", {"id": identity_id})
    return {"ok": True, "id": identity_id}


class AnchorPatch(BaseModel):
    identity_id: int | None = None
    name: str | None = None
    type: str | None = None
    definition: str | None = None


class IdentityCreateBody(BaseModel):
    name: str
    mission: str = ""
    description: str = ""
    seed_candidate_ids: list[int] = []
    prompt: str = ""            # 附加指令（创建时直接设定；铁律由代码硬保证不被绕过）
    category: str = identity.DEFAULT_CATEGORY   # general | domain_expert


@app.post("/api/ontology/identities")
def create_identity(body: IdentityCreateBody):
    """Create a digital person from the ontology graph: user-picked seed
    candidates become both approved anchors and the persona's initial ontology
    段 (their explicit pick = final adjudication)."""
    iid = identity.create_identity(db.get_conn(), body.name, body.mission,
                                   body.description, body.seed_candidate_ids,
                                   body.prompt, body.category)
    if iid is None:
        return JSONResponse({"error": "名字不能为空或过长"}, status_code=400)
    jobs.emit(db.get_conn(), None, "identity.created",
              {"id": iid, "name": body.name, "seeds": len(body.seed_candidate_ids)})
    return {"ok": True, "id": iid}


class IdentityUpdateBody(BaseModel):
    name: str | None = None
    mission: str | None = None
    description: str | None = None
    prompt: str | None = None          # 附加指令（铁律由代码硬保证不被绕过）
    category: str | None = None        # general | domain_expert


@app.put("/api/ontology/identities/{identity_id}")
def update_identity(identity_id: int, body: IdentityUpdateBody):
    """Lightweight edit of a digital person (name / mission / description)."""
    if not identity.update_identity(db.get_conn(), identity_id,
                                    body.model_dump(exclude_none=True)):
        return JSONResponse({"error": "nothing to update or invalid values"},
                            status_code=400)
    jobs.emit(db.get_conn(), None, "identity.updated", {"id": identity_id})
    return {"ok": True}


@app.post("/api/ontology/anchors")
def add_anchor(body: AnchorPatch):
    """Manually add an anchor under an identity (user fixes the nomination)."""
    if not body.identity_id or not (body.name or "").strip():
        return JSONResponse({"error": "identity_id and name required"}, status_code=400)
    aid = identity.add_anchor(db.get_conn(), body.identity_id,
                              body.model_dump(exclude={"identity_id"}))
    if aid is None:
        return JSONResponse({"error": "identity not found or invalid name"}, status_code=400)
    jobs.emit(db.get_conn(), None, "identity.anchor_added", {"id": aid})
    return {"ok": True, "id": aid}


@app.put("/api/ontology/anchors/{anchor_id}")
def update_anchor(anchor_id: int, body: AnchorPatch):
    """Edit an anchor (name / type / definition) - anchors stay user-editable."""
    if not identity.update_anchor(db.get_conn(), anchor_id,
                                  body.model_dump(exclude={"identity_id"},
                                                  exclude_none=True)):
        return JSONResponse({"error": "nothing to update or invalid values"},
                            status_code=400)
    jobs.emit(db.get_conn(), None, "identity.anchor_updated", {"id": anchor_id})
    return {"ok": True}


@app.post("/api/ontology/anchors/{anchor_id}/status")
def set_anchor_status(anchor_id: int, patch: StatusPatch):
    if not identity.set_anchor_status(db.get_conn(), anchor_id, patch.status):
        return JSONResponse({"error": "invalid status or id"}, status_code=400)
    jobs.emit(db.get_conn(), None, "identity.anchor_status",
              {"id": anchor_id, "status": patch.status})
    return {"ok": True}


# ---------------- exam (quiz grading before approval) ----------------

@app.post("/api/ontology/exam/run")
def trigger_exam():
    """Manual exam trigger (also auto-triggered after a successful extraction).
    Candidates that fail and never passed are deleted automatically; 存疑
    (missing) and mixed-signal ones stay for the user's final approval."""
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM quiz").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "没有考题 - 先运行本体提取（提取时顺带出题）"},
                            status_code=400)
    active = jobs.active_job(conn, "exam")
    if active:
        return JSONResponse(
            {"error": f"考核任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "exam", total=n, detail="审批前考核")
    jobs.run_in_background(job_id, ontology.run_exam)
    return {"job_id": job_id}


# ---------------- orchestration (二次编排: rule clean + GLM triage) ----------------

@app.post("/api/ontology/orchestrate")
def trigger_orchestration():
    """GLM 5.2 二次编排 job (user-triggered "保存"): 垂直领域相关度分档 +
    删除/合并提名。建议只提名, 待用户一键终审才落地。"""
    conn = db.get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM candidates WHERE kind='entity'"
        " AND status IN ('pending','approved')").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "没有候选本体 - 先运行本体提取"},
                            status_code=400)
    if not llm.llm2_configured():
        return JSONResponse(
            {"error": "二次编排需要配置判别模型 GLM——请到设置页填 GLM token"},
            status_code=400)
    active = jobs.active_job(conn, "orchestrate")
    if active:
        return JSONResponse(
            {"error": f"二次编排任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "orchestrate", total=0,
                             detail="GLM 相关度分档 + 删除/合并提名")
    jobs.run_in_background(job_id, orchestration.run_glm_orchestration)
    return {"job_id": job_id}


@app.get("/api/ontology/orchestration")
def get_orchestration():
    return orchestration.pending_summary(db.get_conn())


@app.post("/api/ontology/orchestration/confirm")
def confirm_orchestration():
    """一键终审：执行所有待确认建议（删除/合并），keep 无操作。"""
    result = orchestration.confirm_all(db.get_conn())
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "nothing to confirm")},
                            status_code=400)
    return result


@app.post("/api/ontology/orchestration/discard")
def discard_orchestration():
    return orchestration.discard_all(db.get_conn())


@app.post("/api/ontology/orchestration/items/{item_id}/dismiss")
def dismiss_orchestration_item(item_id: int):
    if not orchestration.dismiss_item(db.get_conn(), item_id):
        return JSONResponse({"error": "item not found or not pending"},
                            status_code=400)
    return {"ok": True, "id": item_id}


# ---------------- assembly (数字人本体装配: persona image -> ontology 段) ----------------

class AssembleBody(BaseModel):
    identity_id: int


@app.post("/api/ontology/assemble")
def trigger_assembly(body: AssembleBody):
    """数字人本体装配 job (user-triggered): 按指定数字人的使命+锚点，从已有
    候选本体里筛选可用本体（L0 锚点召回 + L1 规则排除 + L2 GLM 分档），生成待
    确认清单。落地（复制到 persona_ontology）需用户一键终审。"""
    conn = db.get_conn()
    if not conn.execute("SELECT id FROM identities WHERE id=?",
                        (body.identity_id,)).fetchone():
        return JSONResponse({"error": f"数字人 #{body.identity_id} 不存在"},
                            status_code=404)
    n = conn.execute(
        "SELECT COUNT(*) c FROM candidates WHERE kind='entity'"
        " AND status IN ('pending','approved')").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "没有候选本体 - 先运行本体提取"},
                            status_code=400)
    if not llm.llm2_configured():
        return JSONResponse(
            {"error": "本体装配需要配置判别模型 GLM——请到设置页填 GLM token"},
            status_code=400)
    active = jobs.active_job(conn, "assemble")
    if active:
        return JSONResponse(
            {"error": f"本体装配任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "assemble", total=0,
                             detail="按数字人画像筛选候选本体，装配到数字人 ontology 段",
                             ref_id=body.identity_id)
    jobs.run_in_background(job_id, assembly.run_assembly, body.identity_id)
    return {"job_id": job_id}


@app.get("/api/ontology/assembly")
def get_assembly(identity_id: int | None = None):
    return assembly.pending_summary(db.get_conn(), identity_id)


# ---------------- persona actions (六元组 actions 维度) ----------------

class ActionNominateBody(BaseModel):
    identity_id: int


class ActionStatusBody(BaseModel):
    status: str


@app.get("/api/ontology/actions")
def list_persona_actions(identity_id: int):
    return {"actions": actions.list_actions(db.get_conn(), identity_id)}


@app.post("/api/ontology/actions/nominate")
def nominate_persona_actions(body: ActionNominateBody):
    """LLM 提名动作（只提名），三关校验后落库 pending，待用户审批。"""
    result = actions.nominate_actions(db.get_conn(), body.identity_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "nominate failed")},
                            status_code=400)
    return result


@app.post("/api/ontology/actions/{action_id}/status")
def set_persona_action_status(action_id: int, body: ActionStatusBody):
    if not actions.set_action_status(db.get_conn(), action_id, body.status):
        return JSONResponse({"error": "invalid status or id"}, status_code=400)
    return {"ok": True}


class AssemblyActionBody(BaseModel):
    identity_id: int | None = None


@app.post("/api/ontology/assembly/confirm")
def confirm_assembly(body: AssemblyActionBody):
    """一键终审：把 adopt 的候选复制到数字人 ontology 段，exclude 无操作。"""
    result = assembly.confirm_assembly(db.get_conn(), body.identity_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "nothing to confirm")},
                            status_code=400)
    return result


@app.post("/api/ontology/assembly/discard")
def discard_assembly(body: AssemblyActionBody):
    return assembly.discard_assembly(db.get_conn(), body.identity_id)


@app.post("/api/ontology/assembly/restore")
def restore_assembly(body: AssemblyActionBody):
    """恢复最近一次被丢弃的装配批次（撤销丢弃 / 找回被重跑归档的历史结果）。"""
    result = assembly.restore_assembly(db.get_conn(), body.identity_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "nothing to restore")},
                            status_code=400)
    return result


@app.post("/api/ontology/assembly/items/{item_id}/dismiss")
def dismiss_assembly_item(item_id: int):
    if not assembly.dismiss_assembly_item(db.get_conn(), item_id):
        return JSONResponse({"error": "item not found or not pending"},
                            status_code=400)
    return {"ok": True, "id": item_id}


@app.get("/api/ontology/persona-ontology")
def get_persona_ontology(identity_id: int | None = None):
    return assembly.persona_ontology_list(db.get_conn(), identity_id)


# ---------------- persona chat (数字人对话: 多模型 + 本体约束开关) ----------------

class ChatBody(BaseModel):
    identity_id: int
    message: str
    use_ontology: bool = True          # toggle the ontology constraint
    use_rag: bool = False              # toggle optional corpus-reference injection
    provider: str = "llm2"             # llm2 | llm | ollama
    ollama_model: str | None = None    # required when provider == "ollama"
    session_id: int | None = None      # chat session; omitted -> auto-create


class RouteBody(BaseModel):
    message: str


class CompareArm(BaseModel):
    use_ontology: bool = True
    use_rag: bool = False


class CompareBody(BaseModel):
    identity_id: int
    message: str
    provider: str = "ollama"           # A/B baseline responder (local 7B)
    ollama_model: str | None = None
    left: CompareArm | None = None     # per-pane toggles (ontology / RAG)
    right: CompareArm | None = None


@app.get("/api/chat/models")
def chat_models():
    """Available responders for the chat page: backend llm/llm2 config status
    + the local Ollama model list (for the 7B hallucination baseline)."""
    s = settings_store.load_settings()
    out = {
        "llm": {"configured": llm.llm_configured(), "model": s["llm"]["model"]},
        "llm2": {"configured": llm.llm2_configured(), "model": s["llm2"]["model"]},
        "ollama": {"configured": False, "models": [], "base_url": ""},
    }
    emb = s["embedding"]
    base = (emb.get("base_url") or "http://localhost:11434").rstrip("/")
    probe = embedding.check_ollama(base, "")
    out["ollama"] = {
        "configured": probe.get("ok", False),
        "models": probe.get("models", []),
        "base_url": base,
        "error": probe.get("error"),
    }
    return out


@app.post("/api/chat/route")
def chat_route(body: RouteBody):
    """Auto-route a message to the best-matching approved digital persona
    (deterministic score, 0 LLM). Returns None when nothing matched."""
    r = chat.route_identity(db.get_conn(), body.message)
    return {"route": r}


@app.post("/api/chat")
def persona_chat(body: ChatBody):
    """One turn of conversation with a digital person, answered by the chosen
    responder (GLM 5.2 default / DeepSeek / local Ollama 7B), grounded by the
    persona's ontology constraint when use_ontology is on.

    Provider fallback: when the caller keeps the literal default "llm2" but
    settings.llm_mode == "local", resolve to ("ollama", local_llm.model).
    Backend logic only — explicit provider="llm"/"llm2"/"ollama" wins.
    """
    prov, omodel = body.provider, body.ollama_model
    if prov in ("llm2", "", None):
        prov, omodel = settings_store.resolve_provider()
        omodel = omodel or body.ollama_model
    try:
        result = chat.answer(db.get_conn(), body.identity_id, body.message,
                             use_ontology=body.use_ontology,
                             provider=prov,
                             ollama_model=omodel,
                             use_rag=body.use_rag,
                             session_id=body.session_id)
    except llm.LLMError as e:
        return JSONResponse({"error": f"模型调用失败：{e}"}, status_code=502)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "chat failed")},
                            status_code=400)
    return result


@app.post("/api/chat/compare")
def persona_chat_compare(body: CompareBody):
    """A/B hallucination comparison: the same message answered twice — left with
    the persona's ontology constraint, right without. Does NOT persist to
    chat_messages."""
    try:
        result = chat.compare(db.get_conn(), body.identity_id, body.message,
                              provider=body.provider,
                              ollama_model=body.ollama_model,
                              left=body.left.model_dump() if body.left else None,
                              right=body.right.model_dump() if body.right else None)
    except llm.LLMError as e:
        return JSONResponse({"error": f"模型调用失败：{e}"}, status_code=502)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "compare failed")},
                            status_code=400)
    return result


@app.get("/api/chat/messages")
def chat_messages(identity_id: int, session_id: int | None = None):
    return {"messages": chat.list_messages(db.get_conn(), identity_id, session_id)}


@app.delete("/api/chat/messages")
def clear_chat_messages(identity_id: int, session_id: int | None = None):
    return {"ok": True, "deleted": chat.clear_messages(db.get_conn(), identity_id, session_id)}


# ---------------- chat sessions (multi-session history) ----------------

class SessionCreateBody(BaseModel):
    identity_id: int
    title: str = ""


class SessionRenameBody(BaseModel):
    title: str


@app.get("/api/chat/sessions")
def chat_sessions(identity_id: int):
    return {"sessions": chat.list_sessions(db.get_conn(), identity_id)}


@app.post("/api/chat/sessions")
def create_chat_session(body: SessionCreateBody):
    sess = chat.create_session(db.get_conn(), body.identity_id, body.title)
    if sess is None:
        return JSONResponse({"error": f"数字人 #{body.identity_id} 不存在"},
                            status_code=404)
    return {"ok": True, "session": sess}


@app.patch("/api/chat/sessions/{session_id}")
def rename_chat_session(session_id: int, body: SessionRenameBody):
    if not chat.rename_session(db.get_conn(), session_id, body.title):
        return JSONResponse({"error": "会话不存在或标题为空"}, status_code=400)
    return {"ok": True}


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: int):
    return {"ok": True, "deleted": chat.delete_session(db.get_conn(), session_id)}


# ---------------- persona benchmark (四组对照测试 + 本体问题归因 + 版本管理) ----------------

from . import benchmark  # noqa: E402  (placed here to keep the chat section together)


class BenchmarkBody(BaseModel):
    identity_id: int
    limit: int = benchmark.DEFAULT_LIMIT
    ollama_model: str | None = None


@app.post("/api/benchmark/run")
def run_benchmark_route(body: BenchmarkBody):
    """四组对照测试 job (user-triggered): 本地模型答题（无RAG无本体 / 仅本体 /
    仅RAG / RAG+本体），GLM 判卷提名 + 确定性终审，错误归因提名问题本体 →
    待审变更集。结论与提名显示在数字人卡片上，merge 由用户终审。"""
    conn = db.get_conn()
    if not conn.execute("SELECT id FROM identities WHERE id=?",
                        (body.identity_id,)).fetchone():
        return JSONResponse({"error": f"数字人 #{body.identity_id} 不存在"},
                            status_code=404)
    n = conn.execute(
        "SELECT COUNT(*) c FROM persona_ontology WHERE identity_id=?"
        " AND status='active'", (body.identity_id,)).fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "该数字人本体段为空，先装配本体再测试"},
                            status_code=400)
    if not llm.llm2_configured():
        return JSONResponse({"error": "测试判卷需要配置 GLM——请到设置页填 GLM token"},
                            status_code=400)
    active = jobs.active_job(conn, "benchmark")
    if active:
        return JSONResponse(
            {"error": f"测试任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"}, status_code=409)
    limit = max(1, min(body.limit, benchmark.MAX_LIMIT))
    job_id = jobs.create_job(conn, "benchmark", total=0,
                             detail="四组对照测试：本地模型答题 + GLM 判卷 + 错误归因提名",
                             ref_id=body.identity_id)
    jobs.run_in_background(job_id, benchmark.run_benchmark, body.identity_id,
                           body.ollama_model, limit)
    return {"job_id": job_id}


@app.get("/api/benchmark")
def get_benchmark(identity_id: int):
    return benchmark.benchmark_summary(db.get_conn(), identity_id)


@app.post("/api/benchmark/changes/{change_id}/reject")
def reject_benchmark_change(change_id: int):
    if not benchmark.reject_change(db.get_conn(), change_id):
        return JSONResponse({"error": "提名不存在或已处理"}, status_code=400)
    return {"ok": True, "id": change_id}


class BenchmarkMergeBody(BaseModel):
    identity_id: int


@app.post("/api/benchmark/merge")
def merge_benchmark_changes(body: BenchmarkMergeBody):
    """用户终审：把全部待审提名合并进数字人本体段（先快照，版本 v+1）。"""
    result = benchmark.merge_changes(db.get_conn(), body.identity_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "nothing to merge")},
                            status_code=400)
    return result


class BenchmarkRollbackBody(BaseModel):
    identity_id: int
    version_id: int


@app.post("/api/benchmark/rollback")
def rollback_benchmark_version(body: BenchmarkRollbackBody):
    """回滚到指定版本（恢复快照；回滚前自动把当前状态再快照为新版本）。"""
    result = benchmark.rollback_version(db.get_conn(), body.identity_id,
                                        body.version_id)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "rollback failed")},
                            status_code=400)
    return result


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}


# ---------------- local LLM (WSL2 Ollama) ----------------
# settings.llm_mode ∈ {"cloud","local"}  ·  settings.local_llm = {base_url, model}
# Frontend SettingsPage 使用这一节完成 "测试连通 / 选定模型 / 拉取模型 / 切换主 provider"。
# 旧 settings.json 无这俩键 → settings_store 的 _deep_merge + DEFAULTS 自动填空, 向后兼容。

_OLLAMA_PULL_STATE: dict = {
    "state": "idle",          # idle | running | done | error
    "model": "",
    "received": 0,
    "total": 0,
    "error": None,
    "started_at": 0.0,
    "finished_at": 0.0,
}
_PULL_LOCK = threading.Lock()


def _set_pull_state(**patch):
    with _PULL_LOCK:
        _OLLAMA_PULL_STATE.update(patch)


def _get_pull_state() -> dict:
    with _PULL_LOCK:
        return dict(_OLLAMA_PULL_STATE)


def _do_ollama_pull(base: str, model: str) -> None:
    """Background thread: stream NDJSON from {base}/api/pull, update pull state.

    Reuses netutil.local_urlopen so it works against host- and WSL2-forwarded
    Ollama (which both listen on localhost:11434). Long timeout because model
    pulls often run into the minutes; heartbeat every layer keeps the UI live.
    """
    _set_pull_state(state="running", model=model, received=0, total=0,
                    error=None, started_at=time.time(), finished_at=0.0)
    try:
        from . import netutil  # local import (top-level would import-test pin)
        body = json.dumps({"model": model, "stream": True}).encode("utf-8")
        with netutil.local_urlopen(base.rstrip("/") + "/api/pull", data=body,
                                   method="POST", headers={"Content-Type": "application/json"},
                                   timeout=7200) as resp:
            for raw in resp:
                try:
                    line = raw.decode("utf-8", errors="ignore").strip()
                except Exception:
                    continue
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except Exception:
                    continue
                if not isinstance(evt, dict):
                    continue
                err = evt.get("error")
                if err and not _get_pull_state().get("error"):
                    _set_pull_state(error=str(err))
                if "completed" in evt or "total" in evt:
                    _set_pull_state(
                        received=int(evt.get("completed", 0) or 0),
                        total=int(evt.get("total", 0) or 0),
                    )
                if evt.get("status") == "success":
                    _set_pull_state(state="done", finished_at=time.time())
                    return
        # Stream ended without explicit success event — treat as done if no error
        with _PULL_LOCK:
            if _OLLAMA_PULL_STATE["state"] == "running":
                _OLLAMA_PULL_STATE["state"] = "done"
                _OLLAMA_PULL_STATE["finished_at"] = time.time()
    except Exception as e:
        _set_pull_state(state="error",
                        error=f"{type(e).__name__}: {e}",
                        finished_at=time.time())


@app.get("/api/ollama/models")
def list_ollama_models():
    """List installed models at the configured local Ollama endpoint, plus
    whether the user-selected chat model is currently installed."""
    s = settings_store.load_settings()
    local = (s.get("local_llm") or {})
    base = (local.get("base_url") or "http://localhost:11434").rstrip("/")
    selected = (local.get("model") or "").strip()
    probe = embedding.check_ollama(base, "")
    models = probe.get("models") or []
    return {
        "ok": bool(probe.get("ok")),
        "base_url": base,
        "models": models,
        "selected": selected,
        "selected_installed": bool(selected) and (selected in models),
        "error": probe.get("error"),
    }


class OllamaPullBody(BaseModel):
    model: str


@app.post("/api/ollama/pull")
def ollama_pull(body: OllamaPullBody):
    """Kick off a background Ollama model pull. Frontend polls
    /api/ollama/pull-status for progress. One concurrent pull at a time."""
    model = (body.model or "").strip()
    if not model:
        return JSONResponse({"ok": False, "error": "model required"}, status_code=400)
    with _PULL_LOCK:
        st = _OLLAMA_PULL_STATE.get("state")
        cur_model = _OLLAMA_PULL_STATE.get("model", "")
        if st == "running":
            return JSONResponse({"ok": False, "error": f"已有一个拉取任务在进行（{cur_model}）"},
                                status_code=409)
    s = settings_store.load_settings()
    local = s.get("local_llm") or {}
    base = (local.get("base_url") or "http://localhost:11434").rstrip("/")
    t = threading.Thread(target=_do_ollama_pull, args=(base, model), daemon=True)
    t.start()
    return {"ok": True, "model": model, "state": "started"}


@app.get("/api/ollama/pull-status")
def ollama_pull_status():
    """Snapshot of the in-flight (or last-completed) pull. Frontend polls this."""
    return _get_pull_state()


class LocalLlmPatch(BaseModel):
    base_url: str = ""
    model: str = ""


@app.put("/api/settings/local-llm")
def put_local_llm(body: LocalLlmPatch):
    """Persist the WSL2-Ollama chat LLM endpoint & model selection.

    Structure validation only (no LLM call); the user clicks "检测 Ollama"
    separately via GET /api/ollama/models for live reachability feedback.
    """
    payload = body.model_dump()
    errs = settings_store.validate_local_llm_config(payload)
    if errs:
        return JSONResponse({"ok": False, "error": "; ".join(errs)}, status_code=400)
    merged = settings_store.save_settings({"local_llm": payload})
    return {"ok": True, "settings": settings_store.mask_settings(merged),
            "local_llm": merged.get("local_llm")}


class LlmModeBody(BaseModel):
    mode: str                       # "cloud" | "local"


@app.put("/api/settings/llm-mode")
def put_llm_mode(body: LlmModeBody):
    """Set the main-LLM provider mode. Persists into settings.llm_mode;
    subsequent /api/chat calls without explicit provider fall back to local
    Ollama when mode == "local"."""
    if body.mode not in ("cloud", "local"):
        return JSONResponse({"ok": False, "error": 'mode 必须是 "cloud" 或 "local"'},
                            status_code=400)
    merged = settings_store.save_settings({"llm_mode": body.mode})
    return {"ok": True, "settings": settings_store.mask_settings(merged),
            "mode": body.mode}


# ---------------- pipeline 编排（一等公民实体） ----------------

class PipelineCreateBody(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []


class PipelineUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    entry_node_id: int | None = None
    exit_node_id: int | None = None


class PipelineNodeBody(BaseModel):
    node_key: str = ""
    persona_id: int | None = None
    kind: str = pipeline.KIND_NOMINATE
    step_name: str = ""
    position: dict | None = None


class PipelineNodePatch(BaseModel):
    node_key: str | None = None
    persona_id: int | None = None
    kind: str | None = None
    step_name: str | None = None
    position: dict | None = None


class PipelineRelationBody(BaseModel):
    from_node_id: int
    to_node_id: int
    relation_type: str
    handoff_type: str = ""
    handoff_schema: str = ""


class PipelineChatBody(BaseModel):
    message: str


def _design_changes_via_llm(pipeline_dict: dict, user_message: str) -> list:
    """流程设计师 LLM 提名：把 pipeline 结构 + 用户需求交给 LLM，产出 changes JSON 数组。

    只提名不裁决；解析失败返回空列表（前端可提示用户手动编辑）。"""
    system = (
        "你是流程设计师，根据用户需求修改数字人编排图（pipeline）。\n"
        "pipeline 由数字人节点（nodes）和数字人关系（relations）组成。\n"
        f"关系类型闭集：{', '.join(pipeline.RELATION_TYPES)}\n"
        f"节点 kind：{', '.join(pipeline.NODE_KINDS)}（nominate=数字人LLM提名节点，deterministic=确定性Function节点）\n"
        f"修改动作闭集：{', '.join(pipeline.CHANGE_ACTIONS)}\n"
        "你只提名修改，不裁决。只输出一个 JSON 数组，每项形如 "
        "{\"action\":\"add_node\",\"payload\":{\"node_key\":\"n6\",\"persona_id\":3,"
        "\"kind\":\"nominate\",\"step_name\":\"代码复核\"},\"reason\":\"...\"}。"
    )
    user = (f"当前 pipeline：{json.dumps(pipeline_dict, ensure_ascii=False)}\n"
            f"用户需求：{user_message}\n请输出修改提名 JSON 数组（不要任何解释文字）。")
    try:
        reply = llm.chat([{"role": "system", "content": system},
                          {"role": "user", "content": user}], temperature=0.0)
    except Exception:
        return []
    text = (reply or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except Exception:
        s, e = text.find("["), text.rfind("]")
        if s == -1 or e == -1:
            return []
        try:
            data = json.loads(text[s:e + 1])
        except Exception:
            return []
    if isinstance(data, dict):
        data = data.get("changes", data.get("modifications", []))
    if not isinstance(data, list):
        return []
    return data


def _design_pipeline_via_llm(name: str, user_desc: str, hint_tags: list[str],
                             identities: list[dict]) -> dict | None:
    """「Pipeline 创建工程师」数字人调用：接收需求描述，输出完整 pipeline 设计草案。

    返回 dict 形如 {name, tags, description, nodes:[...], relations:[...]}；
    解析失败返回 None（端点会创建空 pipeline 并返回 note）。"""
    persona_lines = "\n".join(
        f"  - id={i['id']} name={i['name']} category={i.get('category','-')}"
        for i in identities
    ) or "  (暂无已批准数字人)"
    system = (
        "你是平台通用数字人「Pipeline 创建工程师」（category=general）。\n"
        "你的职责：接收用户对编排图的需求描述，输出结构化 pipeline 设计草案，"
        "供用户在前端审批后再生成最终 pipeline。你只提名不裁决——具体 persona_id 由用户后续手动绑定。\n"
        "\n"
        f"关系类型闭集：{', '.join(pipeline.RELATION_TYPES)}\n"
        f"节点 kind 闭集：{', '.join(pipeline.NODE_KINDS)}（nominate=数字人 LLM 提名节点，deterministic=确定性 Function 节点）\n"
        "\n"
        "已批准数字人列表（persona_query 中只描述能力需求，不要捏造 persona_id）：\n"
        + persona_lines +
        "\n"
        "\n"
        "约束：\n"
        "1. 只输出一个 JSON 对象，不要任何解释文字、不要 markdown 围栏。\n"
        "2. JSON 固定结构：{name, tags, description, nodes:[...], relations:[...]}。\n"
        "3. nodes 每项必须含 node_key(以 'n' 开头的短名)/kind/step_name(中文动宾短语)/persona_query。"
        "   2~6 个节点为宜。\n"
        "4. relations 每项必须含 from/to/关系类型。handoff_type/handoff_schema 可选。"
        "   必须为 DAG（无环），下游方向是「产出给下一节点」。\n"
        "5. persona_query 只描述能力需求（如「需要学术调研能力的数字人」），不要写具体 ID。\n"
        "6. 不知道的事实宁可不写，不要补造节点或关系。\n"
        "7. 输出语言：中文。"
    )
    user = (
        f"用户填写的 pipeline 名称：{name}\n"
        f"用户填写的需求描述：{user_desc}\n"
        f"用户填写的标签（可选）：{', '.join(hint_tags) or '(无)'}\n"
        "请输出 JSON 设计草案。"
    )
    try:
        reply = llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=0.0,
        )
    except Exception:
        return None
    text = (reply or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            return None
        try:
            data = json.loads(text[s:e + 1])
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    return data


@app.get("/api/pipelines")
def list_pipelines():
    return {"pipelines": pipeline.list_pipelines(db.get_conn())}


@app.post("/api/pipelines")
def create_pipeline(body: PipelineCreateBody):
    pid = pipeline.create_pipeline(db.get_conn(), body.name, body.description,
                                   body.tags)
    if pid is None:
        return JSONResponse({"error": "名字不能为空或过长"}, status_code=400)
    note = ""
    desc = (body.description or "").strip()
    # 描述非空 → 调「Pipeline 创建工程师」数字人生成节点 + 关系草案
    if desc:
        idents = [dict(r) for r in db.get_conn().execute(
            "SELECT id, name, category FROM identities"
            " WHERE status='approved' ORDER BY id").fetchall()]
        design = _design_pipeline_via_llm(body.name, desc, body.tags or [], idents)
        nodes_added = 0
        rels_added = 0
        if design and isinstance(design.get("nodes"), list):
            for nd in design["nodes"]:
                if not isinstance(nd, dict):
                    continue
                nk = (nd.get("node_key") or "").strip()
                if not nk:
                    continue
                kind = nd.get("kind") or "nominate"
                step = nd.get("step_name") or nk
                pipeline.add_node(db.get_conn(), pid, nk, None, kind, step, None)
                nodes_added += 1
            # 关系需要 from/to 的 node_id，按 node_key 反查
            cur_p = pipeline.get_pipeline(db.get_conn(), pid)
            key2id = {n["node_key"]: n["id"] for n in (cur_p or {}).get("nodes", [])}
            for rd in design.get("relations", []) or []:
                if not isinstance(rd, dict):
                    continue
                fid = key2id.get((rd.get("from") or "").strip())
                tid = key2id.get((rd.get("to") or "").strip())
                if not fid or not tid:
                    continue
                rt = rd.get("relation_type") or "handoff"
                ht = rd.get("handoff_type") or ""
                hs = rd.get("handoff_schema") or ""
                pipeline.add_relation(db.get_conn(), pid, fid, tid, rt, ht, hs)
                rels_added += 1
            jobs.emit(db.get_conn(), None, "pipeline.designed_by_llm",
                      {"id": pid, "by": "Pipeline 创建工程师",
                       "nodes": nodes_added, "relations": rels_added})
        else:
            note = "Pipeline 创建工程师未能解析出有效设计草案，请手动编辑节点与关系。"
            jobs.emit(db.get_conn(), None, "pipeline.design_failed",
                      {"id": pid, "by": "Pipeline 创建工程师"})
    jobs.emit(db.get_conn(), None, "pipeline.created",
              {"id": pid, "name": body.name})
    return {"ok": True, "id": pid, "note": note}


@app.get("/api/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: int):
    p = pipeline.get_pipeline(db.get_conn(), pipeline_id)
    if p is None:
        return JSONResponse({"error": "pipeline not found"}, status_code=404)
    return {"pipeline": p}


@app.put("/api/pipelines/{pipeline_id}")
def update_pipeline(pipeline_id: int, body: PipelineUpdateBody):
    if not pipeline.update_pipeline(db.get_conn(), pipeline_id,
                                    body.model_dump(exclude_none=True)):
        return JSONResponse({"error": "nothing to update or invalid values"},
                            status_code=400)
    jobs.emit(db.get_conn(), None, "pipeline.updated", {"id": pipeline_id})
    return {"ok": True}


@app.delete("/api/pipelines/{pipeline_id}")
def delete_pipeline(pipeline_id: int):
    if not pipeline.delete_pipeline(db.get_conn(), pipeline_id):
        return JSONResponse({"error": "pipeline not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "pipeline.deleted", {"id": pipeline_id})
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/nodes")
def add_pipeline_node(pipeline_id: int, body: PipelineNodeBody):
    nid = pipeline.add_node(db.get_conn(), pipeline_id, body.node_key,
                            body.persona_id, body.kind, body.step_name,
                            body.position)
    if nid is None:
        return JSONResponse({"error": "invalid node (empty key or bad kind)"},
                            status_code=400)
    return {"ok": True, "id": nid}


@app.put("/api/pipelines/{pipeline_id}/nodes/{node_id}")
def update_pipeline_node(pipeline_id: int, node_id: int, body: PipelineNodePatch):
    if not pipeline.update_node(db.get_conn(), node_id,
                                body.model_dump(exclude_none=True)):
        return JSONResponse({"error": "nothing to update or invalid values"},
                            status_code=400)
    return {"ok": True}


@app.delete("/api/pipelines/{pipeline_id}/nodes/{node_id}")
def remove_pipeline_node(pipeline_id: int, node_id: int):
    if not pipeline.remove_node(db.get_conn(), node_id):
        return JSONResponse({"error": "node not found"}, status_code=404)
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/relations")
def add_pipeline_relation(pipeline_id: int, body: PipelineRelationBody):
    rid = pipeline.add_relation(db.get_conn(), pipeline_id, body.from_node_id,
                                body.to_node_id, body.relation_type,
                                body.handoff_type, body.handoff_schema)
    if rid is None:
        return JSONResponse({"error": "invalid relation (bad type or self-loop)"},
                            status_code=400)
    return {"ok": True, "id": rid}


@app.delete("/api/pipelines/{pipeline_id}/relations/{relation_id}")
def remove_pipeline_relation(pipeline_id: int, relation_id: int):
    if not pipeline.remove_relation(db.get_conn(), relation_id):
        return JSONResponse({"error": "relation not found"}, status_code=404)
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/validate")
def validate_pipeline(pipeline_id: int):
    errors = pipeline.validate_pipeline(db.get_conn(), pipeline_id)
    return {"ok": len(errors) == 0, "errors": errors}


@app.post("/api/pipelines/{pipeline_id}/approve")
def approve_pipeline(pipeline_id: int):
    errors = pipeline.validate_pipeline(db.get_conn(), pipeline_id)
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=400)
    if not pipeline.approve_pipeline(db.get_conn(), pipeline_id):
        return JSONResponse({"error": "pipeline not found"}, status_code=404)
    jobs.emit(db.get_conn(), None, "pipeline.approved", {"id": pipeline_id})
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/run")
def run_pipeline(pipeline_id: int):
    """运行 pipeline：创建父 job（kind=pipeline_run），后台执行引擎按拓扑调度。

    deterministic 节点走内置映射表（建数字人），nominate 节点走数字人 tool-use
    loop。与「一键流水线」的 kind=pipeline 区分开，避免互斥误伤。"""
    conn = db.get_conn()
    p = pipeline.get_pipeline(conn, pipeline_id)
    if p is None:
        return JSONResponse({"error": "pipeline not found"}, status_code=404)
    if p["status"] != pipeline.STATUS_APPROVED:
        return JSONResponse({"error": "pipeline 未批准，请先校验并批准"},
                            status_code=400)
    active = jobs.active_job(conn, "pipeline_run")
    if active:
        return JSONResponse({"error": f"已有 pipeline 运行任务 #{active['id']}"},
                            status_code=409)
    job_id = jobs.create_job(conn, "pipeline_run", len(p["nodes"]),
                             detail=f"运行 pipeline：{p['name']}",
                             ref_id=pipeline_id)
    jobs.run_in_background(job_id, pipeline.run_pipeline_execution, pipeline_id)
    return {"job_id": job_id}


@app.get("/api/pipelines/{pipeline_id}/changes")
def list_pipeline_changes(pipeline_id: int):
    return {"changes": pipeline.list_changes(db.get_conn(), pipeline_id)}


@app.post("/api/pipelines/{pipeline_id}/changes/{change_id}/approve")
def approve_pipeline_change(pipeline_id: int, change_id: int):
    if not pipeline.apply_change(db.get_conn(), change_id):
        return JSONResponse({"error": "change not found or not pending"},
                            status_code=400)
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/changes/{change_id}/reject")
def reject_pipeline_change(pipeline_id: int, change_id: int):
    if not pipeline.reject_change(db.get_conn(), change_id):
        return JSONResponse({"error": "change not found"}, status_code=400)
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/chat")
def chat_pipeline(pipeline_id: int, body: PipelineChatBody):
    """对话改 pipeline：流程设计师(LLM)根据用户消息产出修改提名，落库 pending。
    用户在 changes 里审批后应用。LLM 只提名，不裁决。"""
    p = pipeline.get_pipeline(db.get_conn(), pipeline_id)
    if p is None:
        return JSONResponse({"error": "pipeline not found"}, status_code=404)
    changes = _design_changes_via_llm(p, body.message)
    if not changes:
        return {"ok": True, "changes": [], "note": "未能解析出有效修改提名"}
    cid = pipeline.nominate_changes(db.get_conn(), pipeline_id, changes)
    return {"ok": True, "change_id": cid, "changes": changes}


# ---------------- static frontend (dist) ----------------

DIST = Path(__file__).resolve().parent.parent.parent / "client" / "dist"
if DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="dist")
