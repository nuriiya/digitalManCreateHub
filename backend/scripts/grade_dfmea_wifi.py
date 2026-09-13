# -*- coding: utf-8 -*-
"""WiFi 模块 DFMEA **考卷 + 判分器**（design §15.7 验收，考官视角）。

口径
----
**30 道题，每题 5 个可机器判定的判定点，合计 150 点。**
正确率 = 通过判定点 / 150，阈值 **98%**（即最多允许 3 点错）。

题面分三类：

  A 覆盖题（13）—— WiFi 模块的 13 个子系统是否都被分析到（每系统 5 点）；
  B 规则题（8） —— 来源闭集 / 证据可追溯 / AP 与表一致 / 无越级代填 /
                   值域与准则 / 结构完整（每题 5 点，对应 5 个自然子判据）；
  C 类比题（9） —— 历史库有同类案例的子系统，是否引用了**对应部件族**的历史证据
                   （这是「根据现有案例推导」的直接可查证据，每系统 5 点）。

判分只读数据库，不改任何东西。

跑法
----
  docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
      python scripts/grade_dfmea_wifi.py            # 自动取最新有行的 run
  ... -e RUN_ID=12 ...                                # 指定 run
"""
import os
import re
import sys

sys.path.insert(0, "/app/backend")

from app import db  # noqa: E402

THRESHOLD = 0.98
#: 来源闭集基名。注意 `expert:<名>` 的基名是 `expert` —— 必须收进闭集，
#: 否则会把所有专家来源误判为非法（首次试跑就踩了：误报 21 处）。
CLOSED = {"history", "table", "expert", "ai_inferred", "ai_new"}
KEY_FIELDS = ("failure_mode", "severity", "occurrence", "detection", "ap")

# ---- A/C 类题面：13 个 WiFi 子系统 ----
# 每项：(子系统, 判定关键词, 期望类比的历史部件族)
SUBSYS: list[tuple[str, list[str], str]] = [
    ("射频天线",        ["天线"],                       "ANT"),
    ("天线馈电与接地",  ["馈电", "接地"],                "ANT"),
    ("射频功率放大器",  ["功率放大", "PA", "发射功率"],   "RF"),
    ("射频接收前端",    ["接收前端", "低噪", "LNA", "灵敏度"], "RF"),
    ("收发切换与滤波",  ["切换", "滤波", "双工", "谐波"], "RF"),
    ("射频供电",        ["供电", "LDO", "纹波"],         "PMU"),
    ("电源保护",        ["保护", "过流", "过温", "浪涌"], "PMU"),
    ("参考时钟",        ["时钟", "晶振"],                "CLK"),
    ("基带与固件",      ["基带", "状态机"],              "SW"),
    ("连接与漫游管理",  ["漫游", "认证", "扫描", "SSID", "连接管理", "重连"], "SW"),
    ("固件升级",        ["升级", "OTA", "回滚"],         "SW"),
    ("射频屏蔽与 EMC",  ["屏蔽", "EMC", "抗扰", "辐射"],  "EMC"),
    ("模块互连",        ["互连", "连接器", "焊", "馈线"], "CON"),
]
#: C 类题从 SUBSYS 里取前 9 个（历史库有对应族的都取，按表序取 9）
C_SUBSYS = SUBSYS[:9]


# ---------------- 基础设施 ----------------

class Score:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.detail: list[tuple[int, str, str, list[tuple[str, bool, str]]]] = []
        self.q_all_ok = 0

    def add(self, qid: int, kind: str, title: str,
            checks: list[tuple[str, bool, str]]):
        for label, ok, *rest in checks:
            self.total += 1
            if ok:
                self.passed += 1
        allok = all(c[1] for c in checks)
        if allok:
            self.q_all_ok += 1
        self.detail.append((qid, kind, title, [(c[0], c[1],
                                                c[2] if len(c) > 2 else "")
                                               for c in checks]))
        mark = "PASS" if allok else "FAIL"
        print(f"[题 {qid:>2}] {kind} · {title:<14} {mark}")
        for label, ok, *rest in checks:
            if not ok:
                extra = rest[0] if rest and rest[0] else ""
                print(f"         ✗ {label}" + (f"   {extra}" if extra else ""))

    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def _norm(s) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(s or "")).lower()


def _bigrams(s) -> set:
    t = _norm(s)
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def jaccard(a, b) -> float:
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def src_of(row: dict, field: str) -> str:
    s = row.get("sources")
    if isinstance(s, str):
        try:
            import json
            s = json.loads(s)
        except Exception:  # noqa: BLE001
            s = {}
    if not isinstance(s, dict):
        return ""
    return str(s.get(field) or "").strip()


def src_base(v: str) -> str:
    """取来源的基名（去引用号）。"""
    v = (v or "").strip()
    if v.startswith("expert:"):
        return "expert"
    return v.split("#", 1)[0]


def blob_of(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in
                    ("part", "function", "failure_mode", "failure_cause"))


def rows_for(rows: list[dict], kws: list[str]) -> list[dict]:
    out = []
    for r in rows:
        b = blob_of(r).lower()
        if any(k.lower() in b for k in kws):
            out.append(r)
    return out


# ---------------- 主流程 ----------------

def evaluate(conn, rid=None) -> dict:
    """判分主流程（可被端到端脚本 import 复用）。返回评分字典。"""
    if rid is None:
        rid = os.environ.get("RUN_ID")
    if rid:
        rid = int(rid)
    else:
        r = conn.execute(
            "SELECT run_id FROM dfmea_rows WHERE run_id IS NOT NULL"
            " GROUP BY run_id ORDER BY run_id DESC LIMIT 1").fetchone()
        rid = r["run_id"] if r else None

    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM dfmea_rows WHERE run_id=? ORDER BY id", (rid,)).fetchall()]
    cases = [dict(r) for r in conn.execute(
        "SELECT id, part, part_no, failure_mode FROM fmea_cases").fetchall()]
    case_ids = {c["id"] for c in cases}
    case_by_id = {c["id"]: c for c in cases}
    ap_map = {(r["severity"], r["occurrence"], r["detection"]): r["ap"]
              for r in conn.execute("SELECT severity, occurrence, detection, ap"
                                    " FROM fmea_ap_matrix").fetchall()}
    ident_names = {r["name"] for r in conn.execute(
        "SELECT name FROM identities").fetchall()}
    crit_dim = {r["dimension"] for r in conn.execute(
        "SELECT DISTINCT dimension FROM fmea_sod_criteria").fetchall()}

    print("=" * 68)
    print(f"WiFi 模块 DFMEA 考试 · run_id={rid} · 行数={len(rows)}")
    print(f"历史库 {len(cases)} 条 · AP 矩阵 {len(ap_map)} 格 · "
          f"准则维度 {sorted(crit_dim)}")
    print("=" * 68)

    sc = Score()

    # ---------------- A 类：13 个覆盖题 ----------------
    for i, (sub, kws, fam) in enumerate(SUBSYS, start=1):
        rs = rows_for(rows, kws)
        has_ap = [r for r in rs if str(r.get("ap") or "").strip()]
        has_src = [r for r in rs
                   if all(src_base(src_of(r, f)) in CLOSED
                          for f in KEY_FIELDS if src_of(r, f))]
        has_sod = [r for r in rs if all(
            isinstance(r.get(f), int) and 1 <= r.get(f) <= 10
            for f in ("severity", "occurrence", "detection"))]
        sc.add(i, "覆盖", sub, [
            ("该系统有分析行", len(rs) >= 1, f"命中 {len(rs)} 行"),
            ("至少有 1 条失效模式描述",
             any(str(r.get("failure_mode") or "").strip() for r in rs)),
            ("至少有 1 条 S/O/D 全在 1..10", len(has_sod) >= 1),
            ("至少有 1 条 AP 已填", len(has_ap) >= 1),
            ("该系统的来源标注全部合法", len(has_src) == len(rs) if rs else False,
             f"{len(has_src)}/{len(rs)}"),
        ])

    # ---------------- B 类：8 个规则题 ----------------
    def field_missing(field):
        return [r for r in rows if not src_of(r, field)]

    n = len(rows)

    sc.add(14, "规则", "关键格来源无遗漏", [
        (f"{f} 格来源齐全", not field_missing(f),
         f"缺 {len(field_missing(f))}/{n}")
        for f in KEY_FIELDS])

    def illegal(field):
        return [r for r in rows
                if src_of(r, field) and src_base(src_of(r, field)) not in CLOSED]

    sc.add(15, "规则", "来源闭集", [
        (f"{f} 格来源合法", not illegal(f), f"非法 {len(illegal(f))}")
        for f in KEY_FIELDS])

    hist_refs = []
    for r in rows:
        for f in KEY_FIELDS:
            v = src_of(r, f)
            if v.startswith("history#"):
                hist_refs.append((r, f, v))
    bad_fmt = [(r, f, v) for r, f, v in hist_refs
               if not v.split("#", 1)[1].isdigit()]
    bad_id = [(r, f, v) for r, f, v in hist_refs
              if v.split("#", 1)[1].isdigit()
              and int(v.split("#", 1)[1]) not in case_ids]
    hist_rows = {id(r) for r, _, _ in hist_refs}
    hist_fams = {str(case_by_id[int(v.split("#", 1)[1])]["part_no"]).split("-")[1]
                 for _, _, v in hist_refs
                 if v.split("#", 1)[1].isdigit()
                 and int(v.split("#", 1)[1]) in case_by_id
                 and "-" in str(case_by_id[int(v.split("#", 1)[1])]["part_no"])}
    sc.add(16, "规则", "history 引用可追溯", [
        ("history 引用格式合法（history#数字）", not bad_fmt, f"{len(bad_fmt)} 处异常"),
        ("引用编号都在历史库中", not bad_id, f"{len(bad_id)} 处悬空"),
        ("至少 5 个不同部件族被引用", len(hist_fams) >= 5, f"{sorted(hist_fams)}"),
        ("引用历史的行数 ≥ 5", len(hist_rows) >= 5, f"{len(hist_rows)} 行"),
        ("引用历史的行占比 ≥ 25%",
         n and len(hist_rows) / n >= 0.25,
         f"{len(hist_rows)}/{n}"),
    ])

    exp_refs = []
    for r in rows:
        for f in KEY_FIELDS:
            v = src_of(r, f)
            if v.startswith("expert:"):
                exp_refs.append((r, f, v[len("expert:"):].strip()))
    bad_exp = [x for x in exp_refs if not x[2]]
    unknown_exp = [x for x in exp_refs if x[2] and x[2] not in ident_names]

    self_ref = None
    for r in rows:
        for f in KEY_FIELDS:
            v = src_of(r, f)
            if v.startswith("expert:") and "DFMEA 工程师" in v:
                self_ref = v
    sc.add(17, "规则", "expert 引用可追溯", [
        ("expert: 前缀都带专家名", not bad_exp, f"{len(bad_exp)} 处空名"),
        ("专家名都是真实数字人", not unknown_exp,
         f"未知：{[x[2] for x in unknown_exp]}"),
        ("至少 1 处引用专家", len(exp_refs) >= 1, f"{len(exp_refs)} 处"),
        ("未把 DFMEA 工程师自己当专家", self_ref is None, str(self_ref or "")),
        ("引用专家涉及 ≥ 1 个专家", len({x[2] for x in exp_refs}) >= 1,
         f"{sorted({x[2] for x in exp_refs})}"),
    ])

    sod_ok = [r for r in rows if all(
        isinstance(r.get(f), int) for f in ("severity", "occurrence", "detection"))]
    ap_ok = [r for r in rows if str(r.get("ap") or "").strip()]
    ap_match = [r for r in ap_ok
                if ap_map.get((r["severity"], r["occurrence"], r["detection"]))
                == str(r["ap"]).strip()]
    ap_bad = [r for r in ap_ok if r not in ap_match]
    sc.add(18, "规则", "AP 与表一致", [
        ("所有行 S/O/D 齐全", len(sod_ok) == n, f"{len(sod_ok)}/{n}"),
        ("所有行 AP 非空", len(ap_ok) == n, f"{len(ap_ok)}/{n}"),
        ("AP 与矩阵查表一致", len(ap_match) == len(ap_ok) and len(ap_ok) > 0,
         f"{len(ap_match)}/{len(ap_ok)}"),
        ("无 (S,O,D) 越界", all(1 <= r[f] <= 10 for r in sod_ok
                              for f in ("severity", "occurrence", "detection"))),
        ("矩阵 1000 格完好", len(ap_map) == 1000, f"{len(ap_map)} 格"),
    ])

    # 越级代填：失效模式与历史库某条高度相似（Jaccard ≥ 0.45），却把该格标 ai_new
    escalated = []
    for r in rows:
        fm = str(r.get("failure_mode") or "")
        if not fm:
            continue
        best = max((jaccard(fm, c["failure_mode"]) for c in cases), default=0.0)
        if best >= 0.45 and src_base(src_of(r, "failure_mode")) == "ai_new":
            escalated.append((r["id"], fm[:24], round(best, 2)))
    ai_new_rows = [r for r in rows if any(
        src_base(src_of(r, f)) == "ai_new" for f in KEY_FIELDS)]
    ai_inf_rows = [r for r in rows if any(
        src_base(src_of(r, f)) == "ai_inferred" for f in KEY_FIELDS)]
    ev_rows = [r for r in rows if any(
        src_base(src_of(r, f)) in ("history", "expert") for f in KEY_FIELDS)]
    sc.add(19, "规则", "不得越级代填", [
        ("相似失效模式未误标 ai_new", not escalated, str(escalated[:3])),
        ("ai_new 行占比 ≤ 20%", n and len(ai_new_rows) / n <= 0.20,
         f"{len(ai_new_rows)}/{n}"),
        ("ai_inferred 行占比 ≤ 50%", n and len(ai_inf_rows) / n <= 0.50,
         f"{len(ai_inf_rows)}/{n}"),
        ("有 history/expert 证据的行 ≥ 30%", n and len(ev_rows) / n >= 0.30,
         f"{len(ev_rows)}/{n}"),
        ("没有任何行只用 ai_new 兜底整行",
         not [r for r in rows if all(src_base(src_of(r, f)) == "ai_new"
                                     for f in KEY_FIELDS if src_of(r, f))]),
    ])

    sc.add(20, "规则", "S/O/D 值域与准则", [
        ("severity 全在 1..10", all(1 <= r["severity"] <= 10 for r in sod_ok)
         and len(sod_ok) == n),
        ("occurrence 全在 1..10", all(1 <= r["occurrence"] <= 10 for r in sod_ok)
         and len(sod_ok) == n),
        ("detection 全在 1..10", all(1 <= r["detection"] <= 10 for r in sod_ok)
         and len(sod_ok) == n),
        ("准则表含 severity 维度", "severity" in crit_dim),
        ("准则表含 occurrence+detection 维度",
         {"occurrence", "detection"} <= crit_dim),
    ])

    sc.add(21, "规则", "结构完整性", [
        ("行数 ≥ 13（覆盖全部子系统）", n >= 13, f"{n} 行"),
        ("每行 failure_mode 非空",
         all(str(r.get("failure_mode") or "").strip() for r in rows)),
        ("每行 failure_effect 非空",
         all(str(r.get("failure_effect") or "").strip() for r in rows)),
        ("每行 failure_cause 非空",
         all(str(r.get("failure_cause") or "").strip() for r in rows)),
        ("每行 action 非空",
         all(str(r.get("action") or "").strip() for r in rows)),
    ])

    # ---------------- C 类：9 个类比题 ----------------
    for j, (sub, kws, fam) in enumerate(C_SUBSYS, start=22):
        rs = rows_for(rows, kws)
        hist_rows_c = [r for r in rs
                       if any(src_of(r, f).startswith("history#")
                              for f in KEY_FIELDS)]
        exp_rows_c = [r for r in rs
                      if any(src_of(r, f).startswith("expert:")
                             for f in KEY_FIELDS)]
        fam_ok = False
        for r in hist_rows_c:
            for f in KEY_FIELDS:
                v = src_of(r, f)
                if not v.startswith("history#"):
                    continue
                nid = v.split("#", 1)[1]
                if nid.isdigit() and int(nid) in case_by_id:
                    pn = str(case_by_id[int(nid)]["part_no"])
                    if "-" in pn and pn.split("-")[1] == fam:
                        fam_ok = True
        ap_ok_c = [r for r in rs if str(r.get("ap") or "").strip()
                   and ap_map.get((r.get("severity"), r.get("occurrence"),
                                   r.get("detection"))) == str(r["ap"]).strip()]
        src_ok_c = [r for r in rs if all(
            src_base(src_of(r, f)) in CLOSED
            for f in KEY_FIELDS if src_of(r, f))]
        # C-1「有历史引用」与 C-2「有专家引用」是**同一件事的两条路**（考卷原文
        # 写的就是"历史 或 专家"）—— 早先误写成两条硬断言，导致"只用历史、
        # 没问专家"的子系统被冤枉扣分（首轮 5 点即此）。
        ev_c = len({id(r) for r in hist_rows_c} | {id(r) for r in exp_rows_c})
        sc.add(j, "类比", sub, [
            ("有引用历史库的行", len(hist_rows_c) >= 1, f"{len(hist_rows_c)} 行"),
            ("或有引用专家的行（二者之一）", ev_c >= 1,
             f"hist={len(hist_rows_c)} exp={len(exp_rows_c)}"),
            (f"引用了 {fam} 族的历史案例", fam_ok),
            ("该系统来源标注全部合法",
             len(src_ok_c) == len(rs) if rs else False, f"{len(src_ok_c)}/{len(rs)}"),
            ("该系统至少 1 条 AP 与表一致", len(ap_ok_c) >= 1, f"{len(ap_ok_c)} 条"),
        ])

    # ---------------- 汇总 ----------------
    rate = sc.rate()
    print("=" * 68)
    print(f"判定点：{sc.passed}/{sc.total}")
    print(f"题目全对数：{sc.q_all_ok}/30")
    print(f"正确率：{rate * 100:.1f}%   阈值 {THRESHOLD * 100:.0f}%   "
          f"→ {'达标 ✅' if rate >= THRESHOLD else '未达标 ❌'}")
    fails = [(q, kind, title, [(lbl, d) for lbl, ok, d in cs if not ok])
             for q, kind, title, cs in sc.detail
             if any(not ok for _, ok, _ in cs)]
    if fails:
        print("\n未通过明细：")
        for q, kind, title, items in fails:
            print(f"  [题 {q}] {kind}·{title}")
            for lbl, d in items:
                print(f"      ✗ {lbl}" + (f"   {d}" if d else ""))
    print("=" * 68)
    return {"run_id": rid, "rows": len(rows), "passed": sc.passed,
            "total": sc.total, "rate": rate, "q_all_ok": sc.q_all_ok,
            "detail": sc.detail}


def main() -> int:
    r = evaluate(db.get_conn())
    print("\nRESULT:", "PASS" if r["rate"] >= THRESHOLD else "FAIL")
    return 0 if r["rate"] >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
