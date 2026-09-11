# -*- coding: utf-8 -*-
"""验证 tool_call 解析容错 + 动作名归一化（DFMEA 首跑 0 行的两处根因）。

不计 LLM。运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_toolcall_fix.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import actions, chat, db  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(("    PASS  " if cond else "    FAIL  ") + label + (" " + extra if extra else ""))
    if not cond:
        fails.append(label)


print("[1] _parse_tool_call 容错（真实畸形样本）")
raw = ('<tool_call>{"name": "查询历史FMEA", "args": {"query": "蓝牙模块 '
       'Bluetooth module DFMEA 历史记录"}</arg_value></tool_call>')
r = chat._parse_tool_call(raw)
check("缺闭括号 + 伪结束标记仍可解析",
      bool(r) and r[0] == "查询历史FMEA"
      and r[1].get("query", "").startswith("蓝牙模块"),
      f"-> {r}")

print("[2] 正常形式与边界")
r2 = chat._parse_tool_call('<tool_call>{"name": "x", "args": {"a": 1}}</tool_call>')
check("标准 tool_call", r2 == ("x", {"a": 1}), f"-> {r2}")
r3 = chat._parse_tool_call('前言<tool_call>{"name":"y","args":{"b":2}}</tool_call>后语')
check("前后带文字", bool(r3) and r3[0] == "y")
r4 = chat._parse_tool_call("没有任何调用")
check("无 tool_call -> None", r4 is None)
r5 = chat._parse_tool_call('<tool_call>{"name":"z","args":{"s":"含}括号"}}</tool_call>')
check("字符串内括号不干扰", bool(r5) and r5[0] == "z", f"-> {r5}")
check("MAX_ACTION_ROUNDS 已提高", chat.MAX_ACTION_ROUNDS >= 6,
      f"= {chat.MAX_ACTION_ROUNDS}")

print("[3] guard_action 动作名归一化")
conn = db.get_conn()
iid = conn.execute("SELECT id FROM identities WHERE name='DFMEA 工程师'"
                   ).fetchone()["id"]
ok, reason, row = actions.guard_action(conn, iid, "查询历史FMEA",
                                       {"part": "蓝牙模块"})
check("漏空格仍匹配到动作", ok and row and row["builtin_name"] == "fmea_history_query",
      f"ok={ok} reason={reason}")
ok2, _, row2 = actions.guard_action(conn, iid, "查询历史 FMEA", {"part": "蓝牙模块"})
check("精确名仍匹配", ok2 and row2["builtin_name"] == "fmea_history_query")
ok3, reason3, _ = actions.guard_action(conn, iid, "写入DFMEA记录",
                                       {"failure_mode": "x", "sources": {}})
check("「写入DFMEA记录」也能匹配", ok3, f"reason={reason3}")
ok4, _, _ = actions.guard_action(conn, iid, "完全不存在", {})
check("不存在的名字仍拒绝", not ok4)

print("[4] _actions_block 展示每个动作的真实参数名")
blk = chat._actions_block([{
    "name": "询问专家数字人", "description": "问专家",
    "input_schema": {"type": "object",
                     "properties": {"expert": {}, "question": {}},
                     "required": ["expert", "question"]}}])
check("列出参数且标注必填", "参数：expert*、question*" in blk,
      blk.strip().splitlines()[-4][:80])

print("[5] guard_action 参数别名兜底（LLM 写 query -> 补成 question）")
args5 = {"expert": "射频硬件专家", "query": "天线失配的典型不良率？"}
ok5, r5, row5 = actions.guard_action(conn, iid, "询问专家数字人", args5)
check("query 被补全为 question", ok5, f"reason={r5}")
check("补全后的 args 含 question 键", "question" in args5, f"args={args5}")

print("[6] 并行多调用（一次输出多个 tool_call —— 原来只取第一个）")
multi = ('已获取第 1 组。继续等待其余返回……'
         '<tool_call>{"name": "查 AP / S-O-D 准则表", "args": {"severity": 7, "occurrence": 3, "detection": 4}}</tool_call>'
         '<tool_call>{"name": "查 AP / S-O-D 准则表", "args": {"severity": 7, "occurrence": 4, "detection": 5}}</tool_call>'
         '<tool_call>{"name": "查 AP / S-O-D 准则表", "args": {"severity": 8, "occurrence": 4, "detection": 4}}</tool_call>')
cs = chat._parse_tool_calls(multi)
check("3 个并行调用全部解析", len(cs) == 3,
      f"-> {[c[1].get('severity') for c in cs]}")

print("[7] 动作名在 JSON 之外（检索本体{\"args\": …}）")
odd = '<tool_call>检索本体{"args": {"query": "复核准则"}}</tool_call>'
cs2 = chat._parse_tool_calls(odd)
check("无 name 字段仍取到动作名",
      len(cs2) == 1 and cs2[0][0] == "检索本体"
      and cs2[0][1].get("query") == "复核准则", f"-> {cs2}")

print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
