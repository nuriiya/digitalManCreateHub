# -*- coding: utf-8 -*-
"""验证 DMP 协议层（design §10）。

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_protocol.py

覆盖：信封 render/parse 往返（C1）、legacy 兼容（C4）、parse_json 五类输入、
StreamJsonReader 增量解码（C3）、DMP_MODE 开关（C5）、kind 闭集完整性。
"""
import json
import os
import sys

from app import protocol as P


def main() -> int:
    checks = []

    print("=== 1. 信封 render / parse 往返（C1）===")
    env = P.pack_question("报销流程是什么？", session_id=7, turn=3)
    msg = P.render(env)
    print("  render.role =", msg["role"], "| content 类型 =", type(msg["content"]).__name__)
    back = P.parse(msg["content"])
    print("  parse.kind =", back.kind, "| text =", back.text())
    checks.append(msg["role"] == "user" and isinstance(msg["content"], str)
                  and back.kind == "question" and back.text() == "报销流程是什么？")

    print("\n=== 2. legacy 兼容（C4：旧纯文本会话）===")
    leg = P.parse("这是一段没有 JSON 的旧回复")
    print("  kind =", leg.kind, "| legacy =", leg.meta.get("legacy"), "| text =", leg.text())
    checks.append(leg.kind == "answer" and leg.meta.get("legacy") is True
                  and leg.text() == "这是一段没有 JSON 的旧回复")

    print("\n=== 3. parse_json（唯一抠取实现，5 类输入）===")
    cases = [
        ('{"a":1}', {"a": 1}, "裸 JSON"),
        ('```json\n{"b":2}\n```', {"b": 2}, "``` 围栏"),
        ('好的，结果如下：\n{"c":3}\n以上。', {"c": 3}, "前后杂文"),
        ('[{"d":4}]', [{"d": 4}], "数组"),
    ]
    for text, want, label in cases:
        got = P.parse_json(text)
        print(f"  {label:10} -> {got}")
        checks.append(got == want)
    bad = P.parse_json("完全不是 JSON")
    print(f"  {'非法输入':10} -> {bad}")
    checks.append(bad is None)

    print("\n=== 4. StreamJsonReader 增量解码（C3：不必等完整 JSON）===")
    doc = json.dumps({"v": 1, "kind": "answer",
                      "payload": {"text": "第一行\n第二\"行\"", "refused": False}},
                     ensure_ascii=False)
    r = P.StreamJsonReader()
    out = ""
    for i in range(0, len(doc), 5):        # 模拟逐 token：每 5 字符一片
        out += r.feed(doc[i:i + 5])
    print("  增量拼接 =", repr(out))
    print("  结束标记 done =", r.done)
    env2 = r.finish()
    print("  finish ->", None if env2 is None else (env2.kind, env2.text()))
    checks.append(out == '第一行\n第二"行"' and r.done
                  and env2 is not None and env2.kind == "answer")

    print("\n=== 5. DMP_MODE 开关（C5）===")
    print("  默认 =", P.dmp_enabled())
    os.environ["DMP_MODE"] = "off"
    print("  off  =", P.dmp_enabled())
    checks.append(P.dmp_enabled() is False)
    os.environ.pop("DMP_MODE", None)

    print("\n=== 6. kind 闭集 ===")
    print("  ", P.KINDS)
    print("   共", len(P.KINDS), "类")
    checks.append(len(P.KINDS) == 10)

    ok = all(checks)
    print(f"\nRESULT: {'OK' if ok else 'FAILED'}  ({sum(checks)}/{len(checks)} 通过)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
