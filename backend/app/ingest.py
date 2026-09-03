# -*- coding: utf-8 -*-
"""RAG ingest pipeline (one job): load -> chunk -> summary/tags -> embed -> store.

Atomic unit = one chunk: store row + progress update in one SQLite tx.
Content-hash dedupe: unchanged files are skipped on re-ingest.
"""
import hashlib

from . import db, jobs, loaders, chunking, llm, embedding


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_workdir(conn, job_id: int, work_dir: str) -> None:
    files = loaders.scan_workdir(work_dir)
    if not files:
        jobs.update_progress(conn, job_id, 0, 0)
        jobs.emit(conn, job_id, "ingest.scan", {"work_dir": work_dir, "files": 0})
        jobs.finish_job(conn, job_id, ok=True)
        return

    embedding.assert_model_lock(conn)

    # pass 1: load + dedupe check
    pending = []
    for f in files:
        try:
            doc = loaders.load_text(f)
        except Exception as e:
            jobs.emit(conn, job_id, "ingest.file_error",
                      {"file": f.name, "error": str(e)})
            continue
        if doc["note"] and not doc["text"].strip():
            jobs.emit(conn, job_id, "ingest.file_skipped",
                      {"file": doc["name"], "reason": doc["note"]})
            continue
        chash = _content_hash(doc["text"])
        row = conn.execute(
            "SELECT id, content_hash, doc_summary FROM documents WHERE path=?",
            (doc["path"],)).fetchone()
        # complete = doc_summary filled; an interrupted/interrupted-file doc
        # (empty summary) must be re-ingested, not skipped
        complete = row and row["content_hash"] == chash and (row["doc_summary"] or "").strip()
        if complete:
            jobs.emit(conn, job_id, "ingest.file_unchanged", {"file": doc["name"]})
            continue
        if row:  # changed or incomplete: replace (delete old chunks)
            conn.execute("DELETE FROM documents WHERE id=?", (row["id"],))
            conn.commit()
        pending.append((doc, chash))

    total_chunks = 0
    plans = []
    for doc, chash in pending:
        chunks = chunking.chunk_text(doc["text"])
        total_chunks += len(chunks)
        plans.append((doc, chash, chunks))

    jobs.update_progress(conn, job_id, 0, total_chunks)
    jobs.emit(conn, job_id, "ingest.scan",
              {"work_dir": work_dir, "files": len(pending), "chunks": total_chunks})

    done = 0
    for doc, chash, chunks in plans:
        # create document row first with empty summary; chunk rows reference it
        try:
            cur = conn.execute(
                "INSERT INTO documents(name, path, content_hash, doc_summary, embedding,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (doc["name"], doc["path"], chash, "", "[]", db.now()))
            conn.commit()
        except Exception as e:  # noqa: BLE001 - UNIQUE(path) race guard
            conn.rollback()
            jobs.emit(conn, job_id, "ingest.file_error",
                      {"file": doc["name"],
                       "error": f"insert conflict: {type(e).__name__}: {e}"})
            continue
        doc_id = cur.lastrowid

        try:
            chunk_summaries = []
            for c in chunks:
                jobs.poll_control(conn, job_id)  # pause/delete at chunk boundary
                meta = llm.summarize_chunk(c["text"])
                emb = embedding.embed(meta["summary"])  # embed the summary (retrieval entry)
                conn.execute(
                    "INSERT INTO chunks(doc_id, seq, text, summary, tags, embedding,"
                    " content_hash) VALUES(?,?,?,?,?,?,?)",
                    (doc_id, c["index"], c["text"], meta["summary"],
                     jobs.json_dumps(meta["tags"]), jobs.json_dumps(emb), _content_hash(c["text"])))
                # atomic unit: data + progress in one tx
                jobs.update_progress(conn, job_id, done + 1)
                done += 1
                chunk_summaries.append(meta["summary"])

            doc_summary = llm.summarize_document(chunk_summaries)
            doc_emb = embedding.embed(doc_summary)
            conn.execute("UPDATE documents SET doc_summary=?, embedding=? WHERE id=?",
                         (doc_summary, jobs.json_dumps(doc_emb), doc_id))
            conn.commit()
            jobs.emit(conn, job_id, "ingest.document_done",
                      {"file": doc["name"], "doc_id": doc_id, "chunks": len(chunks)})
        except llm.LLMError as e:
            # LLM configured but unreachable (network/auth/quota): never
            # silently degrade - auto-pause with the reason shown on the
            # task row. Partial doc keeps its chunks (empty doc_summary)
            # and is re-ingested from scratch on resume.
            jobs.auto_pause(conn, job_id,
                            f"LLM 调用失败，任务已自动暂停（检查网络/密钥后点「继续」）：{e}")
            raise jobs.JobPaused()
        except (jobs.JobPaused, jobs.JobCancelled):
            raise  # control signals pass through: partial doc keeps its chunks
            # (empty doc_summary) and is re-ingested on resume
        except Exception as e:  # noqa: BLE001
            # one bad file must never kill the whole job: drop the partial doc
            # (cascade deletes its chunks) so a retry re-ingests it from scratch
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            conn.commit()
            jobs.emit(conn, job_id, "ingest.file_error",
                      {"file": doc["name"],
                       "error": f"{type(e).__name__}: {e}"})

    jobs.finish_job(conn, job_id, ok=True)


# ---------------- repair: re-run LLM for rule-fallback summaries ----------------

def _is_rule_fallback(row) -> bool:
    """Deterministic detection: stored summary + tags exactly equal the rule
    fallback outputs -> the LLM call failed silently back then (old code)
    and this chunk only has offline-quality metadata."""
    import json as _json
    try:
        tags = _json.loads(row["tags"]) if row["tags"] else []
    except Exception:
        tags = []
    return (row["summary"] == llm.rule_summary(row["text"])
            and tags == llm.rule_tags(row["text"]))


def run_summary_repair(conn, job_id: int) -> None:
    """Job: re-summarize chunks stuck with rule-fallback quality (e.g. network
    blip victims), then recompute the affected documents' summaries.

    LLM access failure auto-pauses this job like ingest (iron law 2)."""
    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.seq, c.text, c.summary, c.tags FROM chunks c"
    ).fetchall()
    targets = [r for r in rows if _is_rule_fallback(r)]
    docs = sorted({r["doc_id"] for r in targets})
    jobs.update_progress(conn, job_id, 0, len(targets))
    jobs.emit(conn, job_id, "repair.scan",
              {"fallback_chunks": len(targets), "docs": docs})
    if not targets:
        jobs.finish_job(conn, job_id, ok=True)
        return

    fixed = 0
    for r in targets:
        jobs.poll_control(conn, job_id)
        try:
            meta = llm.summarize_chunk(r["text"])
            emb = embedding.embed(meta["summary"])
        except llm.LLMError as e:
            jobs.auto_pause(conn, job_id,
                            f"LLM 调用失败，修复任务已自动暂停（检查网络/密钥后点「继续」）：{e}")
            raise jobs.JobPaused()
        conn.execute("UPDATE chunks SET summary=?, tags=?, embedding=? WHERE id=?",
                     (meta["summary"], jobs.json_dumps(meta["tags"]),
                      jobs.json_dumps(emb), r["id"]))
        fixed += 1
        jobs.update_progress(conn, job_id, fixed)

    # affected docs that already completed: rebuild their doc summary from
    # the now-good chunk summaries. Incomplete docs (empty doc_summary) are
    # left to the normal ingest resume path.
    for doc_id in docs:
        jobs.poll_control(conn, job_id)
        drow = conn.execute(
            "SELECT id, name, doc_summary FROM documents WHERE id=?",
            (doc_id,)).fetchone()
        if not drow or not (drow["doc_summary"] or "").strip():
            continue
        srows = conn.execute(
            "SELECT summary FROM chunks WHERE doc_id=? ORDER BY seq",
            (doc_id,)).fetchall()
        try:
            doc_summary = llm.summarize_document([r["summary"] for r in srows])
            doc_emb = embedding.embed(doc_summary)
        except llm.LLMError as e:
            jobs.auto_pause(conn, job_id,
                            f"LLM 调用失败，修复任务已自动暂停（检查网络/密钥后点「继续」）：{e}")
            raise jobs.JobPaused()
        conn.execute("UPDATE documents SET doc_summary=?, embedding=? WHERE id=?",
                     (doc_summary, jobs.json_dumps(doc_emb), doc_id))
        jobs.emit(conn, job_id, "repair.doc_done",
                  {"file": drow["name"], "doc_id": doc_id})

    jobs.finish_job(conn, job_id, ok=True)


def stats(conn) -> dict:
    docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    return {"documents": docs, "chunks": chunks}


def list_documents(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT d.id, d.name, d.path, d.doc_summary,"
        " (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunk_count"
        " FROM documents d ORDER BY d.id DESC").fetchall()
    return [dict(r) for r in rows]


def list_chunks(conn, page: int = 1, page_size: int = 20,
                doc_id: int | None = None) -> dict:
    where, params = "", []
    if doc_id:
        where = "WHERE doc_id=?"
        params = [doc_id]
    total = conn.execute(f"SELECT COUNT(*) c FROM chunks {where}", params).fetchone()["c"]
    offset = max(0, (page - 1) * page_size)
    rows = conn.execute(
        f"SELECT id, doc_id, seq, summary, tags, text FROM chunks {where}"
        f" ORDER BY doc_id, seq LIMIT ? OFFSET ?",
        params + [page_size, offset]).fetchall()
    import json as _json
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = _json.loads(d["tags"]) if d["tags"] else []
        except Exception:
            d["tags"] = []
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def get_chunk(conn, chunk_id: int) -> dict | None:
    row = conn.execute(
        "SELECT c.id, c.doc_id, c.seq, c.text, c.summary, c.tags, d.name AS doc_name"
        " FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id=?",
        (chunk_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    import json as _json
    try:
        d["tags"] = _json.loads(d["tags"]) if d["tags"] else []
    except Exception:
        d["tags"] = []
    return d


def delete_document(conn, doc_id: int) -> bool:
    """Delete one document. Schema FKs cascade: chunks, then their mentions."""
    cur = conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_chunks(conn, ids: list[int]) -> int:
    """Delete chunks by id (mentions cascade). Returns rows actually deleted."""
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    cur = conn.execute(f"DELETE FROM chunks WHERE id IN ({ph})", ids)
    conn.commit()
    return cur.rowcount


def search(conn, query: str, top_k: int = 5, tag: str | None = None) -> list[dict]:
    """Embed query -> cosine over stored chunk-summary vectors."""
    import json as _json
    q_emb = embedding.embed(query)
    rows = conn.execute("SELECT id, doc_id, seq, text, summary, tags FROM chunks").fetchall()
    scored = []
    for r in rows:
        try:
            tags = _json.loads(r["tags"]) if r["tags"] else []
            emb = _json.loads(conn.execute(
                "SELECT embedding FROM chunks WHERE id=?", (r["id"],)).fetchone()["embedding"])
        except Exception:
            continue
        if tag and tag not in tags:
            continue
        scored.append({"chunk_id": r["id"], "doc_id": r["doc_id"], "seq": r["seq"],
                       "summary": r["summary"], "tags": tags, "text": r["text"],
                       "score": round(embedding.cosine(q_emb, emb), 4)})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]
