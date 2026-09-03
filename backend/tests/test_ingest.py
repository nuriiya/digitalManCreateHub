# -*- coding: utf-8 -*-
"""Ingest pipeline tests (fake LLM + hash embedding, zero network)."""
from app import jobs, ingest


def _run(env, workdir):
    job_id = jobs.create_job(env, "ingest", 0, str(workdir))
    ingest.ingest_workdir(env, job_id, str(workdir))
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return job_id, row


def test_ingest_end_to_end(env, workdir, fake_llm):
    job_id, row = _run(env, workdir)
    assert row["status"] == "done"
    stats = ingest.stats(env)
    assert stats["documents"] == 1
    assert stats["chunks"] >= 1
    # chunk text is verbatim source, summary came from fake llm
    c = ingest.get_chunk(env, 1)
    assert "报销系统" in c["text"]
    assert c["summary"] == "测试段摘要"
    assert c["tags"] == ["测试"]


def test_ingest_dedupe_unchanged_file(env, workdir):
    _run(env, workdir)
    job_id, row = _run(env, workdir)  # re-ingest same content
    assert row["status"] == "done"
    assert ingest.stats(env)["documents"] == 1  # not duplicated
    events = jobs.events_since(env, 0)
    assert any(e["type"] == "ingest.file_unchanged" for e in events)


def test_ingest_changed_file_replaces_chunks(env, workdir):
    _run(env, workdir)
    (workdir / "a.md").write_text(
        "《报销系统》升级为V2，新增预算预警功能。", encoding="utf-8")
    _run(env, workdir)
    assert ingest.stats(env)["documents"] == 1  # same path, replaced
    docs = ingest.list_documents(env)
    assert docs[0]["chunk_count"] >= 1


def test_search_hits_relevant_chunk(env, workdir, fake_llm):
    _run(env, workdir)
    # retrieval entry is the chunk summary (fake llm => "测试段摘要")
    results = ingest.search(env, "测试", top_k=3)
    assert results
    assert results[0]["score"] > 0


def test_pagination(env, workdir, fake_llm):
    big = workdir / "big.md"
    big.write_text("".join(f"第{i}段内容，关于财务报销流程的详细说明文字{i}。" for i in range(200)),
                   encoding="utf-8")
    _run(env, workdir)
    page1 = ingest.list_chunks(env, page=1, page_size=5)
    assert page1["page_size"] == 5 and len(page1["items"]) == 5
    page2 = ingest.list_chunks(env, page=2, page_size=5)
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not (ids1 & ids2)


def test_embedding_model_lock(env, workdir, monkeypatch):
    from app import settings_store, embedding
    _run(env, workdir)  # locks embedding config
    # switching provider must be refused (iron law 1)
    settings_store.save_settings({"embedding": {"provider": "ollama"}})
    monkeypatch.setattr(embedding, "active_provider", lambda: "ollama")
    try:
        embedding.assert_model_lock(env)
        assert False, "should have raised"
    except RuntimeError as e:
        assert "locked" in str(e)


def test_empty_workdir(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    job_id, row = _run(env, empty)
    assert row["status"] == "done"
    assert ingest.stats(env) == {"documents": 0, "chunks": 0}


def test_one_bad_file_never_kills_the_job(env, workdir, fake_llm, monkeypatch):
    """Regression: a file that raises mid-processing is dropped, job still done."""
    (workdir / "b.md").write_text("另一个正常文档，包含考勤与绩效内容。", encoding="utf-8")
    from app import llm
    calls = {"n": 0}

    def flaky_summarize(text):
        calls["n"] += 1
        if "毒文件" in text:
            raise SystemError("error return without exception set")
        return {"summary": "测试段摘要", "tags": ["测试"]}

    (workdir / "poison.md").write_text("毒文件内容，处理它时会抛错。更多文字避免空文档。", encoding="utf-8")
    monkeypatch.setattr(llm, "summarize_chunk", flaky_summarize)
    job_id, row = _run(env, workdir)
    assert row["status"] == "done"  # job survives
    names = [d["name"] for d in ingest.list_documents(env)]
    assert "poison.md" not in names  # partial doc dropped
    assert "a.md" in names and "b.md" in names  # good files all ingested
    events = jobs.events_since(env, 0)
    assert any(e["type"] == "ingest.file_error" and "poison" in e["payload"]["file"]
               for e in events)


def test_interrupted_document_is_retried(env, workdir, fake_llm):
    """A doc row left with empty summary (power cut) must be re-ingested, not skipped."""
    _run(env, workdir)
    env.execute("UPDATE documents SET doc_summary='' WHERE id=1")
    env.commit()
    job_id, row = _run(env, workdir)
    assert row["status"] == "done"
    row_doc = env.execute(
        "SELECT doc_summary FROM documents WHERE name='a.md'").fetchone()
    assert row_doc["doc_summary"]  # completed this time
    events = jobs.events_since(env, 0)
    assert not any(e["type"] == "ingest.file_unchanged" for e in events)


def test_llm_access_failure_auto_pauses_job(env, workdir, fake_llm):
    """Regression (job #7): LLM configured but network blips must auto-pause
    the job with a visible error - never silently degrade to rule quality."""
    import pytest
    from app import llm

    def _network_blip(messages):
        raise RuntimeError("APIConnectionError: Connection error.")

    fake_llm["reply"] = _network_blip
    job_id = jobs.create_job(env, "ingest", 0, str(workdir))
    with pytest.raises(jobs.JobPaused):  # worker unwinds after auto-pause
        ingest.ingest_workdir(env, job_id, str(workdir))
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "paused"
    assert row["error"] and "LLM" in row["error"] and "自动暂停" in row["error"]
    events = jobs.events_since(env, 0)
    assert any(e["type"] == "job.autopaused" for e in events)
    # no silent rule-fallback chunk was written
    assert env.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"] == 0


def test_unconfigured_llm_is_offline_mode_not_pause(env, workdir):
    """No base_url/key at all: rule mode is the intended path, job done."""
    job_id, row = _run(env, workdir)
    assert row["status"] == "done"
    assert row["error"] is None
    from app import llm
    c = ingest.get_chunk(env, 1)
    assert c["summary"] == llm.rule_summary(c["text"])


def test_summary_repair_reruns_fallback_chunks(env, workdir, monkeypatch):
    """Repair job re-summarizes chunks stored with rule-fallback quality
    (network blip victims) and rebuilds the doc summary."""
    from app import llm, settings_store

    # pass 1: offline ingest -> rule-fallback chunks
    _run(env, workdir)
    c = ingest.get_chunk(env, 1)
    from app import llm as _llm
    assert c["summary"] == _llm.rule_summary(c["text"])
    doc0 = ingest.list_documents(env)[0]
    assert doc0["doc_summary"] != "测试整篇摘要"  # still rule-fallback quality

    # pass 2: LLM becomes reachable (fake chat routes canned summaries)
    settings_store.save_settings({"llm": {"base_url": "http://fake.local",
                                          "api_key": "fake-key", "model": "fake-model"}})
    monkeypatch.setattr(llm, "_fake_chat",
                        lambda messages: '{"summary": "测试段摘要", "tags": ["测试"]}'
                        if "文档摘要助手" in messages[-1]["content"] else "测试整篇摘要")
    job_id = jobs.create_job(env, "repair", 0, "rule-fallback 修复")
    ingest.run_summary_repair(env, job_id)
    row = env.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    events = jobs.events_since(env, 0)
    assert any(e["type"] == "repair.scan"
               and e["payload"]["fallback_chunks"] >= 1 for e in events)
    c = ingest.get_chunk(env, 1)
    assert c["summary"] == "测试段摘要"  # re-summarized by LLM
    assert c["tags"] == ["测试"]
    doc = ingest.list_documents(env)[0]
    assert doc["doc_summary"] == "测试整篇摘要"  # rebuilt from good chunks


def test_repair_is_idempotent(env, workdir, monkeypatch):
    """After a successful repair a second repair run finds nothing to do."""
    from app import llm, settings_store
    _run(env, workdir)
    settings_store.save_settings({"llm": {"base_url": "http://fake.local",
                                          "api_key": "fake-key", "model": "fake-model"}})
    monkeypatch.setattr(llm, "_fake_chat",
                        lambda messages: '{"summary": "测试段摘要", "tags": ["测试"]}'
                        if "文档摘要助手" in messages[-1]["content"] else "测试整篇摘要")
    job_id = jobs.create_job(env, "repair", 0, "r1")
    ingest.run_summary_repair(env, job_id)
    job_id2 = jobs.create_job(env, "repair", 0, "r2")
    ingest.run_summary_repair(env, job_id2)
    evs = [e for e in jobs.events_since(env, 0) if e["type"] == "repair.scan"]
    assert evs[-1]["payload"]["fallback_chunks"] == 0  # nothing left
