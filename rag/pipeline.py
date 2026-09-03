# -*- coding: utf-8 -*-
"""RAG 提取管线：分段 → 段 summary+标签 → 聚合整篇 summary → 入库。

流程（先全部算完，再统一入库，避免回填）：
  1. chunking 分段
  2. 每段 LLM 生成 summary + 标签，并对 summary 做 embedding
  3. 多个段 summary 聚合出整篇 summary，做 embedding
  4. 插入 document 得 doc_id，再插入 chunks 关联 doc_id

检索：query() 向量命中 summary，返回对应原文供 LLM 引用。
"""
import config
from rag.chunking import chunk_text
from rag.llm import summarize_chunk, summarize_document
from rag.embedding import embed


def ingest_document(store, text: str, name: str) -> dict:
    """完整走一遍 RAG 提取管线，返回统计信息。"""
    chunks = chunk_text(text)
    if not chunks:
        return {"name": name, "chunks": 0, "doc_id": None, "doc_summary": ""}

    # ① 每段 summary + 标签 + embedding（纯计算，不入库）
    prepared = []
    for c in chunks:
        s = summarize_chunk(c["text"])
        prepared.append({
            "seq": c["index"],
            "text": c["text"],
            "summary": s["summary"],
            "tags": s["tags"],
            "emb": embed(s["summary"]),  # 对 summary 做 embedding，作为检索入口
        })

    # ② 聚合整篇 summary
    doc_summary = summarize_document([p["summary"] for p in prepared])
    doc_emb = embed(doc_summary)

    # ③ 入库：先 document，再 chunks
    doc_id = store.add_document(name, doc_summary, doc_emb)
    for p in prepared:
        store.add_chunk(doc_id, p["seq"], p["text"], p["summary"], p["tags"], p["emb"])

    return {"name": name, "chunks": len(chunks), "doc_id": doc_id,
            "doc_summary": doc_summary}


def query(store, question: str, top_k: int = None, tag: str = None) -> list[dict]:
    """检索：命中 summary 后返回对应原文，供 LLM 引用。"""
    top_k = top_k or config.TOP_K
    q_emb = embed(question)
    return store.search_chunks(q_emb, top_k, tag=tag)
