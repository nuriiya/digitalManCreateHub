# -*- coding: utf-8 -*-
"""LLM 调用：段 summary、打标签、聚合整篇 summary。

正式模式：OpenAI 兼容接口。
本地模式（无 key）：规则降级——摘要取段落首句，标签用关键词匹配，
保证无 LLM 也能跑通全链路。
"""
import json
import re
import config


def _llm_chat(messages: list[dict], temperature: float = 0.2) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("正式模式需要 openai 库")
    client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    resp = client.chat.completions.create(
        model=config.LLM_MODEL, messages=messages, temperature=temperature)
    return resp.choices[0].message.content


# 本地降级用的关键词 → 标签映射（演示用，可按部门扩展）
_KEYWORD_TAGS = [
    ("财务|报销|预算|发票|付款|账", "财务"),
    ("人力|考勤|福利|绩效|招聘|员工", "HR"),
    ("系统|服务器|网络|VPN|权限|故障|IT", "IT"),
    ("合规|安全|隐私|权限|审计|法务|风险", "合规"),
    ("需求|功能|流程|审批", "流程"),
]


def _rule_summary(text: str) -> str:
    """无 LLM 时：取段落前若干句作为摘要。"""
    sents = re.split(r'(?<=[。！？!?；;])', text)
    sents = [s.strip() for s in sents if s.strip()]
    return "".join(sents[:2]) if sents else text[:80]


def _rule_tags(text: str) -> list[str]:
    """无 LLM 时：关键词匹配打标签。"""
    tags = []
    for pattern, tag in _KEYWORD_TAGS:
        if re.search(pattern, text) and tag not in tags:
            tags.append(tag)
    return tags or ["通用"]


def summarize_chunk(text: str) -> dict:
    """给一段文本生成 summary + 多标签。返回 {summary, tags}"""
    if config.LLM_API_KEY:
        try:
            prompt = (
                "你是一个文档摘要助手。请总结下面这段文字的核心要点，"
                "并给出 2-5 个分类标签。\n"
                "严格按 JSON 输出，格式：{\"summary\": \"...\", \"tags\": [\"...\", ...]}\n\n"
                f"原文：\n{text}"
            )
            out = _llm_chat([{"role": "user", "content": prompt}])
            data = _extract_json(out)
            if data and data.get("summary"):
                return {"summary": data["summary"], "tags": data.get("tags", [])}
        except Exception:
            pass
    return {"summary": _rule_summary(text), "tags": _rule_tags(text)}


def summarize_document(chunk_summaries: list[str]) -> str:
    """把多个段 summary 聚合成整篇 summary。"""
    joined = "\n".join(f"{i+1}. {s}" for i, s in enumerate(chunk_summaries))
    if config.LLM_API_KEY:
        try:
            prompt = (
                "下面是一篇文章各段的摘要。请综合成一段整篇文章的摘要，"
                "覆盖全文主题与要点，200 字以内。直接输出摘要文本。\n\n" + joined
            )
            out = _llm_chat([{"role": "user", "content": prompt}])
            if out and out.strip():
                return out.strip()
        except Exception:
            pass
    # 降级：拼接各段摘要前句
    return "；".join(s.split("。")[0] for s in chunk_summaries if s)[:300]


def _extract_json(text: str) -> dict:
    """从 LLM 输出里提取 JSON 对象（容错）。"""
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
    return {}
