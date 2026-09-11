# -*- coding: utf-8 -*-
"""Verify the chunk content-type system landed (design §11).

Run inside the backend container (or any env with DB access):

    docker exec -w /app/backend rag_backend python scripts/verify_chunk_types.py

Checks:
  1. `chunk_types` table exists and holds the 8 builtin rows;
  2. `chunks` gained the four type columns;
  3. how many chunks are already classified vs still NULL (legacy rows);
  4. `chunk_types.resolve()` decision behaviour on unknown / known codes.
"""
import sys

from app import db, chunk_types


def main() -> int:
    conn = db.get_conn()

    rows = conn.execute(
        "SELECT code, label, default_confidence, default_mandatory,"
        " priority, builtin, status FROM chunk_types"
        " ORDER BY priority DESC, code").fetchall()
    print(f"[1] chunk_types rows = {len(rows)}")
    for r in rows:
        print("    {:<12} {:<6} conf={:<6} mandatory={} {} {}".format(
            r["code"], r["label"], r["default_confidence"],
            r["default_mandatory"],
            "builtin" if r["builtin"] else "custom", r["status"]))

    cols = [r["column_name"] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name='chunks' ORDER BY ordinal_position").fetchall()]
    want = ["type", "type_confidence", "type_mandatory", "type_source"]
    missing = [c for c in want if c not in cols]
    print(f"[2] chunks columns = {len(cols)}；新增四列缺失 = {missing or '无'}")
    print(f"    {cols}")

    stat = conn.execute(
        "SELECT COALESCE(type, '<NULL>') t, COUNT(*) n FROM chunks"
        " GROUP BY 1 ORDER BY n DESC").fetchall()
    print(f"[3] chunks 类型分布（共 "
          f"{sum(r['n'] for r in stat)} 条）：")
    for r in stat:
        print(f"    {r['t']:<12} {r['n']}")

    tmap = chunk_types.type_map(conn)
    print("[4] resolve() 裁决抽样：")
    for code in ("hard_rule", "not_in_vocab", None, ""):
        print(f"    {str(code):<14} -> {chunk_types.resolve(code, tmap)}")
    # 自审 2026-09-11：词表 code 恒为小写，resolve 必须容忍大小写/空白输入
    # （否则 API 层传 "Hard_Rule" 会被静默判为未知类型）。
    norm = [("Hard_Rule", "hard_rule"), ("  hard_rule  ", "hard_rule"),
            ("HARD_RULE", "hard_rule")]
    bad = [src for src, want in norm
           if chunk_types.resolve(src, tmap)["type"] != want]
    print(f"    入参归一化断言（大小写/空白）异常 = {bad or '无'}")
    print(f"    rule_type('必须遵守以下规定') -> "
          f"{chunk_types.rule_type('必须遵守以下规定')}")
    print(f"    rule_type('本季度同比增长 12%') -> "
          f"{chunk_types.rule_type('本季度同比增长 12%')}")

    ok = len(rows) >= len(chunk_types.BUILTIN_TYPES) and not missing and not bad
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
