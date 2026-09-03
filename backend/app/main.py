# -*- coding: utf-8 -*-
"""rag_mvp backend entry: FastAPI app + REST + WS event stream + static dist."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db, jobs, settings_store, ingest, ontology, orchestration, assembly, llm, embedding, identity, chat


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = db.get_conn()
    recovered = jobs.recover_stale_jobs(conn, db.now())
    if recovered:
        jobs.emit(conn, None, "system.recovered", {"jobs": recovered})
    yield


app = FastAPI(title="rag-mvp", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


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
def trigger_ingest():
    s = settings_store.load_settings()
    if not s["work_dir"]:
        return JSONResponse({"error": "work_dir not set"}, status_code=400)
    conn = db.get_conn()
    active = jobs.active_job(conn, "ingest") or jobs.active_job(conn, "repair")
    if active:
        return JSONResponse(
            {"error": f"任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发，避免并发写库冲突"},
            status_code=409)
    job_id = jobs.create_job(conn, "ingest", total=0, detail=s["work_dir"])
    jobs.run_in_background(job_id, ingest.ingest_workdir, s["work_dir"])
    return {"job_id": job_id}


@app.post("/api/rag/repair-summaries")
def trigger_repair():
    """Re-run LLM summaries for chunks stuck with rule-fallback quality
    (e.g. network-blip victims of the old silent-degrade code)."""
    conn = db.get_conn()
    if not llm.llm_configured():
        return JSONResponse({"error": "LLM 未配置 - 规则兜底是设计行为，无需修复"},
                            status_code=400)
    active = jobs.active_job(conn, "ingest") or jobs.active_job(conn, "repair")
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
    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    if n == 0:
        return JSONResponse({"error": "no chunks - run ingest first"}, status_code=400)
    active = jobs.active_job(conn, "ontology")
    if active:
        return JSONResponse(
            {"error": f"本体提取任务 #{active['id']} 正在运行（{active['status']}），"
                      "请先暂停或删除它再触发"},
            status_code=409)
    job_id = jobs.create_job(conn, "ontology", total=n, detail="EDC-lite extraction")
    jobs.run_in_background(job_id, ontology.run_extraction)
    return {"job_id": job_id}


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
async def ws_events(ws: WebSocket, since: int = 0):
    """Reconnect-safe event stream: client sends last seq, we push increments."""
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


@app.post("/api/ontology/identities")
def create_identity(body: IdentityCreateBody):
    """Create a digital person from the ontology graph: user-picked seed
    candidates become both approved anchors and the persona's initial ontology
    段 (their explicit pick = final adjudication)."""
    iid = identity.create_identity(db.get_conn(), body.name, body.mission,
                                   body.description, body.seed_candidate_ids)
    if iid is None:
        return JSONResponse({"error": "名字不能为空或过长"}, status_code=400)
    jobs.emit(db.get_conn(), None, "identity.created",
              {"id": iid, "name": body.name, "seeds": len(body.seed_candidate_ids)})
    return {"ok": True, "id": iid}


class IdentityUpdateBody(BaseModel):
    name: str | None = None
    mission: str | None = None
    description: str | None = None


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


@app.post("/api/chat")
def persona_chat(body: ChatBody):
    """One turn of conversation with a digital person, answered by the chosen
    responder (GLM 5.2 default / DeepSeek / local Ollama 7B), grounded by the
    persona's ontology constraint when use_ontology is on."""
    try:
        result = chat.answer(db.get_conn(), body.identity_id, body.message,
                             use_ontology=body.use_ontology,
                             provider=body.provider,
                             ollama_model=body.ollama_model,
                             use_rag=body.use_rag)
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
def chat_messages(identity_id: int):
    return {"messages": chat.list_messages(db.get_conn(), identity_id)}


@app.delete("/api/chat/messages")
def clear_chat_messages(identity_id: int):
    return {"ok": True, "deleted": chat.clear_messages(db.get_conn(), identity_id)}


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


# ---------------- static frontend (dist) ----------------

DIST = Path(__file__).resolve().parent.parent.parent / "client" / "dist"
if DIST.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="dist")
