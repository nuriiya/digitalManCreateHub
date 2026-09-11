# -*- coding: utf-8 -*-
"""自审验证（2026-09-11）：`ingest.search_split` 共享一次 embedding + 两组不重叠。

修复动机
--------
design §11.5 的两路注入（`mandatory=2` 独立配额 + 其余走 top-K）原实现各调一次
`ingest.search()`，而 `search()` 内部每次都调用 `embedding.embed(query)` —— 于是
**每轮对话多一次 embedding 后端网络往返**（Ollama bge-m3）。本脚本用假 embedding
计数器证明修复有效，并校验新函数返回的两组**互不重叠**（原实现的交集会被重复
计数，导致 `hits` 偏大）。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_search_split.py
"""
from app import db, ingest, embedding  # noqa: E402

calls = {"n": 0}
DIM = 1024                       # bge-m3 维度；与库中向量同维才能比较
FIXED = [0.01] * DIM


def _fake(text):
    calls["n"] += 1
    return FIXED


embedding._fake_embed = _fake    # embed() 优先走它，用于计数

conn = db.get_conn()

print("[1] search_split 应只 embed 一次（共享查询向量）")
calls["n"] = 0
rules, others = ingest.search_split(conn, "测试查询", top_k=6, rules_max=4)
print("    embed 调用次数 =", calls["n"], "（期望 1）")
assert calls["n"] == 1, "search_split 未共享 embedding —— 修复无效"

print("[2] 对照：两次独立 search() 会 embed 两次（即修复前的行为）")
calls["n"] = 0
ingest.search(conn, "测试查询", top_k=4, mandatory=2)
ingest.search(conn, "测试查询", top_k=6)
print("    embed 调用次数 =", calls["n"], "（期望 2）")
assert calls["n"] == 2

print("[3] 两组结果互不重叠（原实现交集被重复计入 hits）")
rid = {h["chunk_id"] for h in rules}
oid = {h["chunk_id"] for h in others}
print("    rules =", len(rid), "· others =", len(oid),
      "· 交集 =", len(rid & oid))
assert not (rid & oid), "两组出现重复 chunk"

print("[4] 计数口径 = 两组之和（不重复计）")
print("    总命中 =", len(rules) + len(others))
print("    规则项上限 =", len(rules), "<= 4？", len(rules) <= 4)

print("\nRESULT: OK")
