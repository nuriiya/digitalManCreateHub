# -*- coding: utf-8 -*-
"""DB: single SQLite file for task-state AND data-state (DM7).

Tables:
  jobs / events          - task state (progress, WS event log)
  documents / chunks     - RAG data state
  candidates / mentions / relations - ontology candidate state
  kv                     - misc metadata (embedding model lock etc.)
"""
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "app.db"

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def get_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Thread-safe singleton connection (check_same_thread=False + RLock)."""
    global _conn
    with _lock:
        if db_path is not None and str(db_path) != str(DB_PATH):
            return _open(db_path)  # test mode: fresh connection, caller closes
        if _conn is None:
            _conn = _open(DB_PATH)
        return _conn


def _open(db_path: Path | str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Disk protection: WAL + NORMAL skips the per-commit fsync (still safe
    # against app crashes - the WAL survives; a power cut may lose the last
    # tx, but jobs are idempotent and re-runnable by design).
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    _protect_from_indexer(Path(db_path))
    return conn


# one retry thread per process is enough (prod opens the singleton once)
_indexer_retry_started = False


def _protect_from_indexer(db_file: Path) -> None:
    """Disk protection: Windows Search re-crawls changed files via the NTFS
    USN journal; the SQLite WAL is rewritten hundreds of times per job, which
    sent SearchIndexer into a ~3.5GB/30s crawl loop (measured 2026-08-31).
    Marking the data dir + db files FILE_ATTRIBUTE_NOT_CONTENT_INDEXED stops
    content indexing of them. Best-effort, Windows-only, idempotent, silent.
    -wal/-shm are (re)created after open, so we retry briefly in the bg."""
    if sys.platform != "win32":
        return
    import ctypes
    NOT_CONTENT_INDEXED = 0x2000
    INVALID = 0xFFFFFFFF
    kernel32 = ctypes.windll.kernel32

    def _mark(p: Path) -> None:
        try:
            cur = kernel32.GetFileAttributesW(str(p))
            if cur == INVALID:
                return
            kernel32.SetFileAttributesW(str(p), cur | NOT_CONTENT_INDEXED)
        except Exception:
            pass  # never let a disk-protection nicety break the pipeline

    data_dir = db_file.parent
    for suffix in ("", "-wal", "-shm"):
        _mark(db_file.with_name(db_file.name + suffix))
    _mark(data_dir)

    global _indexer_retry_started
    if _indexer_retry_started:
        return
    _indexer_retry_started = True

    def _retry() -> None:
        for _ in range(3):
            time.sleep(5)
            for suffix in ("-wal", "-shm"):
                _mark(db_file.with_name(db_file.name + suffix))

    threading.Thread(target=_retry, daemon=True,
                     name="db-not-indexed-retry").start()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,                 -- ingest | ontology | model
        status TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
        progress_current INTEGER NOT NULL DEFAULT 0,
        progress_total INTEGER NOT NULL DEFAULT 0,
        detail TEXT,
        error TEXT,
        ref_id INTEGER,                     -- job-scoped reference (assemble -> identity_id)
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        type TEXT NOT NULL,
        payload TEXT,
        ts REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        doc_summary TEXT,
        embedding TEXT,
        created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
    CREATE TABLE IF NOT EXISTS chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        text TEXT NOT NULL,
        summary TEXT,
        tags TEXT,
        embedding TEXT,
        content_hash TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,                 -- entity | relation
        name TEXT NOT NULL,
        definition TEXT,
        status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|merged
        merged_into INTEGER,
        created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_kind_name ON candidates(kind, name);
    CREATE TABLE IF NOT EXISTS mentions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        span_start INTEGER NOT NULL,
        span_end INTEGER NOT NULL,
        text TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        target_name TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS kv (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS identities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        mission TEXT,
        description TEXT,
        keywords TEXT,                          -- JSON list of supporting high-freq words
        status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS anchors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        type TEXT,
        definition TEXT,
        status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS quiz (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        evidence TEXT NOT NULL,                 -- literal span, verified against chunk text
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS exam_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
        candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        verdict TEXT NOT NULL,                  -- pass | fail | missing
        created_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS exam_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_id INTEGER NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
        job_id INTEGER,
        mode TEXT NOT NULL,                     -- with_ontology | ablated
        answer TEXT,                            -- LLM-1 closed-book answer
        used_names TEXT,                        -- JSON: entities mapped back to ontology nodes
        verdict TEXT NOT NULL,                  -- pass | fail (after deterministic gates)
        enough INTEGER NOT NULL DEFAULT 1,      -- LLM-1: subgraph/question sufficient?
        judge_verdict TEXT,                     -- LLM-2 nomination: supported|contradicted|unknown
        issue TEXT,                             -- fail attribution: retrieval|ontology|context|unused|none
        reason TEXT,                            -- adjudication reason
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_exam_runs_quiz ON exam_runs(quiz_id);
    CREATE TABLE IF NOT EXISTS orchestration_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,                 -- rule | glm
        status TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|discarded
        created_at REAL NOT NULL,
        confirmed_at REAL
    );
    CREATE TABLE IF NOT EXISTS orchestration_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        action TEXT NOT NULL,                 -- delete | merge | keep
        category TEXT,                        -- core | marginal | irrelevant (相关度分档)
        reason TEXT,
        merge_into INTEGER,                   -- target candidate_id for merge
        status TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|dismissed
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_orch_items_batch ON orchestration_items(batch_id);
    CREATE INDEX IF NOT EXISTS idx_orch_items_cand ON orchestration_items(candidate_id);
    CREATE TABLE IF NOT EXISTS persona_ontology (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'entity',    -- entity | relation
        name TEXT NOT NULL,
        definition TEXT,
        source_candidate_id INTEGER,            -- traceability back to the candidate pool
        status TEXT NOT NULL DEFAULT 'active',  -- active | deprecated
        created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_ont_uniq
        ON persona_ontology(identity_id, kind, name);
    CREATE INDEX IF NOT EXISTS idx_persona_ont_identity
        ON persona_ontology(identity_id);
    CREATE TABLE IF NOT EXISTS assembly_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|discarded
        created_at REAL NOT NULL,
        confirmed_at REAL
    );
    CREATE TABLE IF NOT EXISTS assembly_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id INTEGER NOT NULL,
        candidate_id INTEGER NOT NULL,
        action TEXT NOT NULL,                    -- adopt | exclude
        category TEXT,                           -- core | marginal | irrelevant
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|dismissed
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_asm_items_batch ON assembly_items(batch_id);
    CREATE INDEX IF NOT EXISTS idx_asm_items_cand ON assembly_items(candidate_id);
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        role TEXT NOT NULL,                     -- user | assistant
        content TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chat_identity ON chat_messages(identity_id);
    CREATE TABLE IF NOT EXISTS persona_benchmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        job_id INTEGER,
        model TEXT NOT NULL,                    -- responder (ollama model)
        judge TEXT NOT NULL DEFAULT 'llm2/GLM', -- judge channel
        total INTEGER NOT NULL,                 -- questions count
        stats TEXT,                             -- JSON per-arm counts/rates
        conclusion TEXT,                        -- deterministic template text
        status TEXT NOT NULL DEFAULT 'running', -- running|done|failed
        error TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pbench_identity ON persona_benchmarks(identity_id);
    CREATE TABLE IF NOT EXISTS persona_benchmark_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        benchmark_id INTEGER NOT NULL REFERENCES persona_benchmarks(id) ON DELETE CASCADE,
        quiz_id INTEGER NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,                   -- gold answer
        arm TEXT NOT NULL,                      -- none|ontology|rag|rag_ontology
        reply TEXT,
        verdict TEXT,                           -- correct|partial|wrong|refused
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pbench_items ON persona_benchmark_items(benchmark_id);
    CREATE TABLE IF NOT EXISTS persona_ontology_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        benchmark_id INTEGER NOT NULL REFERENCES persona_benchmarks(id) ON DELETE CASCADE,
        ontology_id INTEGER,                    -- persona_ontology.id (no FK: rollback replaces rows)
        name TEXT NOT NULL,
        kind TEXT,                              -- 'entity' for add; NULL for annotate/update/delete
        action TEXT NOT NULL,                   -- annotate|update|delete|add
        suggested_definition TEXT,              -- update: fixed definition · add: new entity definition
        note TEXT,                              -- annotate: the annotation note
        reason TEXT,                            -- GLM nomination reason
        evidence TEXT,                          -- JSON [{quiz_id, question, reply}]
        status TEXT NOT NULL DEFAULT 'pending', -- pending|merged|rejected
        version_id INTEGER,                     -- set when merged
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_poc_identity ON persona_ontology_changes(identity_id, status);
    CREATE TABLE IF NOT EXISTS persona_ontology_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identity_id INTEGER NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        benchmark_id INTEGER,
        changelog TEXT,                         -- JSON list of applied changes
        snapshot TEXT NOT NULL,                 -- JSON snapshot of persona_ontology rows
        created_at REAL NOT NULL,
        UNIQUE(identity_id, version)
    );
    """)
    # migration: control flag for pause/delete (old DBs lack the column)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(jobs)")]
    if "cancel_flag" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_flag INTEGER NOT NULL DEFAULT 0")
    # migration: ref_id carries a job-scoped reference (e.g. assemble -> identity_id
    # for resume), so re-runs know which persona the job was for.
    if "ref_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN ref_id INTEGER")
    # migration: benchmark annotate action writes a note on persona_ontology rows
    po_cols = [r["name"] for r in conn.execute("PRAGMA table_info(persona_ontology)")]
    if "note" not in po_cols:
        conn.execute("ALTER TABLE persona_ontology ADD COLUMN note TEXT")
    # migration: the `add` action records the kind of the new entity it inserts
    poc_cols = [r["name"] for r in conn.execute("PRAGMA table_info(persona_ontology_changes)")]
    if "kind" not in poc_cols:
        conn.execute("ALTER TABLE persona_ontology_changes ADD COLUMN kind TEXT")
    conn.commit()


def kv_set(conn: sqlite3.Connection, k: str, v) -> None:
    conn.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))
    conn.commit()


def kv_get(conn: sqlite3.Connection, k: str, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def now() -> float:
    return time.time()
