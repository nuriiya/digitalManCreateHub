# -*- coding: utf-8 -*-
"""调研模块：多源搜索 → 置信度分级 → 跨源聚合排序 → 入库 RAG。

置信度分级是确定性代码裁决（非 LLM 排序），体现「期刊＞会议＞预印本＞学位论文＞
网页」的递减。DBLP 的 type 字段是区分期刊/会议的关键依据（无需代理，国内可直连）。
"""
import hashlib
import json

from . import db, embedding, mcp

# 置信度层级（分数越高越可信）
_CONF = {"期刊": 100, "会议": 80, "预印本": 60, "学位论文": 40, "网页": 20}


def classify_confidence(record: dict) -> tuple[int, str]:
    """根据来源 + type/venue 判定置信度层级。返回 (分数, 层级名)。"""
    source = (record.get("source") or "").lower()
    typ = (record.get("type") or "").lower()
    venue = (record.get("venue") or "").lower()
    # OpenAlex 的 type 是最可靠的分类依据（无需代理、无反爬）
    if source == "openalex":
        if typ in ("article", "review", "editorial", "letter"):
            return _CONF["期刊"], "期刊"
        if "conference" in typ or typ in ("proceedings",):
            return _CONF["会议"], "会议"
        if typ == "preprint":
            return _CONF["预印本"], "预印本"
        if "dissertation" in typ or typ in ("book", "book-chapter",
                                            "reference-entry"):
            return _CONF["学位论文"], "学位论文"
        return _CONF["预印本"], "预印本"  # OpenAlex 其他类型（dataset 等）
    if "journal" in typ:
        return _CONF["期刊"], "期刊"
    if "conference" in typ or "workshop" in typ:
        return _CONF["会议"], "会议"
    if "thesis" in typ or "dissertation" in typ or typ.startswith("book"):
        return _CONF["学位论文"], "学位论文"
    if source == "dblp":
        # DBLP 未归入期刊/会议/论文的（Informal/Reference/Data）→ 预印本
        return _CONF["预印本"], "预印本"
    if source == "arxiv":
        return _CONF["预印本"], "预印本"
    if source == "semantic_scholar":
        return (_CONF["会议"], "会议") if venue else (_CONF["预印本"], "预印本")
    if source == "google_scholar":
        return _CONF["预印本"], "预印本"
    return _CONF["网页"], "网页"


def aggregate(results: list[dict]) -> list[dict]:
    """多源结果聚合：按置信度降序，同置信度按引用数降序。"""
    scored = []
    for r in results:
        score, level = classify_confidence(r)
        scored.append({**r, "confidence": score, "confidence_level": level})
    scored.sort(key=lambda x: (-x["confidence"],
                               -(x.get("citation_count") or 0)))
    return scored


# 搜索源顺序：OpenAlex 优先（无需代理、无反爬、type 可靠），DBLP/arXiv 兜底
_SEARCH_SOURCES = ("search_openalex", "search_dblp", "search_arxiv")


def search_and_aggregate(conn, query: str, mcp_server_id: int,
                         max_results: int = 5) -> dict:
    """调多个 MCP 搜索源，聚合按置信度排序。单源失败不阻断（容错）。"""
    all_results = []
    errors = []
    for tool in _SEARCH_SOURCES:
        r = mcp.call_tool(conn, mcp_server_id, tool,
                          {"query": query, "max_results": max_results})
        if r.get("ok"):
            res = r.get("result")
            if isinstance(res, list):
                all_results.extend(res)
        else:
            errors.append({"tool": tool, "error": r.get("error")})
    aggregated = aggregate(all_results)
    return {"query": query, "total": len(aggregated),
            "results": aggregated, "errors": errors}


def ingest_results(conn, query: str, results: list[dict]) -> dict:
    """把聚合结果入库 RAG：一个「调研主题」文档 + 每条结果一个 chunk。幂等。

    chunk 的 source_meta 存置信度/来源/URL，检索时可按置信度过滤或排序。
    """
    if not results:
        return {"document_id": None, "chunks": 0}
    path = f"research:{query}"
    existing = conn.execute("SELECT id FROM documents WHERE path=?",
                            (path,)).fetchone()
    doc_id = existing["id"] if existing else None
    summary = f"调研主题「{query}」共 {len(results)} 条结果，按置信度递减。"
    if doc_id is None:
        doc_emb = embedding.embed(summary)
        cur = conn.execute(
            "INSERT INTO documents(name, path, content_hash, doc_summary,"
            " embedding, file_type, created_at) VALUES(?,?,?,?,?,?,?)",
            (f"调研：{query}", path,
             hashlib.sha1(path.encode()).hexdigest(), summary,
             doc_emb, "research", db.now()))
        doc_id = cur.lastrowid
    else:
        # 幂等：清旧 chunks 重新入库
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("UPDATE documents SET doc_summary=?, created_at=? WHERE id=?",
                     (summary, db.now(), doc_id))
    n = 0
    for i, r in enumerate(results):
        title = (r.get("title") or "").strip()
        if not title:
            continue
        authors = r.get("authors") or []
        if isinstance(authors, list):
            authors_txt = ", ".join(str(a) for a in authors)
        else:
            authors_txt = str(authors)
        text = "\n".join(filter(None, [
            title, authors_txt, r.get("venue") or "",
            str(r.get("year") or ""), r.get("summary") or ""]))
        emb = embedding.embed(title + ". " + (r.get("summary") or ""))
        source_meta = {
            "source": r.get("source"),
            "confidence": r.get("confidence"),
            "confidence_level": r.get("confidence_level"),
            "url": r.get("url") or r.get("pdf_url") or "",
            "year": r.get("year"),
            "venue": r.get("venue"),
            "doi": r.get("doi"),
        }
        conn.execute(
            "INSERT INTO chunks(doc_id, seq, text, summary, tags, embedding,"
            " content_hash, source_meta) VALUES(?,?,?,?,?,?,?,?)",
            (doc_id, i, text, title,
             [r.get("source") or "", r.get("confidence_level") or ""],
             emb, hashlib.sha1(text.encode()).hexdigest(),
             json.dumps(source_meta, ensure_ascii=False)))
        n += 1
    conn.commit()
    return {"document_id": doc_id, "chunks": n}
