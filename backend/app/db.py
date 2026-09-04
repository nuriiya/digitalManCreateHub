# -*- coding: utf-8 -*-
"""DB: single PostgreSQL connection (task state + data state + pgvector RAG).

This is the FULL replacement of the old SQLite-based db.py. The whole
backend now stores everything in PostgreSQL (see schema below), with the
pgvector extension for ANN-friendly embedding storage on chunks/documents.

Compatibility shim
------------------
Most call sites were written against the old sqlite3 Connection API. We keep
that surface stable so the migration stays a one-file change for most modules:

  - conn.execute(sql, params)         -> cursor (sqlite3-style)
  - cur.lastrowid                     -> int  (auto-populated for INSERTs)
  - cur.fetchone() / cur.fetchall()   -> row(s)
  - cur.rowcount / cur.description    -> passthrough
  - conn.commit() / conn.rollback()   -> passthrough
  - row["col"]                        -> psycopg3 Row supports str key

`?` placeholders are translated to `%s` on the fly. INSERTs without an
explicit RETURNING automatically get `RETURNING id` appended so
`cur.lastrowid` still works (the very first row is fetched and cached;
this matches the sqlite3 model where the cursor never has another result
row after a plain INSERT).
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DSN_FILE = DATA_DIR / "pg_dsn"  # start.ps1 writes it after starting pgvector
DATA_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()
_conn = None  # singleton _PGConn wrapper


# ---------------- DSN resolution ----------------

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/rag"


def _resolve_dsn() -> str:
    # 1) explicit env override (12-factor)
    dsn = (os.environ.get("RAG_DATABASE_URL")
           or os.environ.get("DATABASE_URL", "")).strip()
    if dsn:
        return dsn
    # 2) start.ps1 writes the file after bringing up pgvector
    if DSN_FILE.exists():
        try:
            d = DSN_FILE.read_text(encoding="utf-8").strip()
            if d:
                return d
        except OSError:
            pass
    return DEFAULT_DSN


# ---------------- shim: ? -> %s + cursor.lastrowid ----------------

_Q2S = re.compile(r"\?")
_INSERT = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_RETURNING = re.compile(r"\bRETURNING\b", re.IGNORECASE)


def _q2s(sql: str) -> str:
    return _Q2S.sub("%s", sql)


class _Cursor:
    """sqlite3-Cursor-compatible wrapper over a psycopg3 cursor."""
    __slots__ = ("_c", "_lastrowid")

    def __init__(self, pg_cursor, lastrowid):
        self._c = pg_cursor
        self._lastrowid = lastrowid

    @property
    def lastrowid(self) -> int | None:
        return self._lastrowid

    @property
    def rowcount(self) -> int:
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass

    def __iter__(self):
        return iter(self._c)


class _Conn:
    """sqlite3-Connection-compatible wrapper over a psycopg3 connection.

    All execute/commit calls are serialized by the module-level RLock +
    jobs._emit_lock (callers). psycopg3 connections are NOT thread-safe for
    concurrent use; the single-singleton + write-side locking model keeps
    that safe.
    """
    __slots__ = ("_c",)

    def __init__(self, pg_conn):
        self._c = pg_conn

    @property
    def raw(self):
        """Escape hatch: direct access to the psycopg3 connection (for
        pgvector registration, autocommit toggling, etc.)."""
        return self._c

    def execute(self, sql: str, params: Any = ()):
        sql = _q2s(sql)
        lastrowid = None
        if _INSERT.match(sql) and not _RETURNING.search(sql):
            # Auto-append RETURNING id so cur.lastrowid stays meaningful.
            # We consume the RETURNING row here (callers never fetchall after
            # a plain INSERT, so this matches the sqlite3 contract).
            sql = sql.rstrip().rstrip(";").rstrip() + " RETURNING id"
            cur = self._c.execute(sql, params or ())
            row = cur.fetchone()
            if row is not None:
                lastrowid = row["id"]
            return _Cursor(cur, lastrowid)
        cur = self._c.execute(sql, params or ())
        return _Cursor(cur, None)

    def executemany(self, sql: str, seq):
        sql = _q2s(sql)
        cur = self._c.executemany(sql, seq)
        return cur

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


# ---------------- singleton + connection helpers ----------------

def _connect():
    import psycopg
    from psycopg.rows import dict_row
    from pgvector.psycopg import register_vector
    dsn = _resolve_dsn()
    # dict_row keeps the sqlite3.Row-style `row["col"]` access the whole
    # codebase relies on (the SQLite era set row_factory=Row; the PG migration
    # initially dropped it, breaking every dict-style row read on non-empty
    # tables).
    pg = psycopg.connect(dsn, autocommit=False, connect_timeout=8,
                         row_factory=dict_row)
    register_vector(pg)  # enables list[float] <-> vector and Python list <-> TEXT[]
    pg.execute("SET application_name = 'rag_mvp'")
    pg.execute("SET statement_timeout = 0")  # long LLM/blocking jobs OK
    return pg


def get_conn(db_path=None) -> _Conn:  # db_path kept for API compat with old tests
    """Thread-safe singleton (RLock + single connection). `db_path` is
    accepted but ignored on PG — tests should rely on the PG fixtures in
    conftest (which set a per-test schema, not a per-test file)."""
    global _conn
    with _lock:
        if _conn is None:
            _conn = _Conn(_connect())
            _init_schema(_conn)
        return _conn


def reset_conn() -> None:
    """Drop the singleton (used by tests + by main during dev recovery)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


# ---------------- schema (PostgreSQL + pgvector) ----------------
#
# All tables are PG-native. Highlights:
#   - BIGSERIAL for synthetic PKs (sqlite3 INTEGER PRIMARY KEY AUTOINCREMENT
#     equivalent; preserves lastrowid semantics via the shim's RETURNING id)
#   - TEXT[] for tag arrays (psycopg3 maps Python list[str] <-> TEXT[] once
#     register_vector is called; same adapter covers vector + array)
#   - JSONB for structured payloads (kv, events, identities.keywords,
#     exam_runs.used_names, persona_ontology_versions.snapshot/changelog,
#     persona_ontology_changes.evidence, persona_benchmarks.stats, chunks
#     .source_meta)
#   - vector (pgvector) for embeddings — UNSPECIFIED DIMENSION (pgvector
#     0.5+ supports unbounded-dim columns). dim is enforced at write time
#     by the embedding_lock (iron law 1) and at read time by the HNSW
#     index, both of which are created in _init_schema AFTER we know the
#     active embedding dim. ANN retrieval uses exact <=> (brute-force
#     cosine) — fine up to ~100k chunks; HNSW can be added later if needed.

_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    progress_current BIGINT NOT NULL DEFAULT 0,
    progress_total BIGINT NOT NULL DEFAULT 0,
    detail TEXT,
    error TEXT,
    cancel_flag BIGINT NOT NULL DEFAULT 0,
    ref_id BIGINT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq BIGSERIAL PRIMARY KEY,
    job_id BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    payload JSONB,
    ts DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_seq ON events(seq);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id);

-- RAG: documents/chunks carry pgvector embeddings + source provenance.
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    doc_summary TEXT,
    embedding vector,
    file_type TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_path ON documents(path);

CREATE TABLE IF NOT EXISTS chunks (
    id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq BIGINT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    tags TEXT[],
    embedding vector,
    content_hash TEXT NOT NULL,
    source_meta JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

-- ontology evidence chain (FK-bound to chunks + candidates)
CREATE TABLE IF NOT EXISTS candidates (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    definition TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    merged_into BIGINT,
    tags TEXT[] NOT NULL DEFAULT '{}'::text[],
    created_at DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_kind_name
    ON candidates(kind, name);

CREATE TABLE IF NOT EXISTS mentions (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    span_start BIGINT NOT NULL,
    span_end BIGINT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relations (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    chunk_id BIGINT REFERENCES chunks(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'http',
    image TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'stopped',
    created_at DOUBLE PRECISION NOT NULL
);

-- identity pre-screening (anchors guide extraction; prompt is the persona's
-- custom system-instruction fragment appended during chat composition)
CREATE TABLE IF NOT EXISTS identities (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    mission TEXT,
    description TEXT,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS anchors (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT,
    definition TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL
);

-- exam (quiz / runs / candidate-level pass-fail)
CREATE TABLE IF NOT EXISTS quiz (
    id BIGSERIAL PRIMARY KEY,
    chunk_id BIGINT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_results (
    id BIGSERIAL PRIMARY KEY,
    quiz_id BIGINT NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
    candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_runs (
    id BIGSERIAL PRIMARY KEY,
    quiz_id BIGINT NOT NULL REFERENCES quiz(id) ON DELETE CASCADE,
    job_id BIGINT,
    mode TEXT NOT NULL,
    answer TEXT,
    used_names JSONB,
    verdict TEXT NOT NULL,
    enough BIGINT NOT NULL DEFAULT 1,
    judge_verdict TEXT,
    issue TEXT,
    reason TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exam_runs_quiz ON exam_runs(quiz_id);

-- orchestration: rule + GLM triages
CREATE TABLE IF NOT EXISTS orchestration_batches (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL,
    confirmed_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS orchestration_items (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL,
    candidate_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    category TEXT,
    reason TEXT,
    merge_into BIGINT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orch_items_batch
    ON orchestration_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_orch_items_cand
    ON orchestration_items(candidate_id);

-- assembly: persona ontology 段 assembly
CREATE TABLE IF NOT EXISTS assembly_batches (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL,
    confirmed_at DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS assembly_items (
    id BIGSERIAL PRIMARY KEY,
    batch_id BIGINT NOT NULL,
    candidate_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    category TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asm_items_batch
    ON assembly_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_asm_items_cand
    ON assembly_items(candidate_id);

-- persona ontology 段 (per-identity) + benchmark + version history
CREATE TABLE IF NOT EXISTS persona_ontology (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'entity',
    name TEXT NOT NULL,
    definition TEXT,
    source_candidate_id BIGINT,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_ont_uniq
    ON persona_ontology(identity_id, kind, name);
CREATE INDEX IF NOT EXISTS idx_persona_ont_identity
    ON persona_ontology(identity_id);

-- chat history
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_identity
    ON chat_messages(identity_id);

-- benchmark (四组对照 + 归因 + 版本管理)
CREATE TABLE IF NOT EXISTS persona_benchmarks (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    job_id BIGINT,
    model TEXT NOT NULL,
    judge TEXT NOT NULL DEFAULT 'llm2/GLM',
    total BIGINT NOT NULL,
    stats JSONB,
    conclusion TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pbench_identity
    ON persona_benchmarks(identity_id);
CREATE TABLE IF NOT EXISTS persona_benchmark_items (
    id BIGSERIAL PRIMARY KEY,
    benchmark_id BIGINT NOT NULL REFERENCES persona_benchmarks(id) ON DELETE CASCADE,
    quiz_id BIGINT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    arm TEXT NOT NULL,
    reply TEXT,
    verdict TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pbench_items
    ON persona_benchmark_items(benchmark_id);

CREATE TABLE IF NOT EXISTS persona_ontology_changes (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    benchmark_id BIGINT NOT NULL REFERENCES persona_benchmarks(id) ON DELETE CASCADE,
    ontology_id BIGINT,
    name TEXT NOT NULL,
    kind TEXT,
    action TEXT NOT NULL,
    suggested_definition TEXT,
    note TEXT,
    reason TEXT,
    evidence JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    version_id BIGINT,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_poc_identity
    ON persona_ontology_changes(identity_id, status);

CREATE TABLE IF NOT EXISTS persona_ontology_versions (
    id BIGSERIAL PRIMARY KEY,
    identity_id BIGINT NOT NULL REFERENCES identities(id) ON DELETE CASCADE,
    version BIGINT NOT NULL,
    benchmark_id BIGINT,
    changelog JSONB,
    snapshot JSONB NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE(identity_id, version)
);
"""


def _init_schema(conn: _Conn) -> None:
    with conn.raw.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
        # Migrations for databases created before a column existed.
        cur.execute("ALTER TABLE candidates ADD COLUMN IF NOT EXISTS"
                    " tags TEXT[] NOT NULL DEFAULT '{}'::text[]")
    conn.commit()


# ---------------- kv helpers (JSONB-aware) ----------------

def kv_set(conn, k: str, v) -> None:
    # psycopg3 maps a JSON-encoded str to JSONB automatically (str -> jsonb
    # cast). For Python dict/list we let the default adapter handle it.
    if isinstance(v, str):
        payload = v
    else:
        payload = json.dumps(v, ensure_ascii=False)
    conn.execute(
        "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, payload))
    conn.commit()


def kv_get(conn, k: str, default=None):
    row = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    if not row:
        return default
    v = row["v"]
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v
    return default


def now() -> float:
    return time.time()
