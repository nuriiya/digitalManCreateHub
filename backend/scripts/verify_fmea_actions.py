# -*- coding: utf-8 -*-
"""验证 DFMEA 动作零件（design §15.3 的 C1~C4）。

覆盖：查历史 / 查 AP 表 / 查准则 / 写行（**来源闭集**与 **AP 以表为准**两条裁决线）
/ `ai_new` 待确认清单可筛出。测试写入用哨兵 run_id，跑完清理，不污染真实数据。

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_fmea_actions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import actions, db, fmea  # noqa: E402

SENTINEL_RUN = -999999
fails = []


def check(label, cond, extra=""):
    print(("    PASS  " if cond else "    FAIL  ") + label + (" " + extra if extra else ""))
    if not cond:
        fails.append(label)


conn = db.get_conn()

# ---- C1 查历史 ----
print("[1] fmea_history_query(part=蓝牙模块)")
r = actions.execute_builtin(conn, 0, "fmea_history_query", {"part": "蓝牙模块"})
check("ok 且命中", r.get("ok") and r["result"]["hits"] > 0)
it = r["result"]["items"][0]
print(f"        ref={it['ref']} mode={it['failure_mode']} "
      f"S/O/D={it['severity']}/{it['occurrence']}/{it['detection']} AP={it['ap']} "
      f"出处={it['source_doc']}")
check("每条带可引用 ref", str(it.get("ref", "")).startswith("history#"))
check("带出处可溯源", bool(it.get("source_doc")))

print("[2] fmea_history_query(keyword=晶振)")
r2 = actions.execute_builtin(conn, 0, "fmea_history_query", {"keyword": "晶振"})
check("关键词命中", r2.get("ok") and r2["result"]["hits"] >= 1,
      f"hits={r2['result']['hits']}")

print("[3] fmea_history_query 空参数")
r3 = actions.execute_builtin(conn, 0, "fmea_history_query", {})
check("空参数被拒", not r3.get("ok"))

# ---- C2 查表 ----
print("[4] fmea_ap_table(S=9,O=3,D=3)")
r4 = actions.execute_builtin(conn, 0, "fmea_ap_table",
                             {"severity": 9, "occurrence": 3, "detection": 3})
check("查到 AP", r4.get("ok") and r4["result"]["ap"] in ("H", "M", "L"),
      f"AP={r4.get('result', {}).get('ap')}")

print("[5] fmea_ap_table 越界 S/O/D")
r5 = actions.execute_builtin(conn, 0, "fmea_ap_table",
                             {"severity": 11, "occurrence": 3, "detection": 3})
check("越界被拒", not r5.get("ok"))

print("[6] fmea_ap_table(dimension=occurrence, score=5)")
r6 = actions.execute_builtin(conn, 0, "fmea_ap_table",
                             {"dimension": "occurrence", "score": 5})
check("查到准则", r6.get("ok") and "criterion" in r6.get("result", {}),
      r6.get("result", {}).get("criterion", ""))

# ---- C4 写行：来源闭集 ----
print("[7] fmea_write_row —— 合法来源")
fmea.set_run_id(SENTINEL_RUN)
row = {"part": "蓝牙模块", "function": "2.4GHz 收发",
       "failure_mode": "天线阻抗失配", "failure_effect": "通信距离不足",
       "severity": 6, "failure_cause": "匹配元件公差漂移", "occurrence": 4,
       "prevention_control": "1% 精度元件", "detection_control": "S11 测试",
       "detection": 3, "ap": "M", "action": "增加来料抽检",
       "sources": {"failure_mode": "history#1", "severity": "history#1",
                   "failure_cause": "expert:射频专家", "action": "ai_inferred",
                   "ap": "table#ap"}}
rw = actions.execute_builtin(conn, 0, "fmea_write_row", row)
check("写入成功", rw.get("ok"), f"row_id={rw.get('result', {}).get('row_id')}")
check("四种来源形式（含带引用号）均被接受", rw.get("ok"))

print("[8] fmea_write_row —— 非法来源必须被拒")
bad = dict(row)
bad["sources"] = {"failure_mode": "也许吧"}
rb = actions.execute_builtin(conn, 0, "fmea_write_row", bad)
check("非法来源被拒", not rb.get("ok"), rb.get("error", "")[:60])

print("[9] AP 以表为准（LLM 给错的 AP 被修正）")
row2 = dict(row)
row2["failure_mode"] = "AP 修正测试"
row2["severity"], row2["occurrence"], row2["detection"] = 9, 8, 8
row2["ap"] = "L"                      # 故意给错
row2["sources"] = {"failure_mode": "ai_inferred", "ap": "ai_inferred"}
rw2 = actions.execute_builtin(conn, 0, "fmea_write_row", row2)
check("写入成功", rw2.get("ok"))
check("AP 被表修正", rw2["result"]["ap"] != "L",
      f"表给 {rw2['result']['ap']}，修正标记={rw2['result']['ap_corrected']}")

print("[10] ai_new 可被机器筛出")
row3 = dict(row)
row3["failure_mode"] = "全新功能失效模式"
row3["sources"] = {"failure_mode": "ai_new", "failure_cause": "ai_new",
                   "severity": "table"}
rw3 = actions.execute_builtin(conn, 0, "fmea_write_row", row3)
check("写入成功", rw3.get("ok"))
pend = fmea.pending_ai_new(conn, SENTINEL_RUN)
ids = {p["row_id"] for p in pend}
check("待确认清单捞出该行", rw3["result"]["row_id"] in ids,
      f"清单={len(pend)} 条")
check("逐格标注 ai_new 字段", any(len(p["fields"]) == 2 for p in pend))

# ---- 批量粒度（补零件：避免逐条往返耗尽 tool-use 轮数）----
print("[11] 批量：一次取回 S/O/D 三张准则表")
r11 = actions.execute_builtin(conn, 0, "fmea_ap_table", {})
check("无参返回三张准则", r11.get("ok")
      and set(r11["result"]["criteria"].keys()) == {"severity", "occurrence", "detection"}
      and len(r11["result"]["criteria"]["severity"]) == 10)

print("[12] 批量：一次查多条 AP")
r12 = actions.execute_builtin(conn, 0, "fmea_ap_table",
                              {"items": [{"severity": 9, "occurrence": 3, "detection": 3},
                                         {"severity": 6, "occurrence": 5, "detection": 4}]})
check("items 批量返回 2 条", r12.get("ok") and r12["result"]["hits"] == 2,
      f"-> {[x['ap'] for x in r12.get('result', {}).get('items', [])]}")

print("[13] 批量：一次写多行（来源校验不放松）")
batch = [
    {**row, "failure_mode": "批量行A", "sources": {"failure_mode": "history#1"}},
    {**row, "failure_mode": "批量行B", "sources": {"failure_mode": "table#ap"}},
    {**row, "failure_mode": "批量行C", "sources": {"failure_mode": "非法来源XYZ"}},
]
r13 = actions.execute_builtin(conn, 0, "fmea_write_row", {"rows": batch})
check("写入 2 行、拒绝 1 行",
      r13.get("ok") and r13["result"]["written"] == 2
      and r13["result"]["failed"] == 1,
      f"written={r13.get('result', {}).get('written')} failed={r13.get('result', {}).get('failed')}")

# ---- 清理 ----
n = conn.execute("DELETE FROM dfmea_rows WHERE run_id=?", (SENTINEL_RUN,))
conn.commit()
print(f"\n清理：删除测试行 {n.rowcount} 条")
print("RESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
