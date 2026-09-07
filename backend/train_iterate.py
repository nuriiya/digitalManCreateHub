# -*- coding: utf-8 -*-
"""批量跑能力题 + 统计通过率（训练迭代度量）。

用法：backend 目录下
    python train_iterate.py <identity_id> <start_task_id> <n>
示例：
    python train_iterate.py 3 1 10   # 代码工程师跑 HumanEval 前 10 题
"""
import sys

from app import db, capability


def batch_run(identity_id: int, task_ids: list[int], provider: str = "llm2") -> list[tuple[int, str]]:
    conn = db.get_conn()
    results = []
    for tid in task_ids:
        r = capability.run_for_identity(conn, identity_id, tid, provider=provider)
        results.append((tid, r.get("verdict")))
        print(f"  task {tid}: {r.get('verdict', r.get('error'))}", flush=True)
    return results


if __name__ == "__main__":
    identity_id = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    task_ids = list(range(start, start + n))
    print(f"=== 数字人 {identity_id} 跑能力题 {start}..{start + n - 1} ===", flush=True)
    results = batch_run(identity_id, task_ids)
    passed = sum(1 for _, v in results if v == "pass")
    print(f"=== 通过率: {passed}/{n} = {passed / n:.2%} ===", flush=True)
