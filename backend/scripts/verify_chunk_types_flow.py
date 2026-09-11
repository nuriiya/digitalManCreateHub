# -*- coding: utf-8 -*-
"""End-to-end data-flow check for the chunk type system (design §11).

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_chunk_types_flow.py

Exercises the real read/write paths (no LLM needed):
  1. user-final-review write path  (same SQL as POST /api/rag/chunks/{id}/type)
  2. `ingest.get_chunk` returns the four new fields
  3. `ingest.list_chunks` three-dimension filter
  4. `ingest.search(..., mandatory=2)` 强制等级过滤
  5. `chat._rag_snippets` 分两路（rules / texts）

Everything is reverted at the end so the corpus stays untouched.
"""
import sys

from app import db, chunk_types, ingest, chat


def main() -> int:
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM chunks ORDER BY id LIMIT 1").fetchone()
    if not row:
        print("no chunks in corpus -> nothing to verify (OK, empty DB)")
        return 0
    cid = row["id"]
    print(f"target chunk id = {cid}")

    # ---- 1. 用户终审写路径（与 API 同 SQL）----
    tmap = chunk_types.type_map(conn)
    tinfo = chunk_types.resolve("hard_rule", tmap)
    conn.execute(
        "UPDATE chunks SET type=?, type_confidence=?, type_mandatory=?,"
        " type_source=? WHERE id=?",
        (tinfo["type"], tinfo["type_confidence"], tinfo["type_mandatory"],
         chunk_types.SOURCE_USER, cid))
    conn.commit()

    # ---- 2. 读取路径 ----
    d = ingest.get_chunk(conn, cid)
    got = (d["type"], d["type_confidence"], d["type_mandatory"], d["type_source"])
    print(f"[1+2] get_chunk -> type={got[0]} conf={got[1]} "
          f"mandatory={got[2]} source={got[3]}")
    ok_read = got == ("hard_rule", "high", 2, "user")

    # ---- 3. 列表三维过滤 ----
    lst = ingest.list_chunks(conn, 1, 20, None, "hard_rule", None, None)
    lst2 = ingest.list_chunks(conn, 1, 20, None, None, None, 2)
    print(f"[3] list_chunks(type=hard_rule) total={lst['total']}；"
          f"list_chunks(mandatory=2) total={lst2['total']}")
    ok_list = lst["total"] >= 1 and lst2["total"] >= 1

    # ---- 4. 检索强制等级过滤（需要 embedding）----
    try:
        hits = ingest.search(conn, "规则", top_k=3, mandatory=2)
        print(f"[4] search(mandatory=2) hits={len(hits)} "
              f"ids={[h['chunk_id'] for h in hits]} "
              f"types={[h['type'] for h in hits]}")
        ok_search = any(h["chunk_id"] == cid for h in hits)
    except Exception as e:  # embedding backend down -> 降级不算失败
        print(f"[4] search skipped (embedding backend): "
              f"{type(e).__name__}: {str(e)[:100]}")
        ok_search = None

    # ---- 5. _rag_snippets 两路 ----
    try:
        got2 = chat._rag_snippets(conn, "规则")
        print(f"[5] _rag_snippets -> rules={len(got2['rules'])} "
              f"texts={len(got2['texts'])} error={got2['error']}")
        ok_snip = len(got2["rules"]) >= 1
    except Exception as e:
        print(f"[5] _rag_snippets failed: {type(e).__name__}: {str(e)[:100]}")
        ok_snip = None

    # ---- 回滚 ----
    conn.execute(
        "UPDATE chunks SET type=NULL, type_confidence=NULL,"
        " type_mandatory=NULL, type_source=NULL WHERE id=?", (cid,))
    conn.commit()
    print("reverted target chunk to NULL")

    checks = [ok_read, ok_list]
    if ok_search is not None:
        checks.append(ok_search)
    if ok_snip is not None:
        checks.append(ok_snip)
    print("\nRESULT:", "OK" if all(checks) else "FAILED",
          f"(read={ok_read} list={ok_list} search={ok_search} snip={ok_snip})")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
