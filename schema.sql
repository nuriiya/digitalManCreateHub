-- RAG 雏形建表脚本（正式模式用；本地模式自动建内存表，无需执行）
-- 与 rag/store.py 中 PGStore._init_schema 保持一致

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    doc_summary TEXT,
    embedding vector(384)
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    summary TEXT,
    tags TEXT[],
    embedding vector(384)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
