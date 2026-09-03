# -*- coding: utf-8 -*-
"""RAG 雏形入口：ingest 导入文档 + query 检索。

用法：
  1. 导入文档：
     python main.py ingest --file 部门文档.txt
     python main.py ingest --text "直接贴一段文字"
  2. 查询（命中 summary，回取原文）：
     python main.py query "报销流程是什么"
     python main.py query "报销流程是什么" --tag 财务
"""
import argparse
import sys

from rag.store import get_store
from rag.pipeline import ingest_document, query


def cmd_ingest(args):
    store = get_store()
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
        name = args.file
    elif args.text:
        text = args.text
        name = "命令行文本"
    else:
        print("请提供 --file 或 --text")
        sys.exit(1)

    result = ingest_document(store, text, name)
    print("=" * 60)
    print(f"文档名：{result['name']}")
    print(f"分段数：{result['chunks']}")
    print(f"文档 ID：{result['doc_id']}")
    print("=" * 60)
    print("整篇 summary：")
    print(result["doc_summary"])
    print("=" * 60)


def cmd_query(args):
    store = get_store()
    hits = query(store, args.question, top_k=args.top_k, tag=args.tag)
    if not hits:
        print("未命中任何摘要。请先 ingest 文档。")
        return
    print("=" * 60)
    print(f"查询：{args.question}" + (f"（标签过滤：{args.tag}）" if args.tag else ""))
    print("=" * 60)
    for i, h in enumerate(hits, 1):
        print(f"\n【命中 {i}】score={h['score']:.4f}  tags={h['tags']}")
        print(f"  summary: {h['summary'][:80]}...")
        print(f"  原文片段: {h['text'][:120]}...")
    print("\n" + "=" * 60)


def main():
    p = argparse.ArgumentParser(description="数字人 RAG 雏形")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="导入文档")
    ing.add_argument("--file", help="文档路径")
    ing.add_argument("--text", help="直接粘贴文本")
    ing.set_defaults(func=cmd_ingest)

    q = sub.add_parser("query", help="检索查询")
    q.add_argument("question")
    q.add_argument("--tag", help="按标签过滤")
    q.add_argument("--top_k", type=int, default=None)
    q.set_defaults(func=cmd_query)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
