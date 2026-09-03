# -*- coding: utf-8 -*-
"""存储层：双模式。

- MemoryStore：本地演示，无外部依赖，数据在内存里。
- PGStore：正式模式，PostgreSQL + pgvector。

两张表：
  documents(id, name, doc_summary, doc_embedding)
  chunks(id, doc_id, seq, text, summary, tags, embedding)

检索时先在 summary 层做向量相似度，命中后沿 doc_id/seq 回取原文。
"""
import config


class MemoryStore:
    """内存存储（本地演示模式）。"""

    def __init__(self):
        self.documents = []
        self.chunks = []
        self._doc_id = 0
        self._chunk_id = 0

    def add_document(self, name: str, doc_summary: str, doc_emb: list[float]) -> int:
        self._doc_id += 1
        self.documents.append({
            "id": self._doc_id, "name": name,
            "doc_summary": doc_summary, "embedding": doc_emb,
        })
        return self._doc_id

    def add_chunk(self, doc_id: int, seq: int, text: str, summary: str,
                  tags: list[str], emb: list[float]) -> int:
        self._chunk_id += 1
        self.chunks.append({
            "id": self._chunk_id, "doc_id": doc_id, "seq": seq,
            "text": text, "summary": summary, "tags": tags, "embedding": emb,
        })
        return self._chunk_id

    def search_chunks(self, query_emb: list[float], top_k: int, tag: str = None):
        """在段 summary 层做余弦检索，返回 [{chunk_id, summary, tags, score}]"""
        from rag.embedding import cosine
        scored = []
        for c in self.chunks:
            if tag and tag not in c["tags"]:
                continue
            scored.append((c, cosine(query_emb, c["embedding"])))
        scored.sort(key=lambda x: -x[1])
        return [{"chunk_id": c["id"], "doc_id": c["doc_id"], "seq": c["seq"],
                 "summary": c["summary"], "tags": c["tags"],
                 "text": c["text"], "score": s} for c, s in scored[:top_k]]

    def get_document_summary(self, doc_id: int):
        for d in self.documents:
            if d["id"] == doc_id:
                return d
        return None


class PGStore:
    """PostgreSQL + pgvector 存储（正式模式）。"""

    def __init__(self, dsn: str):
        import psycopg
        from pgvector.psycopg import register_vector
        self.conn = psycopg.connect(dsn)
        register_vector(self.conn)

    def _init_schema(self):
        """建表（幂等）。vector 维度与 config.EMBED_DIM 一致。"""
        dim = config.EMBED_DIM
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS documents (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    doc_summary TEXT,
                    embedding vector({dim})
                )
            """)
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    id SERIAL PRIMARY KEY,
                    doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    summary TEXT,
                    tags TEXT[],
                    embedding vector({dim})
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                ON chunks USING hnsw (embedding vector_cosine_ops)
            """)
        self.conn.commit()

    def add_document(self, name: str, doc_summary: str, doc_emb: list[float]) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (name, doc_summary, embedding) VALUES (%s,%s,%s) RETURNING id",
                (name, doc_summary, doc_emb))
            return cur.fetchone()[0]

    def add_chunk(self, doc_id: int, seq: int, text: str, summary: str,
                  tags: list[str], emb: list[float]) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chunks (doc_id, seq, text, summary, tags, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (doc_id, seq, text, summary, tags, emb))
            return cur.fetchone()[0]

    def search_chunks(self, query_emb: list[float], top_k: int, tag: str = None):
        with self.conn.cursor() as cur:
            if tag:
                cur.execute(
                    "SELECT id, doc_id, seq, text, summary, tags, "
                    "1 - (embedding <=> %s) AS score FROM chunks "
                    "WHERE %s = ANY(tags) ORDER BY embedding <=> %s LIMIT %s",
                    (query_emb, tag, query_emb, top_k))
            else:
                cur.execute(
                    "SELECT id, doc_id, seq, text, summary, tags, "
                    "1 - (embedding <=> %s) AS score FROM chunks "
                    "ORDER BY embedding <=> %s LIMIT %s",
                    (query_emb, query_emb, top_k))
            rows = cur.fetchall()
        return [{"chunk_id": r[0], "doc_id": r[1], "seq": r[2], "text": r[3],
                 "summary": r[4], "tags": r[5], "score": float(r[6])} for r in rows]

    def get_document_summary(self, doc_id: int):
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, name, doc_summary FROM documents WHERE id=%s", (doc_id,))
            r = cur.fetchone()
        return {"id": r[0], "name": r[1], "doc_summary": r[2]} if r else None


def get_store():
    """按配置返回存储实例。"""
    if config.DATABASE_URL:
        store = PGStore(config.DATABASE_URL)
        store._init_schema()
        return store
    return MemoryStore()
