# -*- coding: utf-8 -*-
"""Pipeline 版本族归档（2026-09-14）。

按用户指令：
- 保留 #44（run#33 命中 98.0% 阈值）为唯一 canonical pipeline
- 其余（DFMEA 历次迭代）归入 #44 的 family，作为归档版本
- #1「自动写代码 pipeline」属不同领域，自成 family

操作幂等：再跑一次结果不变。
"""
import sys
sys.path.insert(0, "/app/backend")

from app import db

CANONICAL_PID = 44


def _classify(name: str) -> int:
    """返回该 pipeline 应当归入的 family root。非 DFMEA 域自成 family（id=自身）。"""
    n = (name or "").lower()
    if any(k in n for k in ("dfmea", "wifi", "bluetooth")):
        return CANONICAL_PID
    return 0  # 由调用方处理「自成 family」


def main():
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "SELECT id, name, status, is_archived, family_id FROM pipelines ORDER BY id")
        rows = [dict(r) for r in cur.fetchall()]

        moved = 0
        canonical_set = 0
        for r in rows:
            pid = r["id"]
            if pid == CANONICAL_PID:
                # 确保 #44 是 canonical：未归档、family 指向自己
                conn.execute(
                    "UPDATE pipelines SET is_archived=false, family_id=?, parent_version_id=NULL"
                    " WHERE id=?",
                    (CANONICAL_PID, pid))
                canonical_set += 1
                continue
            family = _classify(r["name"])
            if family == 0:
                # 非 DFMEA 域：自成 family 并归档
                conn.execute(
                    "UPDATE pipelines SET is_archived=true, family_id=? WHERE id=?",
                    (pid, pid))
            else:
                conn.execute(
                    "UPDATE pipelines SET is_archived=true, family_id=?"
                    " WHERE id=?",
                    (family, pid))
            moved += 1
        conn.commit()

        print(f"=== 归档完成 ===")
        print(f"canonical 设置（#44）: {canonical_set}")
        print(f"迁移为归档版本        : {moved}")
        print(f"未处理                : 0")
        print()
        print("=== 当前 pipeline 列表（按 family 分组）===")
        cur = conn.execute(
            "SELECT id, name, is_archived, family_id FROM pipelines"
            " ORDER BY family_id NULLS FIRST, is_archived DESC, id")
        for r in cur.fetchall():
            d = dict(r)
            tag = "CANONICAL" if d["id"] == CANONICAL_PID else ("archived" if d["is_archived"] else "active")
            print(f"  #{d['id']:>3} {tag:<10} family={d['family_id']}  {d['name']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()