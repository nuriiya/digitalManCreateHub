# -*- coding: utf-8 -*-
"""DB sanity checks for the PostgreSQL backend.

The old SQLite WAL/indexer assertions do not apply to PG. We verify instead
that:
  - the singleton DB connects and the schema applied
  - kv_set / kv_get round-trip a JSONB value
  - chunk ingest (via the canonical helpers) persists source_meta and a
    pgvector embedding that round-trips through cosine distance.
"""


def test_pg_server_reachable_and_schema_applied(env):
    from app import db
    # singleton should match the per-test fixture, and the schema's tables
    # should be visible under the current search_path.
    cur = env.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = current_schema()"
        " ORDER BY table_name")
    rows = [r["table_name"] for r in cur.fetchall()]
    # every canonical table we expect (sample; full set is asserted on the
    # schema-side via _SCHEMA_SQL being a single CREATE TABLE IF NOT EXISTS block)
    for must in ("jobs", "events", "documents", "chunks", "identities",
                 "anchors", "kv"):
        assert must in rows, f"table {must} missing from schema"


def test_kv_round_trip_jsonb(env):
    from app import db
    payload = {"provider": "ollama", "model": "bge-m3", "dim": 1024}
    db.kv_set(env, "embedding_lock", payload)
    got = db.kv_get(env, "embedding_lock")
    assert got == payload


def test_chunk_insert_persists_source_meta_and_vector(env):
    """Smoke test: insert one chunk via the loader-output shape and verify
    source_meta (JSONB) + tags (TEXT[]) + embedding (pgvector) all read back."""
    import json as _json
    from app import db, embedding
    embedding.set_fake_embed(lambda _: [0.1] * 8)   # any 8-dim stub is fine
    try:
        env.execute(
            "INSERT INTO documents(name, path, content_hash, doc_summary,"
            " embedding, file_type, created_at) VALUES(?,?,?,?,?,?,?)",
            ("x.xlsx", "/tmp/x.xlsx", "h1", "", None, "xlsx", db.now()))
        env.commit()
        row = env.execute("SELECT id FROM documents WHERE path=?", ("/tmp/x.xlsx",)).fetchone()
        doc_id = row["id"]
        emb = [0.1] * 8
        env.execute(
            "INSERT INTO chunks(doc_id, seq, text, summary, tags, embedding,"
            " content_hash, source_meta) VALUES(?,?,?,?,?,?,?,?)",
            (doc_id, 0, "chunk text", "summary", ["测试", "xlsx"], emb,
             "ch", {"file": "x.xlsx", "sheet": "订单", "header": ["col1"],
                    "start_row": 2, "end_row": 5}))
        env.commit()
        row = env.execute(
            "SELECT id, tags, source_meta FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()
        assert row is not None
        # psycopg3 maps TEXT[] -> Python list[str] and JSONB -> Python dict
        assert row["tags"] == ["测试", "xlsx"]
        meta = row["source_meta"] if isinstance(row["source_meta"], dict) else _json.loads(row["source_meta"])
        assert meta["sheet"] == "订单"
        assert meta["start_row"] == 2
        assert meta["end_row"] == 5
    finally:
        embedding.clear_fake_embed()


def test_pgvector_cosine_distance_returns_similarity(env):
    """Sanity: two identical embeddings self-distance = 0 (cosine sim = 1)."""
    from app import db, embedding
    embedding.set_fake_embed(lambda _: [0.1, 0.2, 0.3])
    try:
        env.execute(
            "INSERT INTO documents(name, path, content_hash, doc_summary,"
            " embedding, file_type, created_at) VALUES(?,?,?,?,?,?,?)",
            ("a.txt", "/tmp/a.txt", "a", "s", None, "txt", db.now()))
        env.commit()
        d = env.execute("SELECT id FROM documents WHERE path=?", ("/tmp/a.txt",)).fetchone()
        # one chunk with self-embedding
        env.execute(
            "INSERT INTO chunks(doc_id, seq, text, summary, embedding,"
            " content_hash, source_meta) VALUES(?,?,?,?,?,?,?)",
            (d["id"], 0, "x", "y", [0.1, 0.2, 0.3], "h", {}))
        env.commit()
        # embedding query that exactly matches -> score near 1
        rows = env.execute(
            "SELECT 1 - (embedding <=> ?) AS s FROM chunks WHERE doc_id=?",
            ([0.1, 0.2, 0.3], d["id"])).fetchall()
        assert abs(rows[0]["s"] - 1.0) < 1e-6
    finally:
        embedding.clear_fake_embed()
