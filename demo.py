# -*- coding: utf-8 -*-
"""本地演示脚本：单进程内导入多篇文档并查询，验证 RAG 提取管线全链路。

用法：python demo.py
（无需任何第三方依赖，summary 走规则降级、向量走本地 hash）
"""
from rag.store import MemoryStore
from rag.pipeline import ingest_document, query

DOCS = [
    ("报销流程.txt", "员工报销流程：员工填写报销单，提交部门经理审批，财务审核发票后付款。"
      "报销需在30天内提交，超期需说明理由。差旅报销需附行程单和住宿发票。"
      "单笔超过5000元的报销需总监审批。"),
    ("IT设备申请.txt", "IT系统申请流程：员工登录OA系统，填写设备申请单，IT部门审核后发放设备。"
      "VPN权限需直属领导审批。服务器资源申请需走工单系统，紧急故障可直接拨打IT热线。"
      "新员工入职当天由IT统一配发笔记本电脑。"),
    ("绩效考核.txt", "绩效考核流程：员工每季度填写自评，直属领导评分，HR汇总后进行绩效面谈。"
      "年度绩效与调薪、晋升挂钩。绩效申诉需在结果公布后10个工作日内向HR提出。"),
]

QUERIES = [
    "报销需要什么材料？",
    "超过多少钱需要总监审批？",
    "新员工电脑怎么领？",
    "绩效申诉的期限是多久？",
]


def main():
    store = MemoryStore()
    print("#" * 64)
    print("# 数字人 RAG 雏形 · 本地演示")
    print("#" * 64)

    print("\n===== ① 导入文档（分段 → summary+标签 → 聚合整篇 summary）=====\n")
    for name, text in DOCS:
        r = ingest_document(store, text, name)
        print(f"文档《{name}》→ {r['chunks']} 段")
        print(f"  整篇 summary: {r['doc_summary'][:50]}...\n")

    print("===== ② 检索（命中 summary → 回取原文）=====\n")
    for q in QUERIES:
        hits = query(store, q, top_k=2)
        print(f"Q: {q}")
        if not hits:
            print("   （未命中）\n")
            continue
        for h in hits:
            print(f"   · score={h['score']:.4f} tags={h['tags']}")
            print(f"     summary: {h['summary'][:45]}...")
            print(f"     原文: {h['text'][:60]}...")
        print()

    print("===== ③ 标签过滤（验证 summary 打标签可分类）=====\n")
    hits = query(store, "审批", top_k=5, tag="财务")
    print(f"标签过滤「财务」命中 {len(hits)} 条：")
    for h in hits:
        print(f"   · {h['summary'][:40]}... tags={h['tags']}")


if __name__ == "__main__":
    main()
