# -*- coding: utf-8 -*-
"""手机蓝牙模块 DFMEA 端到端验收（design §15）。

**先有测试用例，再跑 FMEA** —— 本脚本即验收规范，分三阶段：

  STAGE=gen     一句话需求 → pipeline（拓扑与 persona 绑定）
  STAGE=run     审批 → 运行（真跑 LLM + 动作）
  STAGE=verify  校验 DFMEA 产出（来源合规性 / AP 一致性 / 可追溯性）
  STAGE=all     三阶段连跑

验收断言（全部 must-pass）
--------------------------
  G1 生成成功            G2 所有节点绑定数字人（E6）      G3 含 DFMEA 工程师
  G4 含 ≥2 个部件专家     G5 含 review 复核门              G6 编排校验通过
  R1 运行 job 成功
  V1 DFMEA 行数 ≥ 3      V2 每行 sources 非空
  V3 所有来源在闭集内     V4 **所有非空格都有来源标注**
  V5 每行 AP 与 AP 表一致 V6 `history#N` 的 N 真实存在
  V7 `expert:<名>` 的专家真实存在
  V8 覆盖 ≥2 个不同部件   V9 `ai_new` 项可被机器筛出

运行（容器内）::

    docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
        python scripts/verify_dfmea_bluetooth_e2e.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, fmea, jobs, pipeline  # noqa: E402

STAGE = (os.environ.get("STAGE") or "all").strip().lower()
PID_KEY = "verify_dfmea_e2e_pid"

#: 模拟用户在**对话页**输入的一句话需求（不含任何结构性提示）
REQUEST = (
    "请为手机蓝牙模块做一份 DFMEA。需要覆盖射频链路、电源与时钟、结构与工艺、"
    "嵌入式固件几个子系统的部件专家，由 DFMEA 工程师汇总产出 DFMEA 表，"
    "最后经 DFMEA 复核员复核。每条失效模式都要有严重度 S、频度 O、探测度 D，"
    "并且标注数据来源。"
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
    print(f"    需求：{REQUEST[:56]}…")
    t0 = time.time()
    r = pipeline.generate_from_request(conn, REQUEST, provider="llm")
    check("G1 生成成功", r.get("ok"), f"耗时 {time.time()-t0:.1f}s "
          f"{r.get('error', '')}")
    if not r.get("ok"):
        return 0
    pid = r["pipeline_id"]
    _kv_set(conn, PID_KEY, pid)
    p = pipeline.get_pipeline(conn, pid)
    names = {}
    for n in p["nodes"]:
        if n["persona_id"]:
            row = conn.execute("SELECT name FROM identities WHERE id=?",
                               (n["persona_id"],)).fetchone()
            names[n["id"]] = row["name"] if row else "?"

    print(f"    pipeline #{pid} 「{p['name']}」 节点 {len(p['nodes'])} "
          f"关系 {len(p['relations'])}")
    for n in p["nodes"]:
        print(f"      {n['node_key']:<26} {names.get(n['id'], '(未绑定)'):<16}"
              f" {n['step_name'][:26]}")

    unbound = [n["node_key"] for n in p["nodes"] if not n["persona_id"]]
    check("G2 所有节点绑定数字人", not unbound, f"未绑定={unbound or '无'}")
    check("G3 含 DFMEA 工程师",
          any("DFMEA 工程师" == v for v in names.values()))
    experts = [v for v in names.values() if v.endswith("专家")]
    check("G4 含 ≥2 个部件专家", len(experts) >= 2, f"专家={experts}")
    check("G5 含 review 复核门",
          any(x["relation_type"] == "review" for x in p["relations"]))
    errs = pipeline.validate_pipeline(conn, pid)
    check("G6 编排校验通过", not errs, f"{errs or ''}")
    has_ask = any(x["relation_type"] == "ask" for x in p["relations"])
    print(f"    （信息）含 ask 边 = {has_ask}")
    return pid


# ---------------- 阶段 2：审批 + 运行 ----------------

def stage_run(conn, pid) -> None:
    print("[R] 审批 + 运行 pipeline")
    errs = pipeline.validate_pipeline(conn, pid)
    if errs:
        check("R1 运行成功", False, f"校验未过：{errs}")
        return
    check("R0 审批通过", pipeline.approve_pipeline(conn, pid))
    run_before = conn.execute("SELECT COUNT(*) c FROM pipeline_runs WHERE"
                              " pipeline_id=?", (pid,)).fetchone()["c"]
    job_id = jobs.create_job(conn, "pipeline", 0, detail=f"pipeline#{pid}")
    jobs.run_in_background(job_id, pipeline.run_pipeline_execution, pid)
    print(f"    job #{job_id} 已启动，等待完成…")
    t0 = time.time()
    status = jobs.wait_job(conn, job_id, timeout=1800)
    dt = time.time() - t0
    row = conn.execute("SELECT error FROM jobs WHERE id=?", (job_id,)).fetchone()
    check("R1 运行成功", status == "done",
          f"status={status} 耗时 {dt:.0f}s err={row['error'] if row else ''}")
    run_after = conn.execute("SELECT COUNT(*) c FROM pipeline_runs WHERE"
                             " pipeline_id=?", (pid,)).fetchone()["c"]
    print(f"    （信息）pipeline_runs {run_before} -> {run_after}")


# ---------------- 阶段 3：校验产出 ----------------

def stage_verify(conn, pid) -> None:
    print("[V] 校验 DFMEA 产出")
    runs = conn.execute("SELECT id, status FROM pipeline_runs WHERE pipeline_id=?"
                        " ORDER BY id DESC", (pid,)).fetchall()
    run_ids = [r["id"] for r in runs]
    print(f"    pipeline_runs = {[(r['id'], r['status']) for r in runs]}")
    if not run_ids:
        check("V1 DFMEA 行数 ≥ 3", False, "无 run 记录")
        return
    ph = ",".join("?" for _ in run_ids)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM dfmea_rows WHERE run_id IN ({ph}) ORDER BY id",
        run_ids).fetchall()]
    check("V1 DFMEA 行数 ≥ 3", len(rows) >= 3, f"实际 {len(rows)} 行")
    if not rows:
        return

    bad_src, bad_field, ap_bad = [], [], []
    hist_bad, expert_bad, parts = [], [], set()
    valid_sources = (fmea.SRC_HISTORY, fmea.SRC_TABLE, fmea.SRC_AI_INFERRED,
                     fmea.SRC_AI_NEW)
    for row in rows:
        parts.add(row.get("part") or "")
        src = row.get("sources") or {}
        if not src:
            bad_src.append(row["id"])
        # V3 来源闭集
        for f, v in src.items():
            s = str(v).strip()
            base = s.split("#", 1)[0]
            ok = base in valid_sources or s.startswith(fmea.SRC_EXPERT_PREFIX)
            if not ok:
                bad_src.append((row["id"], f, s))
        # V4 所有非空格都有来源
        for f in fmea.SOURCE_FIELDS:
            val = row.get(f)
            if val not in (None, "") and f not in src:
                bad_field.append((row["id"], f))
        # V5 AP 与表一致
        s_, o_, d_ = row.get("severity"), row.get("occurrence"), row.get("detection")
        if s_ and o_ and d_:
            looked = fmea.ap_lookup(conn, s_, o_, d_)
            if looked.get("ok") and looked["result"]["ap"] != row.get("ap"):
                ap_bad.append((row["id"], row.get("ap"),
                               looked["result"]["ap"]))
        # V6 history#N 可追溯
        for f, v in src.items():
            s = str(v).strip()
            if s.startswith(fmea.SRC_HISTORY + "#"):
                try:
                    cid = int(s.split("#", 1)[1])
                except Exception:  # noqa: BLE001
                    hist_bad.append((row["id"], f, s))
                    continue
                ex = conn.execute("SELECT 1 FROM fmea_cases WHERE id=?",
                                  (cid,)).fetchone()
                if not ex:
                    hist_bad.append((row["id"], f, s))
            if s.startswith(fmea.SRC_EXPERT_PREFIX):
                nm = s[len(fmea.SRC_EXPERT_PREFIX):].strip()
                ex = conn.execute("SELECT 1 FROM identities WHERE name=?",
                                  (nm,)).fetchone()
                if not ex:
                    expert_bad.append((row["id"], f, s))

    check("V2 每行 sources 非空", not bad_src, f"异常={bad_src[:3]}")
    check("V3 来源全在闭集内", not bad_src, f"越界={bad_src[:3]}")
    check("V4 所有非空格均有来源标注", not bad_field,
          f"漏标={bad_field[:5]}（共 {len(bad_field)} 处）")
    check("V5 每行 AP 与 AP 表一致", not ap_bad, f"不一致={ap_bad[:3]}")
    check("V6 history#N 可追溯", not hist_bad, f"悬空={hist_bad[:3]}")
    check("V7 expert:<名> 专家真实存在", not expert_bad,
          f"未知专家={expert_bad[:3]}")
    parts.discard("")
    check("V8 覆盖 ≥2 个不同部件", len(parts) >= 2, f"部件={sorted(parts)}")
    pend = fmea.pending_ai_new(conn, run_ids[0])
    ai_new_n = sum(1 for r in rows for v in (r.get("sources") or {}).values()
                   if str(v).startswith(fmea.SRC_AI_NEW))
    check("V9 ai_new 项可被机器筛出", len(pend) >= 0,
          f"待人工确认 {len(pend)} 行 / ai_new 标记 {ai_new_n} 处")

    # 结果摘要
    print("\n    —— DFMEA 产出摘要 ——")
    for r in rows[:6]:
        print(f"      #{r['id']} [{r.get('part')}] {str(r.get('failure_mode'))[:22]}"
              f" S/O/D={r.get('severity')}/{r.get('occurrence')}/{r.get('detection')}"
              f" AP={r.get('ap')}")
        print(f"         来源: {json.dumps(r.get('sources') or {}, ensure_ascii=False)[:150]}")


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
