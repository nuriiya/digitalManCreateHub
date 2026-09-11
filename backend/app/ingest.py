# -*- coding: utf-8 -*-
"""RAG ingest pipeline (one job): load -> chunk -> summary/tags -> embed -> store.

Atomic unit = one chunk: store row + progress update in one PG tx.
Content-hash dedupe: unchanged files are skipped on re-ingest.

PG specifics (vs the old SQLite version):
  - `documents.embedding` and `chunks.embedding` are pgvector `vector`
    columns of UNSPECIFIED DIMENSION (pgvector 0.5+). We pass Python lists
    of float directly; the pgvector adapter (registered in db._connect) maps
    them to `vector`. NULL on the document row at insert time = no vector
    yet (we fill it after the document summary is computed).
  - `chunks.tags` is a `TEXT[]` column; the pgvector-adapter package also
    registers a Python list[str] <-> TEXT[] adapter. We pass list[str]
    directly (no more json.dumps wrap).
  - `chunks.source_meta` is JSONB (file / sheet / header / row range) so the
    frontend can render the provenance of a retrieved chunk.
  - Retrieval uses pgvector `<=>` cosine distance (exact scan - fine up to
    ~100k chunks; HNSW can be added later without an API change).

Dedup policy (user-decided 2026-09-04):
  - Lookup is by NAME (the user's mental anchor), then content_hash.
  - Same name + same content_hash + doc_summary present -> skip (file_unchanged).
  - Same name + different content_hash (or empty doc_summary) -> UPDATE the
    documents row in place (keep documents.id stable; downstream chunks /
    doc-graph references stay consistent). Old chunks are deleted explicitly
    so mentions cascade through chunks.doc_id ON DELETE CASCADE.
  - New name -> INSERT a fresh row.
"""
import hashlib
import json

from pgvector import Vector

from . import db, jobs, loaders, llm, embedding, chunk_types
from .jsonb import maybe_jsonb


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ingest_workdir(conn, job_id: int, work_dir: str) -> None:
    """Scan a directory and ingest whatever is new / changed.

    Dedup is by name (see module docstring). Atomic unit is one chunk
    (data + progress in one PG tx); LLM failures auto-pause like before."""
    files = loaders.scan_workdir(work_dir)
    if not files:
        jobs.update_progress(conn, job_id, 0, 0)
        jobs.emit(conn, job_id, "ingest.scan", {"work_dir": work_dir, "files": 0})
        jobs.finish_job(conn, job_id, ok=True)
        return

    embedding.assert_model_lock(conn)

    # pass 1: load + skip unsupported
    loaded = []
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
        loaded.append(doc)

    total_chunks = sum(len(d["chunks"]) for d in loaded)
    jobs.update_progress(conn, job_id, 0, total_chunks)
    jobs.emit(conn, job_id, "ingest.scan",
              {"work_dir": work_dir, "files": len(loaded),
               "chunks": total_chunks})

    done = 0
    for doc in loaded:
        _kind, _doc_id, n = _ingest_one_file(conn, job_id, doc, done)
        done += n

    jobs.finish_job(conn, job_id, ok=True)


def _ingest_one_file(conn, job_id: int, doc: dict, start_chunk_index: int
                     ) -> tuple[str, int | None, int]:
    """Run the per-file pipeline for ONE document.

    Returns (status, doc_id, chunks_done):
      - status:       "added" | "replaced" | "unchanged" | "error"
      - doc_id:       documents.id (existing kept on replace; None on error)
      - chunks_done:  how many chunk rows this call wrote (for progress)

    Dedup rule is by NAME (the user's mental anchor), then content_hash:
      - row absent                          -> INSERT
      - row present, equal hash, summary OK -> file_unchanged, skip
      - row present, different (or empty)   -> UPDATE documents in place
        (id stable), DELETE chunks (mentions cascade), re-embed.
    """
    chash = _content_hash(doc["text"])
    existing = conn.execute(
        "SELECT id, content_hash, doc_summary FROM documents WHERE name=?",
        (doc["name"],)).fetchone()

    if existing and existing["content_hash"] == chash \
            and (existing["doc_summary"] or "").strip():
        jobs.emit(conn, job_id, "ingest.file_unchanged",
                  {"file": doc["name"], "doc_id": existing["id"]})
        return ("unchanged", existing["id"], 0)

    if existing:
        # Same name, content changed (or previous run was incomplete):
        # keep documents.id stable. We do NOT DELETE the documents row -
        # we DELETE its chunks explicitly so mentions cascade through
        # chunks.doc_id ON DELETE CASCADE.
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (existing["id"],))
        conn.execute(
            "UPDATE documents SET path=?, content_hash=?, doc_summary='',"
            " embedding=NULL, file_type=?, created_at=? WHERE id=?",
            (doc["path"], chash, doc.get("file_type") or "",
             db.now(), existing["id"]))
        conn.commit()
        doc_id = existing["id"]
        emit_kind = "replaced"
    else:
        try:
            cur = conn.execute(
                "INSERT INTO documents(name, path, content_hash, doc_summary,"
                " embedding, file_type, created_at) VALUES(?,?,?,?,?,?,?)",
                (doc["name"], doc["path"], chash, "", None,
                 doc.get("file_type") or "", db.now()))
            conn.commit()
            doc_id = cur.lastrowid
            emit_kind = "added"
        except Exception as e:  # noqa: BLE001 - UNIQUE(path) race guard
            conn.rollback()
            jobs.emit(conn, job_id, "ingest.file_error",
                      {"file": doc["name"],
                       "error": f"insert conflict: {type(e).__name__}: {e}"})
            return ("error", None, 0)

    try:
        chunk_summaries: list[str] = []
        done_local = 0
        # 词表只查一次：同一份文件的所有 chunk 共用同一份裁决依据（design §11.4）。
        # LLM 只拿到「可选 type 清单」，两个维度不交给它 —— 由 resolve() 裁决。
        tmap = chunk_types.type_map(conn)
        catalog = chunk_types.catalog_for_prompt(tmap)
        for c in doc["chunks"]:
            jobs.poll_control(conn, job_id)  # pause/delete at chunk boundary
            meta = llm.summarize_chunk(c["text"], type_catalog=catalog)
            emb = embedding.embed(meta["summary"])  # embed the summary (retrieval entry)
            # 提名 -> 裁决（铁律 L1）：type 不在 active 词表即回落 unknown，
            # 置信度/强制等级一律取词表默认值（保证同 type 全库语义一致）。
            tinfo = chunk_types.resolve(meta.get("type"), tmap)
            tsrc = (chunk_types.SOURCE_RULE if meta.get("source") == "rule"
                    else chunk_types.SOURCE_LLM)
            # list[str] tags -> TEXT[], list[float] embedding -> vector.
            # source_meta is the JSONB provenance block from the loader.
            conn.execute(
                "INSERT INTO chunks(doc_id, seq, text, summary, tags,"
                " embedding, content_hash, source_meta,"
                " type, type_confidence, type_mandatory, type_source)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, c["index"], c["text"], meta["summary"],
                 meta["tags"] or [], emb,
                 _content_hash(c["text"]),
                 json.dumps(c.get("source_meta") or {},
                            ensure_ascii=False),
                 tinfo["type"], tinfo["type_confidence"],
                 tinfo["type_mandatory"], tsrc))
            # atomic unit: data + progress in one tx
            done_local += 1
            jobs.update_progress(conn, job_id, start_chunk_index + done_local)
            chunk_summaries.append(meta["summary"])

        doc_summary = llm.summarize_document(chunk_summaries)
        doc_emb = embedding.embed(doc_summary)
        conn.execute("UPDATE documents SET doc_summary=?, embedding=?"
                     " WHERE id=?",
                     (doc_summary, doc_emb, doc_id))
        conn.commit()
        jobs.emit(conn, job_id, f"ingest.file_{emit_kind}",
                  {"file": doc["name"], "doc_id": doc_id,
                   "chunks": len(doc["chunks"])})
        return (emit_kind, doc_id, done_local)
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
        return ("error", None, 0)


# ---------------- repair: re-run LLM for rule-fallback summaries ----------------

def _is_rule_fallback(row) -> bool:
    """Deterministic detection: stored summary + tags exactly equal the rule
    fallback outputs -> the LLM call failed silently back then (old code)
    and this chunk only has offline-quality metadata."""
    tags = maybe_jsonb(row["tags"]) or []
    if not isinstance(tags, list):
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
    tmap = chunk_types.type_map(conn)
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
        # 修复时同步重判 type（旧数据可能带的是规则兜底类型，design §11.4）
        tinfo = chunk_types.resolve(meta.get("type"), tmap)
        conn.execute(
            "UPDATE chunks SET summary=?, tags=?, embedding=?, type=?,"
            " type_confidence=?, type_mandatory=?, type_source=? WHERE id=?",
            (meta["summary"], meta["tags"] or [], emb, tinfo["type"],
             tinfo["type_confidence"], tinfo["type_mandatory"],
             chunk_types.SOURCE_RULE if meta.get("source") == "rule"
             else chunk_types.SOURCE_LLM, r["id"]))
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
                     (doc_summary, doc_emb, doc_id))
        jobs.emit(conn, job_id, "repair.doc_done",
                  {"file": drow["name"], "doc_id": doc_id})

    jobs.finish_job(conn, job_id, ok=True)


def stats(conn) -> dict:
    docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    return {"documents": docs, "chunks": chunks}


def list_documents(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT d.id, d.name, d.path, d.doc_summary, d.file_type,"
        " (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunk_count"
        " FROM documents d ORDER BY d.id DESC").fetchall()
    return [dict(r) for r in rows]


def list_chunks(conn, page: int = 1, page_size: int = 20,
                doc_id: int | None = None, type: str | None = None,
                confidence: str | None = None,
                mandatory: int | None = None) -> dict:
    """分页列出 chunks（design §11.6）。

    新增三维过滤：`type`（词表 code）/ `confidence`（high|medium|low）/
    `mandatory`（0|1|2）；同时输出四个类型字段供前端列与徽章使用。"""
    clauses, params = [], []
    if doc_id:
        clauses.append("doc_id=?")
        params.append(doc_id)
    if type:
        clauses.append("type=?")
        params.append(type)
    if confidence:
        clauses.append("type_confidence=?")
        params.append(confidence)
    if mandatory is not None:
        clauses.append("type_mandatory=?")
        params.append(int(mandatory))
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = conn.execute(f"SELECT COUNT(*) c FROM chunks {where}", params).fetchone()["c"]
    offset = max(0, (page - 1) * page_size)
    rows = conn.execute(
        f"SELECT id, doc_id, seq, summary, tags, text, source_meta,"
        f" type, type_confidence, type_mandatory, type_source"
        f" FROM chunks {where}"
        f" ORDER BY doc_id, seq LIMIT ? OFFSET ?",
        params + [page_size, offset]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["tags"] = maybe_jsonb(d.get("tags")) or []
        # source_meta is a JSONB dict (may already be); fall back to {} for legacy rows.
        d["source_meta"] = maybe_jsonb(d.get("source_meta")) or {}
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def get_chunk(conn, chunk_id: int) -> dict | None:
    row = conn.execute(
        "SELECT c.id, c.doc_id, c.seq, c.text, c.summary, c.tags, c.source_meta,"
        " c.type, c.type_confidence, c.type_mandatory, c.type_source,"
        " d.name AS doc_name, d.file_type AS doc_file_type"
        " FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE c.id=?",
        (chunk_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["tags"] = maybe_jsonb(d.get("tags")) or []
    d["source_meta"] = maybe_jsonb(d.get("source_meta")) or {}
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


def search(conn, query: str, top_k: int = 5, tag: str | None = None,
           type: str | None = None, confidence: str | None = None,
           mandatory: int | None = None) -> list[dict]:
    """Embed the query -> pgvector cosine over stored chunk-summary vectors.

    `1 - (embedding <=> ?)` is the cosine similarity on vectors that the
    embedding store pre-normalizes (pgvector cosine distance). `tag` restricts
    to chunks whose `tags` array contains that token (`? = ANY(tags)`).

    design §11.5 新增三维过滤：
      - `type`     词表 code 精确匹配；
      - `mandatory` 0(参考)/1(建议)/2(强制) 精确匹配 —— 对话注入用它把
        `mandatory=2` 的强制知识单独捞出来（独立配额，不参与普通 top-K 竞争）；
      - `confidence` 是「**至少这么可信**」语义：high 只匹配 high，
        medium 匹配 high+medium，low 不过滤。

    需要「同一 query 跑两组过滤」时（对话注入的两路）请用 `search_split` ——
    它共享一次 embedding，避免重复调用 embedding 后端。
    """
    q_emb = Vector(embedding.embed(query))  # Vector -> '[...]' text so the
    # `<=> ?` operator sees a vector literal, not an untyped double[] array.
    return _search_with_emb(conn, q_emb, top_k, tag, type, confidence,
                            mandatory)


def search_split(conn, query: str, top_k: int = 5, rules_max: int = 4):
    """对话注入专用：**一次 embed**，两次查询（强制项 / 普通参考资料）。

    design §11.5 的两路注入若各调一次 `search()`，会重复调用 embedding 后端
    （每轮对话多一次 Ollama 网络往返）。自审发现（2026-09-11）：原实现确实
    调了两次。此处共享同一查询向量，并保证两组结果**互不重叠**。

    Returns `(rule_hits, other_hits)`：
      - `rule_hits`  —— `mandatory=2`，独立配额上限 `rules_max`；
      - `other_hits` —— 其余命中（已剔除 rule_hits 的成员），上限 `top_k`。
    """
    q_emb = Vector(embedding.embed(query))
    rule_hits = _search_with_emb(conn, q_emb, rules_max, mandatory=2)
    hits = _search_with_emb(conn, q_emb, top_k)
    rule_ids = {h["chunk_id"] for h in rule_hits}
    return rule_hits, [h for h in hits if h["chunk_id"] not in rule_ids]


def _search_with_emb(conn, q_emb, top_k: int = 5, tag: str | None = None,
                     type: str | None = None, confidence: str | None = None,
                     mandatory: int | None = None) -> list[dict]:
    """`search` 的向量已就绪版本（供复用同一 query 向量的多次调用）。"""
    clauses: list[str] = []
    params: list = []
    if tag:
        clauses.append("? = ANY(tags)")
        params.append(tag)
    if type:
        clauses.append("type = ?")
        params.append(type)
    if mandatory is not None:
        clauses.append("type_mandatory = ?")
        params.append(int(mandatory))
    if confidence:
        allowed = {"high": ["high"], "medium": ["high", "medium"]}.get(confidence)
        if allowed:
            clauses.append("type_confidence IN ("
                           + ",".join("?" for _ in allowed) + ")")
            params.extend(allowed)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT id, doc_id, seq, text, summary, tags, source_meta,"
        " type, type_confidence, type_mandatory, type_source,"
        " 1 - (embedding <=> ?::vector) AS score FROM chunks"
        f" {where}"
        " ORDER BY embedding <=> ?::vector LIMIT ?",
        [q_emb] + params + [q_emb, top_k]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = maybe_jsonb(d.get("tags")) or []
        d["source_meta"] = maybe_jsonb(d.get("source_meta")) or {}
        d["score"] = round(float(d.get("score") or 0.0), 4)
        out.append({
            "chunk_id": d["id"], "doc_id": d["doc_id"], "seq": d["seq"],
            "summary": d["summary"], "tags": d["tags"], "text": d["text"],
            "source_meta": d["source_meta"], "score": d["score"],
            "type": d.get("type"), "type_confidence": d.get("type_confidence"),
            "type_mandatory": d.get("type_mandatory"),
            "type_source": d.get("type_source"),
        })
    return out