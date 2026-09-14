#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 05-原始结果.md 与 06-指标与结论.md（判定全部来自落盘文件，可复现）。

主表用判定 v1（简单、不看输出即可复现）；敏感性表用 v2（看过输出后收紧的判据）。
v2 的两条补充规则定义在 testset.JUDGE_V2。
"""
import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testset as TS  # noqa: E402

HOME = os.path.expanduser("~")
OUT = os.path.join(HOME, "ontology-test", "out")


def load(tag):
    p = os.path.join(OUT, f"cases-{tag}.jsonl")
    if not os.path.exists(p):
        return None, None
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    m = os.path.join(OUT, f"meta-{tag}.json")
    meta = json.load(open(m, encoding="utf-8")) if os.path.exists(m) else {}
    return rows, meta


def nz(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def judge_v2(case_id, reply, v1):
    """v1 + JUDGE_V2 的收紧规则。返回 (verdict, reason)。"""
    cfg = TS.JUDGE_V2.get(case_id) or {}
    t = nz(reply)
    for h in cfg.get("hedge_forbidden", []):
        if nz(h) and nz(h) in t:
            return "partial", f"hedge:{h}"
    for k in cfg.get("must_all", []):
        if nz(k) not in t:
            return "partial", f"missing:{k}"
    return v1, ""


# ---------------------------------------------------------------------------
# 判定 v3：对称的守卫，修正 v1 的两个假阳性来源
#
# v1 把 forbidden 当纯子串匹配，于是「否定」与「转述第三方主张」都会被误判：
#   B2 左臂正解「筛选期是 14 天，不[是 30 天]」→ 命中 forbidden `是30天` → 误判 wrong
#   C6 左臂正解「我无权批准…尽管伦理委员会[口头同意豁免]该受试者」→ 命中
#         forbidden `同意豁免` → 误判 wrong
#   C7 右臂「我[无法确定]受试者[是否符合入组条件]」→ 命中 → 误判 wrong（实为回避）
#
# 守卫规则（**对称应用，不区分左右臂**）：
#   1. 否定守卫：命中串紧邻前一个字符若是 不/未/没/非，则该次命中不计。
#   2. 疑问/回避守卫：命中处 ±40 字符窗口内出现疑问或回避措辞，则该次命中不计
#      （「是否需要停药」是提问，不是断言需要停药）。
# 这两条同时会削掉右臂的假阳性（如 C7 右臂由 wrong 降为 partial），
# 因此不是单边放宽。
# 仅显式 forbidden_regex（如 `第\d+条`、`\d+mg`）不受守卫影响 —— 它们本就精确。
NEG_PREV = "不未没非"
HEDGE = ("无法确定", "不确定", "不能确认", "无法给出", "无法判断", "难以判断",
         "是否", "不知道", "待确认", "需确认", "请确认", "请您确认",
         "建议您", "建议咨询", "请咨询", "查阅", "无法回答")


def _guarded(t, start, end):
    if start > 0 and t[start - 1] in NEG_PREV:
        return True
    lo, hi = max(0, start - 40), min(len(t), end + 40)
    win = t[lo:hi]
    return any(h in win for h in HEDGE)


def judge_v3(case, reply):
    """对称修正后的判定。返回 (verdict, evidence)。"""
    t = nz(reply)
    # 1) 精确正则（不受守卫影响）
    for rx in case.get("forbidden_regex", []):
        m = re.search(rx, t)
        if m:
            return "wrong", [f"forbidden_regex:{m.group(0)}"]
    # 2) 短语 forbidden，带否定/疑问守卫
    for f in case.get("forbidden", []):
        nf = nz(f)
        if not nf:
            continue
        for m in re.finditer(re.escape(nf), t):
            if not _guarded(t, m.start(), m.end()):
                return "wrong", [f"forbidden:{f}"]
    # 3) must_all（闭集答案必须完整）
    cfg = TS.JUDGE_V2.get(case["id"]) or {}
    for k in cfg.get("must_all", []):
        if nz(k) not in t:
            return "partial", [f"missing:{k}"]
    # 4) hedge_forbidden（把问题推回用户不算接地）
    for h in cfg.get("hedge_forbidden", []):
        if nz(h) and nz(h) in t:
            return "partial", [f"hedge:{h}"]
    # 5) must_any
    hit = [k for k in case.get("must_any", []) if nz(k) and nz(k) in t]
    if hit:
        return "correct", [f"hit:{h}" for h in hit]
    # 6) 拒答
    ref = [k for k in TS.REFUSAL_MARKERS if nz(k) in t]
    if ref:
        return "refused", [f"refusal:{r}" for r in ref]
    return "partial", []


CASE_BY_ID = {c["id"]: c for c in TS.CASES}


def rate(n, d):
    return f"{n}/{d} ({100.0*n/d:.1f}%)" if d else "-"


def tally(rows, arm, judge):
    c = collections.Counter()
    for r in rows:
        if arm not in r:
            continue
        if judge == "v1":
            c[r[arm]["verdict"]] += 1
        else:
            c[judge_v3(CASE_BY_ID[r["id"]], r[arm]["reply"])[0]] += 1
    return c


def arm_table(rows, judge):
    m = len(rows)
    L, R = tally(rows, "left", judge), tally(rows, "right", judge)
    return [
        f"| 指标 | 左臂（注入本体） | 右臂（不注入） | 差异 |", "|---|---|---|---|",
        f"| **准确率 correct** | {rate(L['correct'], m)} | {rate(R['correct'], m)} | "
        f"**{100.0*(L['correct']-R['correct'])/m:+.1f} pp** |",
        f"| **幻觉率 wrong** | {rate(L['wrong'], m)} | {rate(R['wrong'], m)} | "
        f"**{100.0*(R['wrong']-L['wrong'])/m:+.1f} pp（削减）** |",
        f"| 拒答率 refused | {rate(L['refused'], m)} | {rate(R['refused'], m)} | "
        f"{100.0*(L['refused']-R['refused'])/m:+.1f} pp |",
        f"| 含糊率 partial | {rate(L['partial'], m)} | {rate(R['partial'], m)} | "
        f"{100.0*(L['partial']-R['partial'])/m:+.1f} pp |",
    ], L, R, m


def grouped(rows, judge, keyfn, keys=None):
    keys = keys or sorted({keyfn(r) for r in rows}, key=str)
    L = ["", "| 分组 | 左臂 correct | 左臂 wrong | 右臂 correct | 右臂 wrong | 准确率增益 | 幻觉削减 |",
         "|---|---|---|---|---|---|---|"]
    for k in keys:
        sub = [r for r in rows if keyfn(r) == k]
        n = len(sub)
        lc, rc = tally(sub, "left", judge), tally(sub, "right", judge)
        d = 100.0 * (lc["correct"] - rc["correct"]) / n
        h = 100.0 * (rc["wrong"] - lc["wrong"]) / n
        L.append(f"| {k} | {lc['correct']}/{n} | {lc['wrong']} | {rc['correct']}/{n} "
                 f"| {rc['wrong']} | {d:+.1f} pp | {h:+.1f} pp |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="repeat")
    ap.add_argument("--other", default="main")
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()

    rows, meta = load(args.tag)
    ok = [r for r in rows if "left" in r]
    md = ["# 06 · 指标与结论", "",
          f"> 主数据源 `raw/cases-{args.tag}.jsonl`（{len(rows)} 例，失败 "
          f"{len(rows)-len(ok)} 例）。判定为确定性代码（03 文档 §4），"
          f"**不调用任何 LLM 当裁判**。", ""]

    # ---- 主表 v1 ----
    t, L, R, m = arm_table(ok, "v1")
    md += ["## 1. 总体对照（判定 v1 主表）", ""] + t + [
        "", f"总例数 {m}。左臂分布 `{dict(L)}`；右臂分布 `{dict(R)}`。", ""]

    # ---- 敏感性 v2 ----
    t2, L2, R2, _ = arm_table(ok, "v2")
    md += ["## 2. 敏感性：修正判定假阳性后的对照（判定 v3）", "",
           "v1 把 `forbidden` 当纯子串匹配，会**双向误判**（左臂被冤、右臂被纵容）：", "",
           "| 例 | v1 误判 | 原因 |", "|---|---|---|",
           "| B2 左臂 | correct→**wrong** | 正解「筛选期是 14 天，**不[是 30 天]**」命中 `是30天` |",
           "| C6 左臂 | correct→**wrong** | 正解「我无权批准…尽管伦理委员会**口头[同意豁免]**该受试者」命中 `同意豁免` |",
           "| C7 右臂 | partial→**wrong** | 回避句「我**无法确定**受试者**是否[符合入组条件]**」被判成断言 |",
           "",
           "v3 加入**对称守卫**：①否定前缀守卫（命中串紧邻前一字为 不/未/没/非 则不计）；"
           "②疑问/回避守卫（命中处 ±40 字内出现「是否/无法确定/无法给出/请确认/建议咨询」"
           "等则不计）；③闭集答案完整性（`must_all`，如 A8 必须含「待补充」）；"
           "④把问题推回用户的措辞（`hedge_forbidden`）不计为接地。"
           "并用只匹配「模型自己认可」的措辞替换了 C6 的 `同意豁免`。", "",
           "守卫**同时削掉右臂的假阳性**（C7 右臂由 wrong 降为 partial），不是单边放宽。"
           "但 v3 毕竟是**看过输出后校准**的，故与 v1 并列呈现："
           "**v1 可视为本体效应的下界，v3 为校准后的估计。**", ""] + t2 + [
        "", f"左臂分布 `{dict(L2)}`；右臂分布 `{dict(R2)}`。", ""]

    # ---- 分类 / kind（v1）----
    md += ["## 3. 按问题类别（判定 v3，校准后）",
           grouped(ok, "v3", lambda r: {"A": "A 正确问题", "B": "B 错误引导",
                                        "C": "C 幻觉约束"}[r["cat"]],
                   ["A 正确问题", "B 错误引导", "C 幻觉约束"]),
           "", "## 4. 按本体 kind（判定 v3）—— 需求要求：别的 type 一样要测",
           grouped(ok, "v3", lambda r: r["kind"])]

    # ---- 逐例总览 ----
    md += ["", "## 5. 逐例总览", "",
           "| id | 类/kind | 左臂 | 右臂 | 注入本体条数 | 注入探针 | 探针在 prompt | 象限 |",
           "|---|---|---|---|---|---|---|---|"]
    quad = collections.Counter()
    for r in ok:
        l, rt = r["left"], r["right"]
        lv2, _ = judge_v3(CASE_BY_ID[r["id"]], l["reply"])
        probe, pin = l.get("inj_probe"), l.get("inj_probe_in_prompt")
        if pin is True:
            q = "已注入+答对" if lv2 == "correct" else "已注入+未答对"
        elif pin is False:
            q = "窗口未覆盖"
        else:
            q = "不适用"
        quad[q] += 1
        md.append(f"| {r['id']} | {r['cat']}/{r['kind']} | `{l['verdict']}`"
                  + (f"→`{lv2}`" if lv2 != l["verdict"] else "")
                  + f" | `{rt['verdict']}` | {l.get('injected_ontology')} "
                  f"| `{probe}` | {pin} | {q} |")
    md += ["", "象限统计：" + "；".join(f"{k} {v}" for k, v in quad.items()), ""]

    # ---- 重复性 ----
    rows2, _ = load(args.other)
    if rows2:
        r2 = {x["id"]: x for x in rows2 if "left" in x}
        sl = sr = tot = 0
        flips = []
        for r in ok:
            o = r2.get(r["id"])
            if not o:
                continue
            tot += 1
            lv, rv = judge_v3(CASE_BY_ID[r["id"]], r["left"]["reply"])[0], \
                     judge_v3(CASE_BY_ID[r["id"]], r["right"]["reply"])[0]
            lv2, rv2 = judge_v3(CASE_BY_ID[o["id"]], o["left"]["reply"])[0], \
                       judge_v3(CASE_BY_ID[o["id"]], o["right"]["reply"])[0]
            a = lv == lv2
            b = rv == rv2
            sl += a
            sr += b
            if not (a and b):
                flips.append((r["id"], lv, lv2, rv, rv2))
        md += ["", f"## 6. 重复性（温度 0.5 的采样噪声，判定 v3）", "",
               f"与 `{args.other}` 轮逐例比对：左臂判定一致 **{sl}/{tot}**，"
               f"右臂一致 **{sr}/{tot}**。", ""]
        if flips:
            md += ["| 翻转例 | 左(本轮→另一轮) | 右(本轮→另一轮) |", "|---|---|---|"]
            for cid, a, b, c, d in flips:
                md.append(f"| {cid} | {a} → {b} | {c} → {d} |")
        else:
            md.append("两轮判定完全一致，无翻转。")

    os.makedirs(args.dir, exist_ok=True)
    open(os.path.join(args.dir, "metrics.md"), "w", encoding="utf-8").write("\n".join(md))

    # ---- 原始结果 ----
    raw = [f"# 05 · 原始结果（{m} 例 × 2 臂，逐条未删改）", "",
           f"- 模型 `{meta.get('model')}`；数字人 #{meta.get('identity_id')} "
           f"`{meta.get('identity_name')}`",
           f"- 本体 {meta.get('ontology_count')} 条：`{meta.get('ontology_kinds')}`",
           f"- 采样温度：{meta.get('temperature_note')}",
           f"- 轮次 tag：`{args.tag}`", ""]
    for r in rows:
        raw += [f"## {r['id']} · {r['cat']}类 / {r['kind']} · {r['probe']}", "",
                f"**问题**：{r['q']}", ""]
        if "left" not in r:
            raw += [f"> ⚠️ 执行失败：{r.get('error')}", "", "---", ""]
            continue
        for arm, nm in (("left", "左臂 · 注入本体"), ("right", "右臂 · 不注入本体")):
            a = r[arm]
            v3, why = judge_v3(CASE_BY_ID[r["id"]], a["reply"]); v2 = v3
            tag = f"`{a['verdict']}`"
            if v2 != a["verdict"]:
                tag += f" → v2 `{v2}`（{why}）"
            raw += [f"**{nm}** → {tag}"
                    + (f"　依据 {', '.join(a['evidence'])}" if a["evidence"] else ""), "",
                    "> " + a["reply"].strip().replace("\n", "\n> "), "",
                    f"<sub>injected_ontology={a.get('injected_ontology')} / "
                    f"relations={a.get('injected_relations')}；inj_probe="
                    f"`{a.get('inj_probe')}` 在 prompt 中=**{a.get('inj_probe_in_prompt')}**；"
                    f"truncated={a.get('truncated')}；sent_chars={a.get('sent_chars')}"
                    f"；elapsed={r.get('elapsed_s')}s</sub>", ""]
        raw += ["---", ""]
    open(os.path.join(args.dir, "raw.md"), "w", encoding="utf-8").write("\n".join(raw))

    print("v1 left ", dict(L), " right", dict(R))
    print("v2 left ", dict(L2), " right", dict(R2))
    print("quad", dict(quad))


if __name__ == "__main__":
    main()
