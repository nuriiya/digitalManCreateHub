# -*- coding: utf-8 -*-
"""一次性回填：把 persona_ontology 里未同步进本体库（candidates）的条目补齐。

背景（2026-09-14 诊断）：trainer.add_ontology 此前只写 persona_ontology 不写
candidates → 前端「知识与本体」页看不到训练师教学的条目。实测 146 条里 84 条
未同步。本脚本把缺口补上；trainer.add_ontology 已修复为双写，此后不再积压。

幂等：再跑一次结果不变（已同步的只补 tags 来源标记，不覆盖定义）。
"""
import sys
sys.path.insert(0, "/app/backend")

from app import db, trainer
from app.ontology import _norm_name


def main():
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT po.identity_id, po.kind, po.name, po.definition"
            " FROM persona_ontology po ORDER BY po.id").fetchall()
        synced = existed = 0
        for r in rows:
            norm = _norm_name(r["name"] or "")
            if not norm:
                continue
            has = conn.execute(
                "SELECT 1 FROM candidates WHERE name_norm=? LIMIT 1",
                (norm,)).fetchone()
            before = 1 if has else 0
            trainer._sync_candidate(conn, r["identity_id"], r["kind"],
                                    r["name"], r["definition"])
            if before:
                existed += 1
            else:
                synced += 1
        total_cand = conn.execute("SELECT COUNT(*) n FROM candidates").fetchone()["n"]
        print(f"=== 回填完成 ===")
        print(f"  persona_ontology 扫描：{len(rows)} 条")
        print(f"  本体库已有（只补 tags）：{existed} 条")
        print(f"  新同步入库：{synced} 条")
        print(f"  candidates 现总数：{total_cand}")
        # 验证：还有多少 persona_ontology 条目在 candidates 里查不到
        missing = 0
        for r in rows:
            norm = _norm_name(r["name"] or "")
            if norm and not conn.execute(
                    "SELECT 1 FROM candidates WHERE name_norm=? LIMIT 1",
                    (norm,)).fetchone():
                missing += 1
        print(f"  回填后仍未同步：{missing} 条（应为 0）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()