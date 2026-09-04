# -*- coding: utf-8 -*-
"""LLM access: V4-Flash via OpenAI-compatible endpoint (DM1) as the
*nominator/generator*; GLM 5.2 (llm2) as the *judge* (open-book discriminator).

Rule fallback when unconfigured/unreachable keeps the whole pipeline runnable
offline - LLM is a *nominator*, never a final judge (iron law 3).

Judge channel (chat2) has NO rule fallback by design: the exam pipeline
refuses to run without a configured discriminator (异源交叉核验), and callers
must surface LLMError instead of silently self-judging with the same model
(同源偏差 - the model rationalizes its own output).
"""
import json
import re
import threading

from . import netutil, settings_store

# hook for UT: tests inject a fake callable (messages -> str)
_fake_chat = None
_fake_chat2 = None
_fake_chat_ollama = None


def set_fake_chat(fn) -> None:
    global _fake_chat
    _fake_chat = fn


def set_fake_chat2(fn) -> None:
    """Fake for the judge channel (llm2). Independent from the generator
    channel so UT can simulate 异源 models separately."""
    global _fake_chat2
    _fake_chat2 = fn


def set_fake_chat_ollama(fn) -> None:
    """Fake for the local Ollama channel (hallucination A/B baseline)."""
    global _fake_chat_ollama
    _fake_chat_ollama = fn


def clear_fake_chat() -> None:
    global _fake_chat, _fake_chat2, _fake_chat_ollama
    _fake_chat = None
    _fake_chat2 = None
    _fake_chat_ollama = None


class LLMError(Exception):
    """LLM is configured but the call failed (network / auth / quota).

    Callers must NOT silently fall back to rule mode on this - surface it
    (iron law 2: say you don't know). Unconfigured LLM is a different case:
    offline rule mode is the intended path there."""


class RateLimited(LLMError):
    """HTTP 429 from the provider. Transient by nature: the concurrent
    scheduler steps its in-flight window DOWN by 1 and re-queues the chunk
    instead of pausing the whole job (only a hard limit / persistent quota
    error pauses)."""


def llm_configured() -> bool:
    s = settings_store.load_settings()["llm"]
    return bool(s.get("base_url") and s.get("api_key"))


def llm2_configured() -> bool:
    s = settings_store.load_settings()["llm2"]
    return bool(s.get("base_url") and s.get("api_key"))


def chat(messages: list[dict], temperature: float = 0.2,
         usage_out: dict | None = None) -> str:
    """One chat completion on the generator channel (llm). Raises LLMError
    on failure (caller decides fallback).

    `usage_out` (optional dict) receives the provider's exact token usage when
    the endpoint reports it (prompt/completion/total tokens) — used by the
    persona chat page for the context-window meter.

    Real calls emit an `llm.call` event (prompt/reply preview) so the task
    detail panel can show exactly what was sent to the model."""
    s = settings_store.load_settings()["llm"]
    _notify({"model": s["model"], "prompt_len": sum(len(m.get("content") or "")
                                                   for m in messages),
             "prompt_preview": _preview(messages[-1].get("content") or "", 600)})
    try:
        if _fake_chat is not None:
            reply = _fake_chat(messages)
        else:
            reply, usage = _call_llm_with_usage(
                s, messages, temperature,
                float(s.get("timeout", 90)), float(s.get("hard_timeout", 1800)))
            if usage_out is not None:
                usage_out.update(usage or {})
    except LLMError:
        raise
    except Exception as e:
        # HTTP 429 = transient throttle: keep its distinct type so the
        # concurrent scheduler can back off instead of pausing the job
        if _is_ratelimited(e):
            raise RateLimited(f"{type(e).__name__}: {str(e)[:200]}") from e
        # LLM failure must never be silent (iron law 2: say you don't know)
        _notify({"model": s["model"], "type": "llm.error",
                 "error": f"{type(e).__name__}: {str(e)[:200]}"})
        raise LLMError(f"{type(e).__name__}: {str(e)[:200]}") from e
    _notify({"model": s["model"], "reply_preview": _preview(reply, 400),
             "type": "llm.reply"})
    return reply


def chat2(messages: list[dict], temperature: float = 0.0) -> str:
    """One chat completion on the judge channel (llm2 = GLM 5.2).

    Judge = open-book cross-verification: the verdict is a *nomination* that
    deterministic code turns into the final decision. No rule fallback here:
    if the judge is unreachable the caller pauses the job (iron law 2)."""
    return _chat_llm2(messages, temperature, "judge")


def chat_persona(messages: list[dict], temperature: float = 0.5,
                 usage_out: dict | None = None) -> str:
    """Persona chat responder on llm2 (GLM 5.2), grounded by the persona's
    ontology constraint injected as a system prompt by the caller.

    No rule fallback: GLM unreachable -> LLMError surfaced to the caller
    (iron law 2: the persona says it doesn't know rather than hallucinating)."""
    return _chat_llm2(messages, temperature, "chat", usage_out)


def chat_ollama(messages: list[dict], temperature: float = 0.5,
                model: str | None = None, usage_out: dict | None = None) -> str:
    """Persona chat responder on a local Ollama LLM (e.g. qwen2.5:7b).

    Ollama exposes an OpenAI-compatible endpoint at {base}/v1, so we reuse
    the same streaming call with a dummy key (Ollama ignores auth). This is
    the A/B baseline for hallucination comparison against GLM 5.2 / V4-Flash.

    No rule fallback: unreachable -> LLMError (iron law 2)."""
    s = settings_store.load_settings()["embedding"]
    base = (s.get("base_url") or "http://localhost:11434").rstrip("/")
    ollama = {
        "base_url": base + "/v1",
        "api_key": "ollama",  # Ollama does not authenticate
        # default responder is the 32k variant (num_ctx pinned to the model max);
        # bare qwen2.5:7b defaults to num_ctx 2048 and silently truncates.
        "model": model or "qwen2.5:7b-32k",
        "timeout": 300,          # local 7B is slower; generous idle tier
        "hard_timeout": 1800,
    }
    _notify({"model": ollama["model"], "channel": "ollama",
             "prompt_len": sum(len(m.get("content") or "") for m in messages),
             "prompt_preview": _preview(messages[-1].get("content") or "", 600)})
    try:
        if _fake_chat_ollama is not None:
            reply = _fake_chat_ollama(messages)
        else:
            reply, usage = _call_llm_with_usage(ollama, messages, temperature,
                                                300.0, 1800.0)
            if usage_out is not None:
                usage_out.update(usage or {})
    except LLMError:
        raise
    except Exception as e:
        if _is_ratelimited(e):
            raise RateLimited(f"{type(e).__name__}: {str(e)[:200]}") from e
        _notify({"model": ollama["model"], "channel": "ollama",
                 "type": "llm.error", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        raise LLMError(f"{type(e).__name__}: {str(e)[:200]}") from e
    _notify({"model": ollama["model"], "channel": "ollama",
             "reply_preview": _preview(reply, 400), "type": "llm.reply"})
    return reply


def _chat_llm2(messages: list[dict], temperature: float, channel: str,
               usage_out: dict | None = None) -> str:
    """Shared llm2 (GLM 5.2) call. `channel` is a telemetry label (judge|chat)."""
    s = settings_store.load_settings()["llm2"]
    _notify({"model": s["model"], "channel": channel,
             "prompt_len": sum(len(m.get("content") or "") for m in messages),
             "prompt_preview": _preview(messages[-1].get("content") or "", 600)})
    # GLM 5.2 defaults to thinking on; the judge channel (verdict + failure
    # attribution) only NOMINATES and deterministic code adjudicates, so the
    # reasoning chain is pure latency (its tokens stream as reasoning_content
    # and are discarded here) and can hang for 10+ minutes or get the
    # connection dropped by the provider. Disable it on judge only; chat keeps
    # thinking (GLM's long-context agentic strength).
    extra_body = {"thinking": {"type": "disabled"}} if channel == "judge" else None
    try:
        if _fake_chat2 is not None:
            reply = _fake_chat2(messages)
        else:
            reply, usage = _call_llm_with_usage(
                s, messages, temperature,
                float(s.get("timeout", 90)), float(s.get("hard_timeout", 1800)),
                extra_body=extra_body)
            if usage_out is not None:
                usage_out.update(usage or {})
    except LLMError:
        raise
    except Exception as e:
        if _is_ratelimited(e):
            raise RateLimited(f"{type(e).__name__}: {str(e)[:200]}") from e
        _notify({"model": s["model"], "channel": channel, "type": "llm.error",
                 "error": f"{type(e).__name__}: {str(e)[:200]}"})
        raise LLMError(f"{type(e).__name__}: {str(e)[:200]}") from e
    _notify({"model": s["model"], "channel": channel,
             "reply_preview": _preview(reply, 400), "type": "llm.reply"})
    return reply


def _call_llm_with_usage(s: dict, messages: list[dict], temperature: float,
                         idle_seconds: float, hard_seconds: float,
                         extra_body: dict | None = None) -> tuple[str, dict | None]:
    """Streaming OpenAI call with a two-tier timeout.

    - idle tier (SDK read timeout): trips when the upstream sends NO bytes
      for `idle_seconds` straight. For a streaming endpoint the read timeout
      measures the *gap between incoming chunks*, which is exactly the
      "is it still alive?" signal - a slow-but-healthy model that keeps
      dripping tokens is NEVER killed by this tier.
    - hard tier (daemon-thread ceiling): total wall-clock cap (default
      30min) against pathological cases (endless thinking loop, upstream
      that keeps the socket busy but never finishes). The abandoned thread
      dies with the process.

    Streaming also means a huge extraction reply (long JSON) can take as
    long as it needs as long as tokens keep arriving - no more 180s
    "LLM was still writing" false positives."""
    box: dict = {}

    def run() -> None:
        try:
            from openai import OpenAI
            # loopback endpoints (Ollama) must NOT go through any proxy:
            # httpx defaults to trust_env=True, which sends localhost through
            # the system proxy -> 502 (killed benchmark job #30).
            kwargs_client = {}
            if netutil.is_local_url(s["base_url"]):
                import httpx
                kwargs_client["http_client"] = httpx.Client(trust_env=False)
            client = OpenAI(base_url=s["base_url"], api_key=s["api_key"],
                            timeout=idle_seconds, max_retries=0,
                            **kwargs_client)
            kwargs = dict(model=s["model"], messages=messages, temperature=temperature,
                          stream=True, stream_options={"include_usage": True})
            if extra_body:
                kwargs["extra_body"] = extra_body
            resp = client.chat.completions.create(**kwargs)
            parts: list[str] = []
            for chunk in resp:
                usage = getattr(chunk, "usage", None)
                if usage is not None:  # usage chunk (include_usage) -> exact tokens
                    box["usage"] = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", None),
                        "completion_tokens": getattr(usage, "completion_tokens", None),
                        "total_tokens": getattr(usage, "total_tokens", None),
                    }
                    continue
                if not chunk.choices:  # usage-only sentinel chunk
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    parts.append(delta.content)
            box["reply"] = "".join(parts)
        except Exception as e:  # surfaced below
            box["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(hard_seconds)
    if t.is_alive():
        raise LLMError(
            f"LLM 调用总时长超过 {hard_seconds:.0f}s 上限（持续无回复或思考过久）。"
            "可在设置页调大「总时长上限」。")
    if "error" in box:
        e = box["error"]
        name = type(e).__name__
        if _is_ratelimited(e):
            raise RateLimited(f"{name}: {str(e)[:200]}") from e
        if name in ("ReadTimeout", "ConnectTimeout", "ReadError"):
            raise LLMError(
                f"LLM {idle_seconds:.0f}s 无新输出（空闲超时），连接可能已中断。"
                "可在设置页调大「空闲超时」。") from e
        raise LLMError(f"{name}: {str(e)[:200]}") from e
    return box["reply"], box.get("usage")


def _call_llm(s: dict, messages: list[dict], temperature: float,
              idle_seconds: float, hard_seconds: float) -> str:
    """Backward-compatible wrapper: returns only the reply text (usage dropped)."""
    reply, _ = _call_llm_with_usage(s, messages, temperature,
                                    idle_seconds, hard_seconds)
    return reply


def _is_ratelimited(e: Exception) -> bool:
    """429 detection across OpenAI SDK error shapes (status_code attr, class
    name, or message text) - the provider throttles us transiently."""
    return (getattr(e, "status_code", None) == 429
            or type(e).__name__ == "RateLimitError"
            or "429" in str(e))


def _preview(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit}字)"


def _notify(fields: dict) -> None:
    """Emit an llm.* event for the job running on this thread (best effort).

    Concurrent-extraction worker threads run in buffer mode: their telemetry
    goes to an in-memory queue that the scheduler thread drains - workers
    never touch the shared sqlite connection from their own thread."""
    from . import db, jobs
    job_id = jobs.current_job_id()
    if job_id is None:
        return
    type_ = fields.pop("type", "llm.call")
    if jobs.telemetry_buffered():
        jobs.buffered_telemetry(job_id, type_, fields)
        return
    try:
        jobs.emit(db.get_conn(), job_id, type_, fields)
    except Exception:
        pass  # telemetry must never break the pipeline


def extract_json(text: str) -> dict | list | None:
    """Tolerant JSON extraction from LLM output."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}|\[.*\]', text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# ---------------- rule fallback (no LLM) ----------------

_KEYWORD_TAGS = [
    (r"财务|报销|预算|发票|付款|账", "财务"),
    (r"人力|考勤|福利|绩效|招聘|员工", "HR"),
    (r"系统|服务器|网络|VPN|权限|故障", "IT"),
    (r"合规|安全|隐私|审计|法务|风险", "合规"),
    (r"需求|功能|流程|审批", "流程"),
]


def rule_summary(text: str) -> str:
    sents = re.split(r'(?<=[。！？!?；;])', text)
    sents = [s.strip() for s in sents if s.strip()]
    return "".join(sents[:2]) if sents else text[:80]


def rule_tags(text: str) -> list[str]:
    tags = []
    for pattern, tag in _KEYWORD_TAGS:
        if re.search(pattern, text) and tag not in tags:
            tags.append(tag)
    return tags or ["通用"]


def summarize_chunk(text: str) -> dict:
    """summary + tags for one chunk. Flash if configured, rule fallback.

    Raises LLMError when the LLM is configured but unreachable (network
    blip / auth) - the caller auto-pauses the job instead of silently
    degrading to rule quality. Unusable replies (bad JSON) still fall
    back - the model answered, the answer was just malformed."""
    if llm_configured():
        try:
            prompt = (
                "你是文档摘要助手。总结这段文字的核心要点，并给出2-5个分类标签。\n"
                '严格按 JSON 输出：{"summary": "...", "tags": ["..."]}\n\n原文：\n' + text)
            data = extract_json(chat([{"role": "user", "content": prompt}]))
            if isinstance(data, dict) and data.get("summary"):
                return {"summary": str(data["summary"]),
                        "tags": [str(t) for t in data.get("tags", [])][:5]}
        except LLMError:
            raise
        except Exception:
            pass
    return {"summary": rule_summary(text), "tags": rule_tags(text)}


def summarize_document(chunk_summaries: list[str]) -> str:
    joined = "\n".join(f"{i+1}. {s}" for i, s in enumerate(chunk_summaries))
    if llm_configured():
        try:
            prompt = ("下面是一篇文章各段的摘要，请综合成整篇摘要，200字以内，"
                      "直接输出摘要文本。\n\n" + joined)
            out = chat([{"role": "user", "content": prompt}])
            if out and out.strip():
                return out.strip()
        except LLMError:
            raise
        except Exception:
            pass
    return "；".join(s.split("。")[0] for s in chunk_summaries if s)[:300]
