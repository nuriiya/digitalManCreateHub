# -*- coding: utf-8 -*-
"""装配 pipeline-factory 元流程到数据库（design §18.3/§18.5）。

幂等：重复运行结果不变（已存在则跳过）。
"""
import sys
sys.path.insert(0, "/app/backend")

from app import db
from app import pipeline_factory as PF


def main():
    conn = db.get_conn()
    try:
        pid = PF.assemble_factory(conn)
        print(f"=== pipeline-factory 落库 ===\n  pipeline_id = {pid}")
        # 详情
        nodes = conn.execute(
            "SELECT node_key, kind, persona_id, step_name FROM pipeline_nodes"
            " WHERE pipeline_id=? ORDER BY id", (pid,)).fetchall()
        rels = conn.execute(
            "SELECT from_node_id, to_node_id, relation_type FROM pipeline_relations"
            " WHERE pipeline_id=? ORDER BY id", (pid,)).fetchall()
        print(f"  节点 {len(nodes)} 条 / 关系 {len(rels)} 条")
        for n in nodes:
            print(f"    {n['node_key']:<4} {n['kind']:<14} {n['step_name']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()