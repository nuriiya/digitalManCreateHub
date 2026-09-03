# -*- coding: utf-8 -*-
"""API smoke tests via TestClient (fake LLM + hash embedding, zero network)."""
import json

from fastapi.testclient import TestClient

from app import jobs, ingest, settings_store


def _client(env, workdir, fake_llm):
    from app.main import app
    settings_store.save_settings({"work_dir": str(workdir)})
    # run pipelines synchronously instead of via background threads
    jid = jobs.create_job(env, "ingest", 0, str(workdir))
    ingest.ingest_workdir(env, jid, str(workdir))
    fake_llm["extraction"] = lambda prompt: json.dumps({
        "entities": [{"name": "报销系统", "type": "系统",
                      "definition": "财务核心", "mentions": ["报销系统"]}],
        "relations": []}, ensure_ascii=False)
    oid = jobs.create_job(env, "ontology", 0, "EDC")
    from app import ontology
    ontology.run_extraction(env, oid)
    return TestClient(app)


def test_health_and_settings_flow(env, workdir, fake_llm):
    client = _client(env, workdir, fake_llm)
    assert client.get("/api/health").json()["ok"] is True

    # settings: save with token then read masked
    r = client.put("/api/settings", json={
        "llm": {"base_url": "https://api.test/v1", "api_key": "sk-abcdef1234567890",
                "model": "v4-flash"}})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_configured"] is True
    assert "abcdef" not in body["settings"]["llm"]["api_key"]

    # test-llm goes through the fake chat hook -> ok
    r = client.post("/api/settings/test-llm")
    assert r.json()["ok"] is True

    # hash embedding check
    r = client.post("/api/settings/test-embedding")
    assert r.json()["ok"] is True


def test_rag_preview_endpoints(env, workdir, fake_llm):
    client = _client(env, workdir, fake_llm)
    stats = client.get("/api/rag/stats").json()
    assert stats["documents"] == 1 and stats["chunks"] >= 1
    assert client.get("/api/rag/documents").json()["documents"][0]["chunk_count"] >= 1
    chunks = client.get("/api/rag/chunks").json()
    assert chunks["items"]
    cid = chunks["items"][0]["id"]
    detail = client.get(f"/api/rag/chunks/{cid}").json()
    assert "报销系统" in detail["text"]
    res = client.post("/api/rag/search", json={"query": "测试"}).json()["results"]
    assert res and res[0]["score"] > 0
    assert client.get("/api/rag/chunks/99999").status_code == 404


def test_ontology_graph_and_approval(env, workdir, fake_llm):
    client = _client(env, workdir, fake_llm)
    g = client.get("/api/ontology/graph").json()
    assert any(n["name"] == "报销系统" for n in g["nodes"])
    cid = [n for n in g["nodes"] if n["name"] == "报销系统"][0]["id"]
    detail = client.get(f"/api/ontology/candidates/{cid}").json()
    assert detail["mentions"]
    r = client.post(f"/api/ontology/candidates/{cid}/status",
                    json={"status": "approved"})
    assert r.json()["ok"] is True
    assert client.post(f"/api/ontology/candidates/{cid}/status",
                       json={"status": "bogus"}).status_code == 400
    g2 = client.get("/api/ontology/graph").json()
    node = [n for n in g2["nodes"] if n["id"] == cid][0]
    assert node["status"] == "approved"


def test_jobs_and_events(env, workdir, fake_llm):
    client = _client(env, workdir, fake_llm)
    jobs_list = client.get("/api/jobs").json()["jobs"]
    assert {j["kind"] for j in jobs_list} >= {"ingest", "ontology"}
    events = client.get("/api/events?since=0").json()["events"]
    assert events and all(e["seq"] > 0 for e in events)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    # incremental: since=last seq returns nothing
    last = seqs[-1]
    assert client.get(f"/api/events?since={last}").json()["events"] == []


def test_ingest_requires_workdir(env, tmp_path, fake_llm):
    from app.main import app
    from app import settings_store
    settings_store.save_settings({"work_dir": ""})
    client = TestClient(app)
    r = client.post("/api/rag/ingest")
    assert r.status_code == 400


def test_rag_delete_document_cascades(env, workdir, fake_llm):
    """Doc delete cascades chunks AND their mentions (schema FK), emits event."""
    client = _client(env, workdir, fake_llm)
    docs = client.get("/api/rag/documents").json()["documents"]
    assert docs
    did = docs[0]["id"]
    # ontology extraction created at least one mention referencing a chunk
    assert env.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"] >= 1

    assert client.delete(f"/api/rag/documents/{did}").status_code == 200
    assert client.delete(f"/api/rag/documents/{did}").status_code == 404  # gone

    stats = client.get("/api/rag/stats").json()
    assert stats["documents"] == 0 and stats["chunks"] == 0
    assert env.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"] == 0
    events = client.get("/api/events?since=0").json()["events"]
    assert any(e["type"] == "rag.document_deleted" for e in events)


def test_rag_delete_chunks_single_and_batch(env, workdir, fake_llm):
    (workdir / "b.md").write_text(
        "第二个文档。预算超支时需要财务专员复核。流程结束后归档。", encoding="utf-8")
    client = _client(env, workdir, fake_llm)
    ids = [c["id"] for c in client.get("/api/rag/chunks").json()["items"]]
    assert len(ids) >= 2

    # single delete: 200 then 404
    assert client.delete(f"/api/rag/chunks/{ids[0]}").status_code == 200
    assert client.delete(f"/api/rag/chunks/{ids[0]}").status_code == 404

    # batch delete the rest
    rest = ids[1:]
    r = client.post("/api/rag/chunks/batch-delete", json={"ids": rest})
    assert r.json()["deleted"] == len(rest)
    stats = client.get("/api/rag/stats").json()
    assert stats["chunks"] == 0
    # mentions referencing the deleted chunks are gone
    assert env.execute("SELECT COUNT(*) c FROM mentions").fetchone()["c"] == 0
    events = client.get("/api/events?since=0").json()["events"]
    assert any(e["type"] == "rag.chunk_deleted" for e in events)
    # empty id list is a no-op, not an error
    assert client.post("/api/rag/chunks/batch-delete", json={"ids": []}).json()["deleted"] == 0


def test_default_to_reachable_ollama(env, monkeypatch, fake_llm):
    """Host-ollama default switch: flips untouched hash, respects the lock."""
    from app import embedding, settings_store, db

    # pretend localhost:11434 answers with bge-m3 (no real network in UT)
    monkeypatch.setattr(embedding, "check_ollama",
                        lambda base_url, model: {"ok": True, "has_model": True,
                                                 "models": [model]})

    # untouched default (hash, no data yet) -> switch
    assert embedding.default_to_reachable_ollama(env) is True
    assert settings_store.load_settings()["embedding"]["provider"] == "ollama"

    # explicit user choice (not hash) -> never touched
    settings_store.save_settings({"embedding": {"provider": "hash"}})
    # now simulate an embedding lock from existing vectors (hash provider)
    db.kv_set(env, "embedding_lock", {"provider": "hash", "model": "bge-m3"})
    assert embedding.default_to_reachable_ollama(env) is False
    assert settings_store.load_settings()["embedding"]["provider"] == "hash"

    # unreachable ollama -> no switch
    monkeypatch.setattr(embedding, "check_ollama",
                        lambda base_url, model: {"ok": False, "has_model": False})
    env.execute("DELETE FROM kv WHERE k='embedding_lock'")
    env.commit()
    assert embedding.default_to_reachable_ollama(env) is False
    assert settings_store.load_settings()["embedding"]["provider"] == "hash"
