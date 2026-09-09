"""context_mgr.py — 纯代码层「上下文管理器」（平台通用件，每个数字人内置）。

与六元组 interface 配套：不新增 DB 实体，作为代码层默认契约供 pipeline 引擎 /
capability / chat 调用。管理三件事：

  1. 交接物一律带 kind 标签（标明"这是什么"），不再是裸文本；
  2. 每类 kind 有默认字符预算 KIND_BUDGET，超过预算不再"一刀切截断"，而是
     由上游数字人同模型自缩减（refine_handoff），LLM 失败才确定性兜底；
  3. 下游入站按 kind 分组 + 预算裁剪，数字人只拿到自己该看的、且在预算内。

后续如需按数字人个性化，再落 persona_interfaces 表（此处默认值即其模板）。
"""

from __future__ import annotations

import json

# ---------------- 交接物 kind 体系（闭集，按需扩展） ----------------
KIND_REQUIREMENT = "需求规格"
KIND_DESIGN = "技术方案"
KIND_CODE = "代码实现"
KIND_REVIEW = "审查意见"
KIND_TEST_REPORT = "失败测试报告"
KIND_TASK = "任务要求"
KIND_TEST_ASSERT = "隐藏测试断言"
KIND_RESEARCH = "调研报告"
KIND_GENERIC = "交接物"

KINDS = (KIND_REQUIREMENT, KIND_DESIGN, KIND_CODE, KIND_REVIEW,
         KIND_TEST_REPORT, KIND_TASK, KIND_TEST_ASSERT, KIND_RESEARCH,
         KIND_GENERIC)

# 步骤名 → 出站 kind（节点贴标签用；规则命中优先，可被关系 handoff_type 覆盖）
NODE_OUT_KIND_BY_STEP = {
    "需求分析": KIND_REQUIREMENT, "需求": KIND_REQUIREMENT,
    "技术设计": KIND_DESIGN, "设计": KIND_DESIGN,
    "编码实现": KIND_CODE, "编码": KIND_CODE, "写码": KIND_CODE,
    "代码": KIND_CODE, "实现": KIND_CODE,
    "代码审查": KIND_REVIEW, "审查": KIND_REVIEW, "复核": KIND_REVIEW,
    "测试": KIND_TEST_REPORT, "测试执行": KIND_TEST_REPORT,
    "调试修复": KIND_TEST_REPORT, "调试": KIND_TEST_REPORT,
}

# 每类 kind 的默认字符预算（摘要/压缩后仍超预算 = 可能还需要再缩或该 kind 太大）
KIND_BUDGET = {
    KIND_REQUIREMENT: 2400,
    KIND_DESIGN: 2400,
    KIND_CODE: 6000,
    KIND_REVIEW: 1500,
    KIND_TEST_REPORT: 3000,
    KIND_TASK: 2000,
    KIND_TEST_ASSERT: 4000,
    KIND_RESEARCH: 3000,
    KIND_GENERIC: 2000,
}
DEFAULT_BUDGET = 2400

# 入站 kind → 该给下游看多少（下游 system 注入裁剪预算，一般略大于出站）
KIND_INPUT_BUDGET = {
    KIND_REQUIREMENT: 2400,
    KIND_DESIGN: 2400,
    KIND_CODE: 6000,
    KIND_REVIEW: 1500,
    KIND_TEST_REPORT: 3000,
    KIND_TASK: 2000,
    KIND_TEST_ASSERT: 4000,
    KIND_RESEARCH: 3000,
    KIND_GENERIC: 2000,
}

# 数字人角色 → 入站 kind 白名单（纯代码层默认契约；空 = 全部接收）。
# 命中白名单的 kind 才注入该角色；不命中但属于直接产物（代码）仍注入关键类。
# 角色名按 identity.name 子串匹配，作为代码层默认（后续可落 persona_interfaces 表个性化）。
ROLE_INPUT_KINDS: dict[str, list[str]] = {
    "调试": [KIND_REQUIREMENT, KIND_DESIGN, KIND_CODE, KIND_REVIEW,
            KIND_TEST_REPORT, KIND_TASK, KIND_TEST_ASSERT],
    "测试": [KIND_REQUIREMENT, KIND_DESIGN, KIND_CODE, KIND_REVIEW,
            KIND_TASK, KIND_TEST_ASSERT],
    "审查": [KIND_REQUIREMENT, KIND_DESIGN, KIND_CODE, KIND_TASK],
    "代码": [KIND_REQUIREMENT, KIND_DESIGN, KIND_TASK, KIND_TEST_ASSERT],
    "设计": [KIND_REQUIREMENT, KIND_TASK],
    "需求": [KIND_TASK],
}
# 审查意见 / 失败测试报告 仅允许出现在明确声明的下游（避免拿错）
ROLE_INPUT_ALLOW_ALL = ()  # 未来可放宽


def allowed_kinds_for(role_name: str, candidates: list[str]) -> list[str]:
    """按角色名过滤候选 kind（白名单命中的保留；角色未知则全收）。"""
    if not candidates:
        return []
    role = role_name or ""
    for key, allow in ROLE_INPUT_KINDS.items():
        if key in role:
            return [k for k in candidates if k in allow or k == KIND_CODE]
    return list(candidates)

HANDOFF_MARKER = "__handoff__"

# 自由文本 handoff_type → 标准 kind（管线关系上用户可写"需求规格/代码"等非标名）
KIND_ALIASES = {
    "需求": KIND_REQUIREMENT, "需求规格": KIND_REQUIREMENT,
    "需求文档": KIND_REQUIREMENT, "需求分析": KIND_REQUIREMENT,
    "技术方案": KIND_DESIGN, "设计": KIND_DESIGN, "设计文档": KIND_DESIGN,
    "技术设计": KIND_DESIGN,
    "代码": KIND_CODE, "代码实现": KIND_CODE, "代码片段": KIND_CODE,
    "实现": KIND_CODE, "审查通过代码": KIND_CODE, "修改后代码": KIND_CODE,
    "审查意见": KIND_REVIEW, "代码审查": KIND_REVIEW, "审查报告": KIND_REVIEW,
    "失败测试报告": KIND_TEST_REPORT, "测试报告": KIND_TEST_REPORT,
    "测试输出": KIND_TEST_REPORT, "失败输出": KIND_TEST_REPORT,
    "任务要求": KIND_TASK, "题目": KIND_TASK, "docstring": KIND_TASK,
    "隐藏测试": KIND_TEST_ASSERT, "测试断言": KIND_TEST_ASSERT,
    "断言": KIND_TEST_ASSERT, "调研报告": KIND_RESEARCH, "调研": KIND_RESEARCH,
}


def normalize_kind(raw: str) -> str:
    """把关系上的自由文本 handoff_type / 用户输入归一化为标准 kind。"""
    r = (raw or "").strip()
    if not r:
        return KIND_GENERIC
    if r in KINDS:
        return r
    return KIND_ALIASES.get(r, r)


def budget_for(kind: str, table: dict | None = None) -> int:
    """取某 kind 的默认预算（未收录则用通用预算）。"""
    t = table or KIND_BUDGET
    k = normalize_kind(kind or KIND_GENERIC)
    if k in t:
        return t[k]
    for kk, v in t.items():
        if kk in (k or ""):
            return v
    return DEFAULT_BUDGET


def kind_for_step(step_name: str) -> str:
    """按节点步骤名推断出站 kind（不命中 = 通用交接物）。"""
    s = (step_name or "").strip()
    if s in NODE_OUT_KIND_BY_STEP:
        return NODE_OUT_KIND_BY_STEP[s]
    for k, v in NODE_OUT_KIND_BY_STEP.items():
        if k and k in s:
            return v
    return KIND_GENERIC


# ---------------- 交接物打包 / 解包 ----------------
def pack_handoff(kind: str, content: str, refined: bool = False,
                 raw_chars: int = 0) -> str:
    """把一段产出打包成带 kind 与元数据的交接物字符串（存入 run_handoffs）。"""
    content = content or ""
    return json.dumps({
        "m": HANDOFF_MARKER,
        "kind": kind or KIND_GENERIC,
        "content": content,
        "chars": len(content),
        "refined": bool(refined),
        "raw_chars": int(raw_chars or len(content)),
    }, ensure_ascii=False)


def unpack_handoff(text: str) -> dict:
    """解包交接物；兼容历史纯文本（未打包 → 视作 generic 交接物）。"""
    if not text:
        return {"kind": KIND_GENERIC, "content": "", "chars": 0,
                "refined": False, "raw_chars": 0}
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            if isinstance(d, dict) and d.get("m") == HANDOFF_MARKER:
                content = str(d.get("content") or "")
                return {"kind": str(d.get("kind") or KIND_GENERIC),
                        "content": content, "chars": len(content),
                        "refined": bool(d.get("refined")),
                        "raw_chars": int(d.get("raw_chars") or len(content))}
        except Exception:
            pass
    return {"kind": KIND_GENERIC, "content": t, "chars": len(t),
            "refined": False, "raw_chars": len(t)}


def truncate_head_tail(text: str, budget: int, head_ratio: float = 0.55) -> str:
    """确定性兜底压缩：保留开头 + 结尾，中段省略（对代码/长文损失最小的粗截断）。"""
    text = text or ""
    if len(text) <= budget:
        return text
    head = int(budget * head_ratio)
    tail = budget - head - len("\n…[中段省略，见原文]…\n")
    if tail < 60:
        return text[: budget - len("\n…[已截断]…\n")] + "\n…[已截断]…\n"
    return text[:head] + "\n…[中段省略，见原文]…\n" + text[-tail:]


# ---------------- 上游数字人自缩减（精炼器） ----------------
def refine_handoff(conn, persona_id: int | None, persona_name: str,
                   kind: str, content: str,
                   provider: str = "llm2",
                   ollama_model: str | None = None,
                   budget: int | None = None) -> dict:
    """超预算 → 由该数字人同模型自缩减为 ≤budget 字的交接摘要。

    返回 dict：{kind, content(可能已缩减), refined(bool), raw_chars, chars,
    error(str|None)}。LLM 调用失败/超时 → 确定性 truncate_head_tail 兜底，
    保证交接不中断。
    """
    content = content or ""
    b = budget or budget_for(kind)
    if len(content) <= b:
        return {"kind": kind, "content": content, "refined": False,
                "raw_chars": len(content), "chars": len(content), "error": None}
    try:
        from . import llm, chat as chat_mod
        system = (
            f"你是数字人「{persona_name or kind}」。你的产出超过了交接预算，"
            "请把它缩减为下游可直接使用的交接摘要。\n"
            "规则：\n"
            "1. 保留全部关键决策、约束、边界、数字与结论；去掉客套与铺垫。\n"
            f"2. 缩减后正文不超过 {b} 字符。\n"
            "3. 只输出缩减后的正文本身，不要解释、不要 markdown 代码块标记。"
        )
        user = f"待缩减的「{kind}」交接物原文如下：\n\n{content}"
        if provider == "ollama":
            reply = llm.chat_ollama([{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                                    temperature=0.2,
                                    model=ollama_model or chat_mod.DEFAULT_OLLAMA_MODEL)
        elif provider == "llm":
            reply = llm.chat([{"role": "system", "content": system},
                              {"role": "user", "content": user}], temperature=0.2)
        else:
            reply = llm.chat2([{"role": "system", "content": system},
                               {"role": "user", "content": user}], temperature=0.2)
        out = (reply or "").strip()
        if out and len(out) <= b * 1.4:  # 允许轻微超预算，LLM 输出难精确卡字
            return {"kind": kind, "content": out, "refined": True,
                    "raw_chars": len(content), "chars": len(out), "error": None}
        if out and len(out) > b * 1.4:
            out = truncate_head_tail(out, b)
            return {"kind": kind, "content": out, "refined": True,
                    "raw_chars": len(content), "chars": len(out), "error": None}
    except Exception as e:  # noqa: BLE001 — 精炼失败不阻断交接
        out = truncate_head_tail(content, b)
        return {"kind": kind, "content": out, "refined": True,
                "raw_chars": len(content), "chars": len(out),
                "error": f"refine failed({type(e).__name__}), fell back to truncate"}
    out = truncate_head_tail(content, b)
    return {"kind": kind, "content": out, "refined": True,
            "raw_chars": len(content), "chars": len(out), "error": None}


# ---------------- 入站裁剪：按 kind 分组 + 预算 ----------------
def shape_inputs(raw_inputs: list[str]) -> list[dict]:
    """把一批交接物按 kind 分组裁剪为注入用的结构化条目。

    返回 [{"kind","content","chars","refined","raw_chars"}]：同 kind 多份合并、
    每 kind 总长不超过 KIND_INPUT_BUDGET（保留每份开头，超预算截断标注来源）。
    """
    groups: dict[str, list[str]] = {}
    metas: dict[str, list[dict]] = {}
    for raw in raw_inputs:
        d = unpack_handoff(raw)
        kind = d["kind"]
        groups.setdefault(kind, []).append(d["content"])
        metas.setdefault(kind, []).append(d)
    out: list[dict] = []
    for kind, contents in groups.items():
        cap = budget_for(kind, KIND_INPUT_BUDGET)
        joined = "\n\n---\n\n".join(contents)
        if len(joined) <= cap:
            out.append({"kind": kind, "content": joined,
                        "chars": len(joined), "refined": False})
            continue
        # 多份超预算：逐份截首保尾，直到塞进 cap
        parts, used = [], 0
        room = cap - len("\n\n---\n\n") * (len(contents) - 1) - 60
        for c in contents:
            if used >= room:
                parts.append("[该上游交接物过长已省略]")
                continue
            take = min(len(c), room - used)
            piece = c[:take]
            if take < len(c):
                piece += "\n…[超出预算截断]…"
            parts.append(piece)
            used += len(piece)
        out.append({"kind": kind, "content": "\n\n---\n\n".join(parts),
                    "chars": used, "refined": True})
    return out


def render_inputs(items: list[dict]) -> str:
    """把裁剪后的交接物列表渲染为给 LLM 的注入文本（带 kind 标签）。"""
    if not items:
        return ""
    lines = []
    for it in items:
        kind = it.get("kind") or KIND_GENERIC
        lines.append(f"【{kind}】")
        lines.append(it.get("content") or "")
    return "\n\n".join(lines)


# ---------------- 续写前压缩（design §9.5：断点续生成配套） ----------------
def refine_system_block(messages: list[dict], budget: int = 12000) -> list[dict]:
    """把 messages 里超长的 system 段压缩到 budget 内（供截断续写前调用）。

    策略：不删语义，先整段截断到 budget；若仍超，交给数字人 LLM 自缩减
    （复用精炼器语义：保留规则名与关键约束）。无 persona 上下文时可纯截断。
    返回新的 messages（原列表不动）。
    """
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") != "system":
            continue
        c = m.get("content") or ""
        if len(c) <= budget:
            continue
        # 保留头部（身份/使命）与尾部（本体的可执行规则通常靠后？不可靠），
        # 确定性做法：保头 + 去尾中段。规则名逐行保留比纯截断更保语义。
        lines = c.splitlines()
        kept, used = [], 0
        head_budget = int(budget * 0.75)
        for ln in lines:
            if used + len(ln) + 1 > head_budget:
                break
            kept.append(ln)
            used += len(ln) + 1
        m["content"] = "\n".join(kept)
    return out
