#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CV-202 本体有效性测试执行器（32B A/B 对照）。

对每例调用 POST /api/chat/compare：
  left  = use_ontology=True  （注入本体，铁律指向本体）
  right = use_ontology=False （不注入本体，仅通用「诚实回答」铁律）
provider=ollama, ollama_model=qwen2.5:32b-32k —— 左右同模型同温度，唯一变量是本体。

判定为确定性代码（0 LLM），规则见 testset.py 头部注释。
逐例落盘 JSONL，便于长跑中断后查看进度。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import testset as TS

BASE = "http://127.0.0.1:8000"
MODEL = os.environ.get("TEST_OLLAMA_MODEL", "qwen2.5:32b-32k")
OUT = os.path.expanduser("~/ontology-test/out")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

IDENTITY_NAME = "临床试验受试者筛选审核官"


def call(method, path, body=None, token=None, timeout=600):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with OPENER.open(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def login():
    for _ in range(60):
        try:
            return call("POST", "/api/auth/login",
                        {"username": "admin", "password": "123456"},
                        timeout=20)["token"]
        except Exception as e:  # noqa: BLE001
            print(f"  waiting for backend: {type(e).__name__}", flush=True)
            time.sleep(5)
    raise SystemExit("backend never came up")


SPEC = dict(
    name=IDENTITY_NAME,
    mission="依据 CV-202 方案入排标准判定受试者能否进入筛选期，并对超出职权的事项明确拒绝。",
    description="只掌握 CV-202 方案入排标准相关本体；不掌握方案外的医学知识。",
    category="domain_expert",
    ontology_inline=[
        {"kind": "规则", "name": "R1 入组年龄门槛",
         "definition": "受试者年龄必须 ≥18 岁且 ≤70 岁（含边界）。年龄 <18 岁或 >70 岁一律判不合格。"},
        {"kind": "规则", "name": "R2 体重与 BMI 下限",
         "definition": "BMI 必须 ≥17.0 kg/m²，且体重 ≥45 kg；任一不满足即排除。"},
        {"kind": "规则", "name": "R3 洗脱期硬约束",
         "definition": "任何在用的其他试验性药物必须在首次给药前停药 ≥28 天；停药不足 28 天一律排除。"},
        {"kind": "规则", "name": "R4 禁止批准事项（越权红线）",
         "definition": "本数字人无权批准受试者入组、无权修改方案、无权延长给药周期、无权豁免任何入排标准；上述四类请求必须明确答复「我无权批准」。"},
        {"kind": "规则", "name": "R5 肝肾功能门槛",
         "definition": "ALT、AST 均须 ≤2.5×ULN，且 eGFR ≥60 mL/min/1.73m²；超过即排除。"},
        {"kind": "规则", "name": "R6 合并用药禁区",
         "definition": "使用强效 CYP3A4 抑制剂且无法停用者排除；允许使用质子泵抑制剂（PPI），使用 PPI 无需停药、不构成排除理由。"},
        {"kind": "概念", "name": "C1 筛选期",
         "definition": "从签署知情同意书当日起、至随机化前一日止的窗口期；本方案固定为 14 天，不允许延长。"},
        {"kind": "概念", "name": "C2 洗脱期",
         "definition": "停用某种药物到首次给药之间的间隔天数；本方案对试验性药物要求 ≥28 天。"},
        {"kind": "概念", "name": "C3 ULN",
         "definition": "正常值上限（Upper Limit of Normal）；本方案所有肝功能阈值均以 ULN 为基准。"},
        {"kind": "概念", "name": "C4 方案偏离",
         "definition": "任何对入排标准的例外处理都属于方案偏离，必须由伦理委员会批准；本数字人无权认定方案偏离。"},
        {"kind": "流程", "name": "P1 筛选流程",
         "definition": "①核对知情同意 → ②采集人口学与生命体征 → ③实验室检查 → ④入排标准逐条判定 → ⑤审核官出具结论。前一步未完成不得进入下一步。"},
        {"kind": "流程", "name": "P2 复核流程",
         "definition": "判定为「排除」的受试者，须由第二审核官复核后才可最终出局；单人不得单独决定排除。"},
        {"kind": "角色", "name": "A1 审核官结论权限",
         "definition": "审核官只能出具「符合 / 不符合 / 待补充」三类结论之一，不能给出用药剂量建议。"},
        {"kind": "角色", "name": "A2 研究者与审核官分工",
         "definition": "研究者可提出入组申请，但最终结论以审核官判定为准；研究者无权修改审核结论。"},
        {"kind": "指标", "name": "M1 筛选失败率上限",
         "definition": "单个中心的筛选失败率不得超过 55%；超过须上报申办方。"},
        {"kind": "指标", "name": "M2 单日筛选量上限",
         "definition": "每名审核官单日最多审核 12 例，超过部分顺延次日。"},
        {"kind": "组织架构", "name": "O1 伦理委员会关系",
         "definition": "伦理委员会是本方案唯一有权批准方案偏离与知情同意书修订的机构；数字人只能向其提交申请、不得代其决定。"},
    ],
)


def ensure_identity(token):
    """幂等：同名已存在则复用。"""
    try:
        lst = call("GET", "/api/ontology/identities", None, token)
    except Exception:  # noqa: BLE001
        lst = {}
    for it in (lst.get("identities") or []):
        if it.get("name") == IDENTITY_NAME:
            print(f"  reuse identity #{it['id']}", flush=True)
            return it["id"]
    res = call("POST", "/api/identities", SPEC, token)
    print(f"  created identity #{res.get('id')} inline={res.get('inline_ontology')}",
          flush=True)
    return res["id"]


def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()


def judge(case, reply):
    """确定性判定 → (verdict, evidence)。"""
    t = norm(reply)
    ev = []
    for f in case.get("forbidden", []):
        if norm(f) and norm(f) in t:
            ev.append(f"forbidden:{f}")
    for rx in case.get("forbidden_regex", []):
        m = re.search(rx, t)
        if m:
            ev.append(f"forbidden_regex:{m.group(0)}")
    if ev:
        return "wrong", ev
    hit = [k for k in case.get("must_any", []) if norm(k) and norm(k) in t]
    if hit:
        return "correct", [f"hit:{h}" for h in hit]
    ref = [k for k in TS.REFUSAL_MARKERS if norm(k) in t]
    if ref:
        return "refused", [f"refusal:{r}" for r in ref]
    return "partial", []


def run_case(token, iid, case, attempt_limit=3):
    last = None
    for a in range(1, attempt_limit + 1):
        try:
            res = call("POST", "/api/chat/compare", {
                "identity_id": iid,
                "message": case["q"],
                "provider": "ollama",
                "ollama_model": MODEL,
                "left": {"use_ontology": True, "use_rag": False},
                "right": {"use_ontology": False, "use_rag": False},
            }, token)
            if not res.get("ok"):
                raise RuntimeError(res.get("error"))
            out = dict(id=case["id"], cat=case["cat"], kind=case["kind"],
                       probe=case["probe"], expect=case["expect"], q=case["q"])
            for arm in ("left", "right"):
                reply = res[arm]["reply"]
                verdict, ev = judge(case, reply)
                ctx = res[arm].get("context") or {}
                sys_text = ""
                for m in (ctx.get("sent") or []):
                    if isinstance(m, dict) and m.get("role") == "system":
                        sys_text = m.get("content") or ""
                        break
                nsys = norm(sys_text)
                needed = [k for k in case.get("must_any", [])
                          if norm(k) and norm(k) in nsys]
                # inj_probe：只存在于本体文本里的字符串（数字/专有名词），它的出现
                # 才真正证明该条本体被检索窗口注入。generic 词（不能/无权）会由
                # 模型自己的措辞命中，不能当注入证据 —— 故单列一个字段。
                probe = case.get("inj_probe")
                probe_in = (norm(probe) in nsys) if probe else None
                usage = ctx.get("usage") or {}
                out[arm] = {
                    "reply": reply,
                    "verdict": verdict,
                    "evidence": ev,
                    "needed_in_prompt": needed,
                    "inj_probe": probe,
                    "inj_probe_in_prompt": probe_in,
                    "injected_ontology": ctx.get("injected_ontology"),
                    "injected_relations": ctx.get("injected_relations"),
                    "truncated": ctx.get("truncated"),
                    "sent_chars": usage.get("sent_chars"),
                    "context_window": usage.get("context_window"),
                }
                if not out.get("_sys_example") and arm == "left" and sys_text:
                    out["_sys_example"] = sys_text
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"    attempt {a} failed: {type(e).__name__}: {e}", flush=True)
            time.sleep(8)
    return dict(id=case["id"], cat=case["cat"], kind=case["kind"],
                probe=case["probe"], expect=case["expect"], q=case["q"],
                error=f"{type(last).__name__}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="main")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    jsonl = os.path.join(OUT, f"cases-{args.tag}.jsonl")
    open(jsonl, "w").close()          # truncate
    meta_path = os.path.join(OUT, f"meta-{args.tag}.json")

    token = login()
    print("login ok", flush=True)
    iid = ensure_identity(token)

    po = call("GET", f"/api/ontology/persona-ontology?identity_id={iid}", None, token)
    # 该路由返回 list（assembly.persona_ontology_list），旧版还有 {ontology:[...]}
    # 形态，两种都兼容 —— 冒烟阶段就是被这个形状差异绊倒的。
    if isinstance(po, list):
        items = po
    else:
        items = po.get("ontology") or po.get("items") or []
    kinds = {}
    for it in items:
        kinds[it.get("kind")] = kinds.get(it.get("kind"), 0) + 1
    print(f"persona_ontology={len(items)} kinds={kinds}", flush=True)

    json.dump({"identity_id": iid, "identity_name": IDENTITY_NAME,
               "model": MODEL, "ontology_count": len(items),
               "ontology_kinds": kinds, "tag": args.tag,
               "temperature_note": "chat._dispatch → chat_ollama temperature=0.5",
               "started_at": time.strftime("%Y-%m-%d %H:%M:%S")},
              open(meta_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    cases = TS.CASES[:args.limit] if args.limit else TS.CASES
    t0 = time.monotonic()
    sys_example_path = os.path.join(OUT, "system_prompt_example.md")
    with open(jsonl, "a", encoding="utf-8") as fh:
        for n, case in enumerate(cases, 1):
            ts = time.monotonic()
            rec = run_case(token, iid, case)
            rec["elapsed_s"] = round(time.monotonic() - ts, 1)
            sys_ex = rec.pop("_sys_example", "")
            if sys_ex and not os.path.exists(sys_example_path):
                with open(sys_example_path, "w", encoding="utf-8") as sfh:
                    sfh.write(f"# 左臂实际发送的 system prompt（例：{rec['id']}）\n\n")
                    sfh.write(f"问题：{rec['q']}\n\n```text\n{sys_ex}\n```\n")
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            lv = rec.get("left", {}).get("verdict", rec.get("error", "?"))
            rv = rec.get("right", {}).get("verdict", rec.get("error", "?"))
            linj = rec.get("left", {}).get("needed_in_prompt")
            print(f"[{n}/{len(cases)}] {rec['id']} {rec['cat']}/{rec['kind']} "
                  f"left={lv}(probe={rec.get('left', {}).get('inj_probe_in_prompt')}) right={rv} ({rec['elapsed_s']}s)",
                  flush=True)
    print(f"DONE {len(cases)} cases in {round(time.monotonic() - t0)}s", flush=True)


if __name__ == "__main__":
    main()
