# -*- coding: utf-8 -*-
"""手机 WiFi 模块 DFMEA 端到端（design §15.7 验收）。

与蓝牙那轮的区别（也是本轮考试的意义）：
  · WiFi 模块在**历史 FMEA 库里没有直接记录** —— 链路必须先「自主搜索部件」
    拿到子系统清单，再对每个子系统去历史库找**同类射频部件**的现有案例做类比
    推导（考的就是这条能力）；
  · 判分不再只看「来源合规」，而是走 30 题考卷（`grade_dfmea_wifi`），
    阈值 **98%**。

三阶段：
  STAGE=gen     一句话需求 → pipeline（拓扑与 persona 绑定）
  STAGE=run     审批 → 运行（真跑 LLM + 动作，耗时较长）
  STAGE=verify  30 题判分
  STAGE=all     连跑

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_dfmea_wifi_e2e.py
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app/backend")

from app import db, jobs, pipeline  # noqa: E402
from grade_dfmea_wifi import evaluate  # noqa: E402  (同目录)

STAGE = (os.environ.get("STAGE") or "all").strip().lower()
PID_KEY = "verify_dfmea_wifi_pid"

#: 模拟用户在**对话页**输入的一句话需求（考官题面；不含任何结构性提示）
#:
#: ⚠️ 题面**不得列举子系统名称**（2026-09-13 修正）。旧题面写了「天线、射频前端、
#: 供电、时钟、固件、屏蔽、互连」7 个词，结果模型照抄这 7 个、只覆盖 7/13 个子
#: 系统，A 类覆盖题白丢 25 分 —— 那是考官泄题，不是能力考核。考官只应说明
#: **约束与要求**（WiFi 无直接历史记录、必须类比、AP 必须查表、逐格标来源），
#: 至于「有哪些子系统、每个子系统去哪找证据」必须由模型自己搜出来。
REQUEST = (
    "请为手机 WiFi 模块做一份 DFMEA。第一步先自主搜索 WiFi 模块的部件清单，"
    "再逐个部件分析可能的失效模式。注意 WiFi 模块在历史 FMEA 库里没有直接记录，"
    "请参照历史库中同类射频部件的现有案例做类比推导。每条失效模式要给出"
    "严重度 S、频度 O、探测度 D，行动优先级 AP 必须查表得出，并逐格标注数据来源。"
    "覆盖范围内的部件都要给出分析，不要遗漏。"
)

fails: list[str] = []


def check(label, cond, extra=""):
    print(("    PASS  " if cond else "    FAIL  ") + label
          + ("  " + extra if extra else ""))
    if not cond:
        fails.append(label)


def _kv_set(conn, k, v):
    conn.execute("INSERT INTO kv(k,v) VALUES(?,?)"
                 " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, json.dumps(v, ensure_ascii=False)))
    conn.commit()


def _kv_get(conn, k):
    r = conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    if not r:
        return None
    v = r["v"]
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:  # noqa: BLE001
            return v
    return v


# ---------------- 阶段 1：生成 ----------------

def stage_gen(conn) -> int:
    print("[G] 一句话需求 → pipeline（不干预生成）")
    print(f"    需求：{REQUEST[:64]}…")
    t0 = time.time()
    r = pipeline.generate_from_request(conn, REQUEST, provider="llm")
    check("G1 生成成功", r.get("ok"),
          f"耗时 {time.time()-t0:.1f}s {r.get('error', '')}")
    if not r.get("ok"):
        return 0
    pid = r["pipeline_id"]
    _kv_set(conn, PID_KEY, pid)
    p = pipeline.get_pipeline(conn, pid)
    names, pids = {}, {}
    for n in p["nodes"]:
        if n["persona_id"]:
            row = conn.execute("SELECT name FROM identities WHERE id=?",
                               (n["persona_id"],)).fetchone()
            names[n["id"]] = row["name"] if row else "?"
            pids[n["id"]] = n["persona_id"]

    print(f"    pipeline #{pid} 「{p['name']}」 节点 {len(p['nodes'])} "
          f"关系 {len(p['relations'])}")
    for n in p["nodes"]:
        print(f"      {n['node_key']:<26} {names.get(n['id'], '(未绑定)'):<16}"
              f" {n['step_name'][:26]}")

    unbound = [n["node_key"] for n in p["nodes"] if not n["persona_id"]]
    check("G2 所有节点绑定数字人", not unbound, f"未绑定={unbound or '无'}")
    check("G3 含 DFMEA 工程师", any(v == "DFMEA 工程师" for v in names.values()))
    experts = [v for v in names.values() if v.endswith("专家")]
    check("G4 含 ≥2 个部件专家", len(experts) >= 2, f"专家={experts}")
    check("G5 含 review 复核门",
          any(x["relation_type"] == "review" for x in p["relations"]))
    check("G6 编排校验通过", not pipeline.validate_pipeline(conn, pid))
    has_ask = any(x["relation_type"] == "ask" for x in p["relations"])
    print(f"    （信息）含 ask 边 = {has_ask}")
    # G7：DFMEA 工程师必须拿到「搜索部件清单」动作，否则无法自主搜索部件
    eng = [i for i, nm in names.items() if nm == "DFMEA 工程师"]
    got = False
    if eng:
        row = conn.execute(
            "SELECT COUNT(*) c FROM persona_actions WHERE identity_id=?"
            " AND builtin_name='fmea_part_search' AND status='approved'",
            (pids[eng[0]],)).fetchone()
        got = (row["c"] or 0) > 0
    check("G7 DFMEA 工程师已绑定「搜索部件清单」", got)
    return pid


# ---------------- 阶段 2：审批 + 运行 ----------------

def preflight(conn) -> bool:
    """考卷前置条件自检：**知识库必须先就位**，否则整场考试无效。

    为什么需要它（2026-09-13 run#29 实测）：`fmea_parts`（部件知识库）当时为空，
    DFMEA 工程师调「搜索部件清单」拿到 `parts: []`，误判为「产品名不对」并反复
    重试同一个动作，直到 8 轮工具轮数耗尽 —— 最终交付物是一个 215 字的裸
    `<tool_calls>` 块，没有表、没有数值，30 题只拿 30/150（20.0%）。
    更糟的是**故障是静默的**：报告里只看到「复核门 FAIL / 行数=0」，读起来像
    模型能力不足，真实原因（夹具没就位）被完全掩盖，白白浪费一整轮迭代。

    所以：跑之前先把夹具点数验明，缺什么就直说，宁可不跑也不要出一个
    「看起来像能力问题」的假低分。
    """
    print("[F] 前置条件自检（夹具）")
    # ① 部件知识库：DFMEA 的分析起点
    n_parts = conn.execute(
        "SELECT COUNT(*) c FROM fmea_parts").fetchone()["c"]
    check("F1 部件知识库已就位（fmea_parts 非空）", n_parts > 0,
          f"fmea_parts={n_parts}" + ("" if n_parts else
          "  ← 请先运行 `python scripts/seed_fmea_parts.py`"))
    # ② 历史 FMEA 库：类比推导的取值来源
    n_cases = conn.execute(
        "SELECT COUNT(*) c FROM fmea_cases").fetchone()["c"]
    check("F2 历史 FMEA 库已就位（fmea_cases 非空）", n_cases > 0,
          f"fmea_cases={n_cases}" + ("" if n_cases else
          "  ← 请先运行 `python seed_fmea_data.py`"))
    # ③ AP 矩阵：AP 以表为准，缺表则 AP 题全废
    n_ap = conn.execute(
        "SELECT COUNT(*) c FROM fmea_ap_matrix").fetchone()["c"]
    check("F3 AP 矩阵已就位（1000 格）", n_ap >= 1000,
          f"fmea_ap_matrix={n_ap}")
    # ④ S/O/D 准则表
    n_crit = conn.execute(
        "SELECT COUNT(*) c FROM fmea_sod_criteria").fetchone()["c"]
    check("F4 S/O/D 准则表已就位", n_crit > 0, f"fmea_sod_criteria={n_crit}")
    return not fails


def stage_run(conn, pid) -> None:
    print("[R] 审批 + 运行 pipeline")
    if not preflight(conn):
        check("R1 运行成功", False,
              "前置夹具缺失 —— 本场考试结果无效，不运行（避免产出误导性低分）")
        return
    errs = pipeline.validate_pipeline(conn, pid)
    if errs:
        check("R1 运行成功", False, f"校验未过：{errs}")
        return
    check("R0 审批通过", pipeline.approve_pipeline(conn, pid))
    job_id = jobs.create_job(conn, "pipeline", 0, detail=f"pipeline#{pid}")
    jobs.run_in_background(job_id, pipeline.run_pipeline_execution, pid)
    print(f"    job #{job_id} 已启动，等待完成…")
    t0 = time.time()
    status = jobs.wait_job(conn, job_id, timeout=3600)
    dt = time.time() - t0
    row = conn.execute("SELECT error FROM jobs WHERE id=?", (job_id,)).fetchone()
    check("R1 运行成功", status == "done",
          f"status={status} 耗时 {dt:.0f}s err={row['error'] if row else ''}")


# ---------------- 阶段 3：判分 ----------------

def stage_verify(conn, pid) -> None:
    print("[V] 30 题判分")
    runs = conn.execute("SELECT id, status FROM pipeline_runs WHERE pipeline_id=?"
                        " ORDER BY id DESC", (pid,)).fetchall()
    print(f"    pipeline_runs = {[(r['id'], r['status']) for r in runs]}")
    if not runs:
        check("V 有运行记录", False, "无 run")
        return
    rid = runs[0]["id"]
    res = evaluate(conn, rid)
    check(f"V 正确率 ≥ 98%（{res['passed']}/{res['total']} = "
          f"{res['rate']*100:.1f}%）", res["rate"] >= 0.98)


# ---------------- 主流程 ----------------

def main() -> int:
    conn = db.get_conn()
    pid = 0
    if STAGE in ("gen", "all"):
        pid = stage_gen(conn)
    else:
        pid = _kv_get(conn, PID_KEY) or 0
        if not pid:
            print("未找到已生成的 pipeline（先跑 STAGE=gen）")
            return 2
    if pid and STAGE in ("run", "all"):
        stage_run(conn, pid)
    if pid and STAGE in ("verify", "all"):
        stage_verify(conn, pid)
    print("\nRESULT:", "OK" if not fails else f"FAILED({len(fails)}): {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
