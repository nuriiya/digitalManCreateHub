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
import os
import re
import threading
import time

from . import netutil, settings_store


def _http_client_kwargs(base_url: str) -> dict:
    """Build the httpx client kwargs for the OpenAI SDK.

    - Loopback (Ollama) -> trust_env=False (bypass the Windows system proxy,
      which would 502 loopback requests — killed benchmark job #30).
    - Remote + LLM_PROXY set -> route through LLM_PROXY explicitly (CN network
      blocks direct access to api.deepseek.com / open.bigmodel.cn; inside the
      docker container the host's mihomo proxy is reachable at
      host.docker.internal:6789 and passed via the LLM_PROXY env var).
    - Remote + no LLM_PROXY -> trust_env=True (honour the host's env proxy).
    """
    if netutil.is_local_url(base_url):
        import httpx
        return {"http_client": httpx.Client(trust_env=False)}
    proxy = os.environ.get("LLM_PROXY", "").strip()
    if proxy:
        import httpx
        return {"http_client": httpx.Client(proxy=proxy, trust_env=False)}
    return {}


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
        # default responder is the CPU-friendly 4k variant (qwen2.5:7b-cpu);
        # pure-CPU hosts (no NVIDIA GPU) cannot afford the 32k prefill on
        # every chat. Benchmark / long-context jobs should pass an explicit
        # 32k model instead of relying on the default.
        "model": model or "qwen2.5:7b-cpu",
        "timeout": 300,          # local 7B is slower; generous idle tier
        "hard_timeout": 1800,
        # keep_alive prevents Ollama's 5-min idle unload (which would cost
        # ~60s of cold-start reload on the next chat). num_ctx must come
        # from the model's Modelfile — Ollama's OpenAI-compat endpoint
        # does not honour per-request num_ctx.
        "keep_alive": "10m",
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


# ---------------- 截断续生成（design §9.5） ----------------
# 检测"回复被中途截断"（finish_reason=length / 未闭合符号结尾），自动续写拼接。
# 背景：qwen2.5:7b 在长上下文修正时输出会中途坍缩成 61~73 字符空壳（引号/三引号
# 未闭合即 SyntaxError）。V4-Flash 无此现象，但任何模型在极长输出/限流下都可能被
# 服务端 max_tokens 截断——本机制把它从"整题失败"救成"续写完整"。

_UNCLOSED = ('"""', "'''", "\\")
MAX_CONTINUE = 3          # 单次生成内最多续写次数
ANCHOR_TAIL = 800         # 续写锚点：保留已生成内容尾部字符数


def _looks_truncated(reply: str, finish_reason: str | None = None) -> bool:
    """截断判定：finish_reason==length（权威）+ 空回复 + 强信号启发式。

    纯代码判定，不靠 LLM 自评。启发式只报"几乎必然是截断"的信号——
      - 以未闭合的三引号 `\"\"\"`/`'''` 结尾：代码 docstring/多行字符串被硬切断
      - 以反斜杠 `\\` 结尾：转义序列被硬切断
      - 空回复：生成中断
    普通单引号/括号结尾（代码里完全正常）不判截断，避免误触发续写。
    """
    if finish_reason == "length":
        return True
    tail = (reply or "").rstrip()
    if not tail:
        return True
    for sym in _UNCLOSED:
        if tail.endswith(sym):
            return True
    return False


def _unclosed_tail_hint(reply: str) -> bool:
    """返回是否疑似截断（供上层记 trace）。"""
    return _looks_truncated(reply)


def _continue_request(reply: str, anchor_chars: int = ANCHOR_TAIL) -> list[dict]:
    """续写请求消息：给模型它自己已写内容的尾部作锚点，要求接着写完整。

    不重发原始任务 prompt —— 避免模型从头重写（丢已写结构）或重复。同一会话
    消息栈 append（assistant 已写 + 续写指令），模型能看到自己上文。
    """
    anchor = reply[-anchor_chars:] if len(reply) > anchor_chars else reply
    return [{"role": "user", "content":
             f"你上一条输出在末尾被截断了。下面是你已写内容的末尾：\n"
             f"```\n{anchor}\n```\n"
             "请从该点**继续**把内容写完整（不要重复锚点及更早的内容，"
             "不要解释，直接续写剩余部分，保持原有格式）。"}]


def chat_with_continuation(messages: list[dict], temperature: float = 0.2,
                           channel: str = "llm",
                           ollama_model: str | None = None) -> tuple[str, dict]:
    """生成 + 截断续写：正常调用 → 若 _looks_truncated 则循环续写拼接。

    channel: "llm"(V4-Flash) / "llm2"(GLM) / "ollama"(qwen 等)。
    返回 (完整回复, {"truncated": bool, "continues": int})。
    """
    if channel == "llm2":
        base = chat2
    elif channel == "ollama":
        base = lambda msgs, temp: chat_ollama(msgs, temperature=temp,  # noqa: E731
                                              model=ollama_model)
    else:
        base = chat
    reply = base(messages, temperature) or ""
    truncated = _looks_truncated(reply)
    continues = 0
    while truncated and continues < MAX_CONTINUE:
        # 续写：在原消息栈上 append 已写内容 + 续写指令（模型看得到自己上文）
        stack = [dict(m) for m in messages]
        stack.append({"role": "assistant", "content": reply})
        stack.extend(_continue_request(reply))
        part = base(stack, temperature) or ""
        if not part.strip():          # 续写为空 → 放弃，保留已有
            break
        reply += part                 # 拼接，而不是重写
        continues += 1
        truncated = _looks_truncated(part)
    return reply, {"truncated": truncated, "continues": continues}


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
            # loopback (Ollama) must NOT go through any proxy; remote LLM
            # endpoints route through LLM_PROXY when set (see helper docstring).
            kwargs_client = _http_client_kwargs(s["base_url"])
            client = OpenAI(base_url=s["base_url"], api_key=s["api_key"],
                            timeout=idle_seconds, max_retries=0,
                            **kwargs_client)
            kwargs = dict(model=s["model"], messages=messages, temperature=temperature,
                          stream=True, stream_options={"include_usage": True})
            # Ollama-specific knobs. NOTE: Ollama's OpenAI-compat endpoint
            # only honours `keep_alive` and `format` as top-level body fields
            # (num_ctx is NOT accepted per-request — it must be set in the
            # Modelfile at model build time). extra_body is the SDK's
            # blessed mechanism for smuggling non-OpenAI fields into the
            # request body without tripping the SDK's argument validator.
            # CPU-only hosts (no NVIDIA GPU) cannot afford the 32k default
            # prefill on every chat — see ollama/qwen2.5:7b-cpu model variant.
            extra = dict(extra_body or {})
            keep_alive = s.get("keep_alive")
            if keep_alive is not None:
                extra["keep_alive"] = keep_alive
            if extra:
                kwargs["extra_body"] = extra
            # Connection-level retries: APIConnectionError (proxy / NAT blip,
            # TLS EOF under transient CN egress) is recoverable — retry with
            # a short backoff instead of failing the whole job. Rate limits
            # are NOT retried here (handled upstream with proper backoff).
            last_err: Exception | None = None
            resp = None
            for attempt in range(1, 4):
                try:
                    resp = client.chat.completions.create(**kwargs)
                    break
                except Exception as e:  # noqa: BLE001
                    ename = type(e).__name__
                    if ename in ("APIConnectionError", "ConnectError",
                                 "ReadTimeout", "ConnectTimeout") and attempt < 3:
                        last_err = e
                        time.sleep(1.0 * attempt)  # 1s / 2s backoff
                        continue
                    raise
            if last_err is not None and resp is None:
                raise last_err
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


# ---------------- streaming generators (chat page 流式 token 输出) ----------------

def _iter_openai(s: dict, messages: list[dict], temperature: float,
                 usage_out: dict | None = None,
                 extra_body: dict | None = None):
    """Generator yielding `content` deltas from a streaming OpenAI-compatible
    chat completion.

    The wrapper layer (`iter_chat` / `iter_chat_persona` / `iter_chat_ollama`)
    assembles the right channel config. The two-tier timeout (idle / hard) is
    enforced by the caller if needed (see `_call_llm_with_usage` for the
    non-streaming counterpart).
    """
    kwargs_client = _http_client_kwargs(s["base_url"])
    from openai import OpenAI
    client = OpenAI(base_url=s["base_url"], api_key=s["api_key"],
                    timeout=float(s.get("timeout", 90)), max_retries=0,
                    **kwargs_client)
    kwargs = dict(model=s["model"], messages=messages, temperature=temperature,
                  stream=True, stream_options={"include_usage": True})
    extra = dict(extra_body or {})
    keep_alive = s.get("keep_alive")
    if keep_alive is not None:
        extra["keep_alive"] = keep_alive
    if extra:
        kwargs["extra_body"] = extra
    resp = client.chat.completions.create(**kwargs)
    for chunk in resp:
        usage = getattr(chunk, "usage", None)
        if usage is not None:  # usage-only sentinel chunk (include_usage=True)
            if usage_out is not None:
                usage_out.update({
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                    "total_tokens": getattr(usage, "total_tokens", None),
                })
            continue
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def iter_chat(messages: list[dict], temperature: float = 0.5,
              usage_out: dict | None = None):
    """Streaming chat on the generator channel (llm). Yields content deltas."""
    s = settings_store.load_settings()["llm"]
    yield from _iter_openai(s, messages, temperature, usage_out=usage_out)


def iter_chat_persona(messages: list[dict], temperature: float = 0.5,
                      usage_out: dict | None = None):
    """Streaming chat on the persona responder channel (llm2 = GLM 5.2).
    Thinking stays ON (chat channel keeps GLM's long-context strength)."""
    s = settings_store.load_settings()["llm2"]
    yield from _iter_openai(s, messages, temperature, usage_out=usage_out)


def iter_chat_ollama(messages: list[dict], temperature: float = 0.5,
                     model: str | None = None,
                     usage_out: dict | None = None):
    """Streaming chat on the local Ollama 7B baseline."""
    s = settings_store.load_settings()["embedding"]
    base = (s.get("base_url") or "http://localhost:11434").rstrip("/")
    ollama = {
        "base_url": base + "/v1",
        "api_key": "ollama",
        "model": model or "qwen2.5:7b-cpu",
        "timeout": 300,
        "hard_timeout": 1800,
        "keep_alive": "10m",
    }
    yield from _iter_openai(ollama, messages, temperature,
                            usage_out=usage_out)


def stream_pick(provider: str, messages: list[dict], ollama_model: str | None,
                usage_out: dict | None = None):
    """Pick the right iter_* generator by channel. Echoes `_dispatch`."""
    if provider == "ollama":
        return iter_chat_ollama(messages, temperature=0.5, model=ollama_model,
                                usage_out=usage_out)
    if provider == "llm":
        return iter_chat(messages, temperature=0.5, usage_out=usage_out)
    return iter_chat_persona(messages, temperature=0.5, usage_out=usage_out)


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
