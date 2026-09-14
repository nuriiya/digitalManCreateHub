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

print("[14] run 内幂等：同 (part, failure_mode) 重复写入不新增行")
# 为什么必须测：pipeline 里「汇总 / 查表 / 写行」常由同一个数字人分几个节点承担，
# 每个节点都有独立 tool-use 循环 —— 实测 WiFi 那轮同一批 29 条被写了两遍（58 行）。
fmea.set_run_id(SENTINEL_RUN)
dup_row = {"part": "幂等测试部件", "failure_mode": "幂等测试失效模式",
           "severity": 5, "occurrence": 3, "detection": 4,
           "sources": {"failure_mode": "ai_inferred"}}
d1 = actions.execute_builtin(conn, 0, "fmea_write_row", dup_row)
d2 = actions.execute_builtin(conn, 0, "fmea_write_row", dup_row)
check("首次写入 ok 且未标记 updated",
      d1.get("ok") and not d1["result"].get("updated"))
check("二次写入复用同一行 id",
      d2.get("ok") and d2["result"]["row_id"] == d1["result"]["row_id"],
      f"{d1.get('result', {}).get('row_id')} vs {d2.get('result', {}).get('row_id')}")
check("二次写入标记 updated", d2.get("ok") and d2["result"].get("updated") is True)
n_dup = conn.execute("SELECT COUNT(*) c FROM dfmea_rows WHERE run_id=?"
                     " AND part=?", (SENTINEL_RUN, "幂等测试部件")).fetchone()["c"]
check("行数仍为 1（未重复落库）", n_dup == 1, f"{n_dup}")

print("[15] AP 来源以表为准（值来自表 → 来源格不得标 AI 推断）")
sod_ap = fmea.ap_lookup(conn, 8, 3, 3)
ap_row = {"part": "AP来源测试部件", "failure_mode": "AP来源测试失效模式",
          "severity": 8, "occurrence": 3, "detection": 3,
          "sources": {"ap": "ai_inferred", "failure_mode": "ai_inferred"}}
a1 = actions.execute_builtin(conn, 0, "fmea_write_row", ap_row)
check("写入 ok", a1.get("ok"), str(a1.get("error") or ""))
check(f"AP 取表值 {sod_ap['result']['ap']}",
      a1.get("ok") and a1["result"]["ap"] == sod_ap["result"]["ap"],
      str(a1.get("result", {}).get("ap")))
check("标记 ap_src_fixed", a1.get("ok") and a1["result"].get("ap_src_fixed") is True)
saved = conn.execute("SELECT sources FROM dfmea_rows WHERE id=?",
                     (a1["result"]["row_id"],)).fetchone()["sources"]
if isinstance(saved, str):
    import json as _json
    saved = _json.loads(saved)
check("sources.ap 已校正为 table#ap",
      str((saved or {}).get("ap")) == "table#ap", str((saved or {}).get("ap")))
check("其余格来源未被改动",
      str((saved or {}).get("failure_mode")) == "ai_inferred",
      str((saved or {}).get("failure_mode")))
print("[16] 非法 sources 必须优雅报错，不得抛异常")
# 为什么必须测：LLM 可能把 sources 传成 list / 字符串（实测 2026-09-12 因此
# 让整条 pipeline 在 7 分钟时崩掉）。原实现靠 validate_sources 兜住；若在它
# 之前就先 `dict(...)`，异常会直接冒泡。
for bad in (["history#1", "expert:x"], "history", 123,
            {"failure_mode": "not_a_kind"}):
    label = f"sources={type(bad).__name__}"
    try:
        rb = actions.execute_builtin(
            conn, 0, "fmea_write_row",
            {"part": "非法来源测试", "failure_mode": "非法来源测试失效",
             "sources": bad})
        check(f"{label} 返回错误而非抛异常", rb.get("ok") is False,
              str(rb.get("error"))[:58])
    except Exception as e:  # noqa: BLE001
        check(f"{label} 返回错误而非抛异常", False, f"抛了 {type(e).__name__}")
fmea.set_run_id(None)

print("[17] 历史库按**部件族**检索（同类案例类比的主路径）")
# 为什么必须测：第 4 轮实测"按中文部件名查必然 0 命中"，历史引用整体归零。
for fam, lo in (("ANT", 2), ("RF", 2), ("PMU", 1)):
    rf = fmea.history_query(conn, family=fam)
    items = rf.get("result", {}).get("items", []) if rf.get("ok") else []
    in_fam = all(str(i.get("part_no", "")).split("-")[1] == fam
                 for i in items if "-" in str(i.get("part_no", "")))
    check(f"family={fam} 命中 ≥{lo} 条", len(items) >= lo, f"{len(items)} 条")
    check(f"family={fam} 结果全部属于该族", in_fam)
check("未知族返回 0 条",
      fmea.history_query(conn, family="ZZZ").get("result", {}).get("hits") == 0)
ps = actions.execute_builtin(conn, 0, "fmea_part_search", {"product": "WiFi 模块"})
fams = [p.get("analogy_family")
        for p in (ps.get("result", {}).get("parts") or [])]
check("部件清单每个部件都带 analogy_family",
      bool(fams) and all(fams) and len(fams) >= 12, str(fams[:5]))
check("analogy_family 可直接用于 family 查询（拿去就用）",
      fmea.history_query(conn, family=fams[0]).get("result", {}).get("hits", 0) > 0,
      f"family={fams[0]}")
check("family 可与 keyword 组合",
      fmea.history_query(conn, family="ANT", keyword="阻抗").get("ok") is True)
check("三参数全空时给出可读错误",
      fmea.history_query(conn).get("ok") is False)

print("[18] AP 矩阵原文可取回（复核门独立复现查表的前提）")
# 为什么必须测：2026-09-13 实测 —— 复核门要核 "AP 是否与表一致"，但接口只有
# 「按 (S,O,D) 查一个值」的形态，**没有取回表原文的能力**，复核员在物理上无法
# 自行复现查表，只能报「AP 表取回失败→无法核验」并给 FAIL，而实际值全对。
# 这是「接口没给够」，不是判别能力问题 —— 判别者要的是**证据本身**。
slice_r = actions.execute_builtin(conn, 0, "fmea_ap_table",
                                  {"matrix_severity": 7})
check("matrix_severity=7 取回切片 ok", slice_r.get("ok"), str(slice_r)[:80])
srows = (slice_r.get("result") or {}).get("rows") or []
check("切片含该 S 档全部 O×D = 100 格", len(srows) == 100, f"{len(srows)}")
bykey = {(r["severity"], r["occurrence"], r["detection"]): r["ap"] for r in srows}
# 用切片独立复现 ap_lookup 的结果（这就是复核门的动作）
for (s, o, d, want) in ((7, 5, 5, "H"), (7, 5, 4, "M")):
    direct = fmea.ap_lookup(conn, s, o, d)["result"]["ap"]
    check(f"切片与 ap_lookup 一致 ({s},{o},{d})", bykey.get((s, o, d)) == direct,
          f"切片={bykey.get((s,o,d))} 直查={direct}")
check("切片行都带准确的 S/O/D 三元组（可按三元组比对）",
      all(isinstance(r.get("severity"), int) and isinstance(r.get("ap"), str)
          for r in srows[:5]))

# ---- 清理 ----
n = conn.execute("DELETE FROM dfmea_rows WHERE run_id=?", (SENTINEL_RUN,))
conn.commit()
print(f"\n清理：删除测试行 {n.rowcount} 条")
print("RESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
sys.exit(0 if not fails else 1)
