# -*- coding: utf-8 -*-
"""数字人对话 (persona chat): GLM 5.2 + 本体约束 + 动态检索窗口.

The digital person answers ONLY from its ontology constraint set:
  - identity (name / mission / description)
  - approved anchors (the persona's core focus)
  - the assembled persona_ontology 段 (its actual knowledge)
  - relations between those entities (how its concepts connect)

本体注入不再是「平铺截断前 N 个 + 独立 LIMIT 60 关系」，而是**动态检索窗口**：
  1. 对用户消息（+ 最近历史）做字面匹配打分（0 LLM，确定性），找出相关本体；
  2. 沿关系图做 1 跳扩展，把相关本体的邻居也纳入候选；
  3. 在「上下文窗口 × ONTOLOGY_BUDGET_RATIO」的 token 预算内按优先级填充——
     锚点(固定) → 命中本体 → 命中关系 → 扩展本体 → 扩展关系；
  4. 关系只注入「已选本体作为 source」的三元组，且本体↔本体优先于本体→文本。

GLM 5.2 是 *responder*；确定性代码负责检索 + 组装，模型只看到封闭的本体约束。

Iron laws:
  - GLM generates text; the ontology block is the ground truth it must obey;
  - if the question falls outside the ontology, the persona says it doesn't
    know rather than hallucinating (iron law 2);
  - no rule fallback on the chat channel: GLM unreachable -> LLMError.
  - the user's `ident.prompt` is an ADDITIVE system-instruction tail; iron
    laws and the ontology block are hard-coded *before* the user tail and
    cannot be overridden by it (tail is wrapped so the persona sees it as
    guidance, never as a rule).
"""
import json
import re
from collections import defaultdict

from . import db, llm, netutil
from .jsonb import maybe_jsonb

HISTORY_LIMIT = 20     # recent messages fed back as conversation context

# 本体 + 关系 的注入预算 = 上下文窗口的固定比例（动态检索窗口）。其余 80% 留给
# 历史对话与模型回复。锚点固定全放；本体/关系按 query 相关性检索后在此预算内填充。
ONTOLOGY_BUDGET_RATIO = 0.20

# 空命中兜底的「中心本体」规模：query 未命中任何本体时，取图内度数最高的前 K 个
# 枢纽实体作为种子（而非平铺全部 1462 个），再沿关系图 1 跳扩展。
FALLBACK_CENTER_LIMIT = 20

# ---- 检索窗口升级（#106 概念抽取兜底 + #107 自适应扩展深度）----
# 字面匹配 0 命中时，V4-Flash 从用户消息抽 3-8 个检索概念词（LLM 只出概念，
# 完全不接触实体选择权——铁律「LLM 无终审权」在此比整表提名更干净），
# 确定性代码再拿概念 ⋈ 本体名/定义做双向包含匹配挑出种子。
CONCEPT_SEED_LIMIT = 10        # 概念命中的种子上限
# L3/L4 扩展从「固定 1 跳」改为预算驱动的逐跳扩展：预算富余才进下一跳，
# 32k 窗口通常 1 跳填满（与旧行为一致），1M 窗口 2 跳扩大覆盖。
MAX_EXPAND_DEPTH = 2           # 扩展跳数上限
MAX_INJECTED_ONTOLOGY = 400    # 注入本体总数护栏（防 1M 窗口 2 跳拉进半图谱稀释注意力）
EXPAND_RESERVE_RATIO = 0.15    # 预算剩余低于此比例不再进下一跳

# context-window sizes (tokens) for the "上下文窗口占比" meter.
# llm/llm2 are 1M-context base models (deployment spec). Ollama's *runtime*
# window is its num_ctx (default 2048 — Ollama silently truncates beyond it),
# NOT the model's native context_length (qwen2.5:7b = 32768); see
# _ollama_context_length(). The meter uses the runtime window so the user can
# see when a 7B baseline is actually being truncated mid-experiment.
CONTEXT_WINDOWS = {"llm": 1_000_000, "llm2": 1_000_000}
OLLAMA_NUM_CTX_DEFAULT = 2048
# default local 7B responder: a Modelfile variant with `PARAMETER num_ctx 32768`
# (Ollama's default num_ctx is 2048 and silently truncates the injected
# ontology, which would corrupt the hallucination A/B baseline).
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-32k"

# chat 可选的 RAG 参考资料注入（「是否使用 RAG」开关）：对用户消息检索
# 已入库语料，把 top-K 原文片段以【参考资料】注入 system prompt。与本体约束
# 相互独立、可叠加；检索 0 LLM（仅 embedding 余弦），失败时优雅降级为
# 「资料不可用」（不注入、不中断对话）。
RAG_TOP_K = 6
RAG_SNIPPET_MAX = 500          # 单条片段截断字符数（保留证据语义）

# literal-match tokenizers for the 0-LLM relevance scorer
_RE_WORD = re.compile(r"[a-z0-9]+")                     # english tokens
_RE_KW = re.compile(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}")  # CJK 2+ / ascii 3+

# 附加指令尾部提示（iron law 紧固层）：把用户的自定义 prompt 显式包成「附加」
# 在最后一段，并强调铁律优先于附加指令，模型不得以附加指令为由绕过铁律。
PROMPT_TAIL_HEADER = (
    "\n\n【附加指令 · 用户在该数字人上额外设定】"
    "\n以下指令由用户在该数字人上额外填写，作为「辅助指引」叠加在上面的"
    "铁律之上；上面任何铁律（尤其是「不知道就说不知道」「不编造来源」「只依"
    "据本体约束回答」）依然高于本段附加指令。\n"
)

PROMPT_TAIL_FOOTER = (
    "\n【/附加指令 · 铁律优先，如与本体/铁律冲突则以铁律为准】\n"
)


# ---------------- constraint assembly (deterministic, 0 LLM) ----------------

def _identity(conn, identity_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["keywords"] = maybe_jsonb(d.get("keywords")) or []
    d["prompt"] = d.get("prompt") or ""
    return d


def _approved_anchors(conn, identity_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT name, type, definition FROM anchors"
        " WHERE identity_id=? AND status='approved' ORDER BY id",
        (identity_id,)).fetchall()]


def _persona_ontology(conn, identity_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT kind, name, definition FROM persona_ontology"
        " WHERE identity_id=? AND status='active' ORDER BY id",
        (identity_id,)).fetchall()]


def _relations(conn, identity_id: int) -> list[dict]:
    """Relations between the persona's ontology entities. persona_ontology
    carries source_candidate_id (back-pointer to the candidate pool), which is
    exactly relations.source_id — so the join is direct.

    Returns ALL relations (no LIMIT): the dynamic retrieval window decides what
    to inject, not a fixed cap."""
    return [dict(r) for r in conn.execute(
        "SELECT p.name AS source_name, rel.target_name, rel.relation_type"
        " FROM persona_ontology p"
        " JOIN relations rel ON rel.source_id = p.source_candidate_id"
        " WHERE p.identity_id=? AND p.status='active'"
        " ORDER BY p.id, rel.id", (identity_id,)).fetchall()]


def persona_context(conn, identity_id: int) -> dict | None:
    """The persona's full ontology constraint (None if identity missing)."""
    ident = _identity(conn, identity_id)
    if not ident:
        return None
    return {
        "identity": ident,
        "anchors": _approved_anchors(conn, identity_id),
        "ontology": _persona_ontology(conn, identity_id),
        "relations": _relations(conn, identity_id),
    }


def route_identity(conn, message: str) -> dict | None:
    """Deterministic auto-routing: decide which approved digital persona should
    answer this message (0 LLM — the routing is a pure string-match score, in
    line with the iron law that the LLM never adjudicates).

    Score = how many of the persona's ontology-段 entities + anchors literally
    appear in (or contain) the message. Highest score wins; None when there is
    no approved persona or nothing matched (caller then asks the user to pick).
    """
    idents = [dict(r) for r in conn.execute(
        "SELECT id, name FROM identities WHERE status='approved' ORDER BY id"
    ).fetchall()]
    if not idents:
        return None
    msg = (message or "").strip().lower()
    if not msg:
        return None
    best: dict | None = None
    for i in idents:
        score = 0
        matched: list[str] = []
        for o in _persona_ontology(conn, i["id"]):
            nm = (o.get("name") or "").strip().lower()
            if nm and (nm in msg or (len(nm) >= 3 and msg in nm)):
                score += 3
                matched.append(o["name"])
        for a in _approved_anchors(conn, i["id"]):
            nm = (a.get("name") or "").strip().lower()
            if nm and nm in msg:
                score += 2
                matched.append(a["name"])
        if score > 0 and (best is None or score > best["score"]):
            best = {"identity_id": i["id"], "identity_name": i["name"],
                    "score": score, "matched": list(dict.fromkeys(matched))}
    return best


def _fmt_anchor(a: dict) -> str:
    defn = (a.get("definition") or "").strip()
    return f"- {a['name']}（{a.get('type') or '概念'}）" + (f"：{defn}" if defn else "")


def _fmt_entity(o: dict) -> str:
    defn = (o.get("definition") or "").strip()
    return f"- {o['name']}" + (f"：{defn}" if defn else "")


def _fmt_relation(r: dict) -> str:
    return f"- {r['source_name']} --{r['relation_type']}--> {r['target_name']}"


def _system_prompt(ctx: dict, use_ontology: bool = True) -> str:
    """Assemble the system prompt from an already-retrieved context
    (ctx["ontology"] / ctx["relations"] are the *injected* subset, not the full
    set — the retrieval window has already applied the budget)."""
    ident = ctx["identity"]
    lines = [
        f"你是数字人「{ident['name']}」。",
        f"使命：{ident['mission'] or '（未填写）'}",
    ]
    if ident.get("description"):
        lines.append(f"定位：{ident['description']}")
    anchors = ctx["anchors"]
    ontology = ctx["ontology"]
    relations = ctx["relations"]
    rag = ctx.get("rag") or []
    if use_ontology and (anchors or ontology):
        lines.append("")
        lines.append("你的本体约束（你只掌握、也只能依据以下本体知识回答）：")
        if anchors:
            lines.append("【锚点本体 · 核心关注】")
            for a in anchors:
                lines.append(_fmt_anchor(a))
        if ontology:
            lines.append("【本体段 · 相关已装配知识】")
            for o in ontology:
                lines.append(_fmt_entity(o))
        if relations:
            lines.append("")
            lines.append("【关系约束 · 概念间关联】")
            seen: set[tuple] = set()
            for r in relations:
                key = (r["source_name"], r["target_name"], r["relation_type"])
                if key in seen:
                    continue
                seen.add(key)
                lines.append(_fmt_relation(r))
    if rag:
        lines.append("")
        lines.append("【参考资料 · 原文片段】")
        for t in rag:
            t = (t or "").strip()
            if t:
                lines.append(t)
    lines.append("")
    lines.append("回答规则（铁律）：")
    has_ont = use_ontology and bool(anchors or ontology)
    if has_ont and rag:
        lines.append("1. 优先依据【本体约束】回答，回答要具体、尽量可追溯到本体；"
                     "本体未覆盖但【参考资料】有的，可依据资料补充并说明依据。")
        lines.append("2. 本体与参考资料都没有相关内容时，明确说「我不知道」或"
                     "「我的知识里没有这方面内容」，绝不编造。")
    elif has_ont:
        lines.append("1. 只依据上述本体约束回答，回答要具体、尽量可追溯到你的本体。")
        lines.append("2. 如果问题超出你的本体知识，明确说「我不知道」或「我的本体里没有"
                     "这方面内容」，绝不编造。")
    elif rag:
        lines.append("1. 只依据上述参考资料回答，回答要具体、可追溯到原文。")
        lines.append("2. 如果问题超出参考资料范围，明确说「我不知道」或「资料里没有"
                     "这方面内容」，绝不编造。")
    else:
        lines.append("1. 诚实回答，不编造事实、不虚构来源。")
        lines.append("2. 如果不知道或不确认，明确说「我不确定」而不是猜测。")
    lines.append("3. 用中文回答。")

    prompt_text = _wrap_user_prompt_tail(ident.get("prompt") or "")
    if prompt_text:
        lines.append(prompt_text)

    return "\n".join(lines)


def _wrap_user_prompt_tail(prompt: str) -> str:
    """Wrap the user-defined additional prompt as an additive tail.

    Iron laws (ontology, "say I don't know", no fabrication) are hard-coded
    ABOVE this block and cannot be overridden by it. The wrapping explicitly
    states that the tail is auxiliary, so the LLM knows to defer to the iron
    laws on conflict. An empty/whitespace prompt yields nothing (no extra
    noise in the system prompt for users who didn't customize)."""
    p = (prompt or "").strip()
    if not p:
        return ""
    # Cap accidental huge prompts defensively (matches the chunk_summary cap).
    p = p[:4000]
    return PROMPT_TAIL_HEADER + p + PROMPT_TAIL_FOOTER


# ---------------- history persistence ----------------

def _history_messages(conn, identity_id: int, limit: int,
                      session_id: int | None = None) -> list[dict]:
    if session_id is not None:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE identity_id=?"
            " AND session_id=? ORDER BY id DESC LIMIT ?",
            (identity_id, session_id, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE identity_id=?"
            " AND session_id IS NULL ORDER BY id DESC LIMIT ?",
            (identity_id, limit)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _save(conn, identity_id: int, role: str, content: str,
          session_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO chat_messages(identity_id, role, content, created_at, session_id)"
        " VALUES(?,?,?,?,?)", (identity_id, role, content, db.now(), session_id))
    conn.commit()


def list_messages(conn, identity_id: int, session_id: int | None = None) -> list[dict]:
    # session 模式：按会话查全部消息（可跨数字人），带 identity_name 供气泡标注
    if session_id is not None:
        rows = conn.execute(
            "SELECT m.id, m.identity_id, m.role, m.content, m.created_at,"
            " m.session_id, i.name AS identity_name"
            " FROM chat_messages m LEFT JOIN identities i ON i.id = m.identity_id"
            " WHERE m.session_id=? ORDER BY m.id", (session_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.id, m.identity_id, m.role, m.content, m.created_at,"
            " m.session_id, i.name AS identity_name"
            " FROM chat_messages m LEFT JOIN identities i ON i.id = m.identity_id"
            " WHERE m.identity_id=? ORDER BY m.id", (identity_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_messages(conn, identity_id: int, session_id: int | None = None) -> int:
    if session_id is not None:
        cur = conn.execute(
            "DELETE FROM chat_messages WHERE identity_id=? AND session_id=?",
            (identity_id, session_id))
    else:
        cur = conn.execute("DELETE FROM chat_messages WHERE identity_id=?",
                           (identity_id,))
    conn.commit()
    return cur.rowcount


# ---------------- chat sessions (multi-session history) ----------------

def _auto_title(message: str) -> str:
    """Derive a session title from the first user message."""
    one = " ".join((message or "").split())
    return (one[:20] + "…") if len(one) > 20 else (one or "新对话")


def create_session(conn, identity_id: int, title: str = "") -> dict | None:
    if not _identity(conn, identity_id):
        return None
    cur = conn.execute(
        "INSERT INTO chat_sessions(identity_id, title, created_at) VALUES(?,?,?)",
        (identity_id, title or "新对话", db.now()))
    conn.commit()
    return {"id": cur.lastrowid, "identity_id": identity_id,
            "title": title or "新对话", "created_at": db.now()}


def rename_session(conn, session_id: int, title: str) -> bool:
    title = (title or "").strip()
    if not title:
        return False
    cur = conn.execute("UPDATE chat_sessions SET title=? WHERE id=?",
                       (title, session_id))
    conn.commit()
    return cur.rowcount > 0


def delete_session(conn, session_id: int) -> int:
    """Delete a session and its messages. Returns message count deleted."""
    n = conn.execute("DELETE FROM chat_messages WHERE session_id=?",
                     (session_id,)).rowcount
    conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    conn.commit()
    return n


def list_sessions(conn, identity_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.id, s.identity_id, s.title, s.created_at,"
        " (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS message_count"
        " FROM chat_sessions s WHERE s.identity_id=? ORDER BY s.id DESC",
        (identity_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------- responder dispatch (multi-model) ----------------

# responder providers: llm2 = GLM 5.2 (default), llm = DeepSeek V4-Flash,
# ollama = local Ollama 7B (hallucination A/B baseline)
PROVIDERS = ("llm2", "llm", "ollama")


def _provider_model(provider: str, ollama_model: str | None) -> str:
    if provider == "llm":
        return llm_settings_model("llm")
    if provider == "ollama":
        return ollama_model or DEFAULT_OLLAMA_MODEL
    return llm_settings_model("llm2")


def llm_settings_model(block: str) -> str:
    from . import settings_store
    return settings_store.load_settings()[block].get("model", "")


def _estimate_tokens(text: str) -> int:
    """Deterministic token estimate (CJK 1 char ≈ 1 token, other 4 chars ≈ 1).

    Fallback only — the provider's exact `usage.prompt_tokens` is preferred
    (see _generate()). Used when the endpoint does not report usage (e.g. UT fake)."""
    cjk = 0
    other = 0
    for ch in text:
        if ("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
                or "\uff00" <= ch <= "\uffef"):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4


_ollama_ctx_cache: dict[str, int] = {}


def _ollama_context_length(model: str, base_url: str) -> int:
    """Model's *native* context_length from Ollama /api/show (qwen2.5:7b -> 32768).

    Distinct from the runtime num_ctx (default 2048) that actually truncates.
    Best effort + cached; falls back to OLLAMA_NUM_CTX_DEFAULT on failure."""
    if model in _ollama_ctx_cache:
        return _ollama_ctx_cache[model]
    try:
        import json as _json
        req = netutil.local_request(
            f"{base_url.rstrip('/')}/api/show",
            data=_json.dumps({"model": model}).encode("utf-8"))
        with netutil.local_urlopen(req, timeout=5) as r:
            info = _json.loads(r.read().decode("utf-8")).get("model_info", {})
        for key, val in info.items():
            if key.endswith("context_length") and isinstance(val, int):
                _ollama_ctx_cache[model] = val
                return val
    except Exception:
        pass
    return OLLAMA_NUM_CTX_DEFAULT


_ollama_numctx_cache: dict[str, int] = {}


def _ollama_num_ctx(model: str, base_url: str) -> int:
    """Runtime num_ctx for an Ollama model, parsed from /api/show `parameters`
    (e.g. "num_ctx 32768"). Ollama defaults to 2048 when the Modelfile doesn't
    pin num_ctx — even though the model's native context_length is larger.

    Best effort + cached; falls back to OLLAMA_NUM_CTX_DEFAULT on failure."""
    if model in _ollama_numctx_cache:
        return _ollama_numctx_cache[model]
    try:
        import json as _json
        import re as _re
        req = netutil.local_request(
            f"{base_url.rstrip('/')}/api/show",
            data=_json.dumps({"model": model}).encode("utf-8"))
        with netutil.local_urlopen(req, timeout=5) as r:
            params = _json.loads(r.read().decode("utf-8")).get("parameters", "") or ""
        m = _re.search(r"num_ctx\s+(\d+)", params)
        val = int(m.group(1)) if m else OLLAMA_NUM_CTX_DEFAULT
        _ollama_numctx_cache[model] = val
        return val
    except Exception:
        return OLLAMA_NUM_CTX_DEFAULT


def _context_window(provider: str, model: str) -> tuple[int, int | None]:
    """(runtime window, model native context_length) in tokens.

    llm/llm2 = 1M (deployment spec). ollama: runtime = num_ctx parsed from the
    model's Modelfile parameters (default 2048 — Ollama silently truncates
    beyond it), native = model_info context_length."""
    if provider == "ollama":
        from . import settings_store
        s = settings_store.load_settings()["embedding"]
        base = (s.get("base_url") or "http://localhost:11434").rstrip("/")
        return _ollama_num_ctx(model, base), _ollama_context_length(model, base)
    return CONTEXT_WINDOWS.get(provider, 1_000_000), None


def _dispatch(provider: str, messages: list[dict], ollama_model: str | None,
              usage_out: dict | None = None) -> str:
    if provider == "ollama":
        return llm.chat_ollama(messages, temperature=0.5, model=ollama_model,
                               usage_out=usage_out)
    if provider == "llm":
        return llm.chat(messages, temperature=0.5, usage_out=usage_out)
    return llm.chat_persona(messages, usage_out=usage_out)  # llm2 (GLM 5.2)


# ---------------- optional RAG reference injection (「使用 RAG」开关) ----------------

def _rag_snippets(conn, message: str, top_k: int = RAG_TOP_K) -> dict:
    """Retrieve top-K corpus chunks for the message (0 LLM, embedding cosine)
    and cut them into injectable reference snippets.

    Returns {"used", "hits", "error", "texts"}. Any failure (embedding backend
    down / empty corpus / bad store) degrades gracefully: the persona answers
    without references and the context reports why (iron law: chat never
    silently fabricates a retrieval)."""
    try:
        from . import ingest
        hits = ingest.search(conn, message, top_k=top_k)
    except Exception as e:  # embedding unconfigured / corpus empty / store error
        return {"used": True, "hits": 0, "error": str(e)[:160], "texts": []}
    texts: list[str] = []
    for h in hits:
        base = ((h.get("text") or "").strip()
                or (h.get("summary") or "").strip())
        if not base:
            continue
        base = " ".join(base.split())
        if len(base) > RAG_SNIPPET_MAX:
            base = base[:RAG_SNIPPET_MAX].rstrip() + "…"
        texts.append(base)
    return {"used": True, "hits": len(hits), "error": None, "texts": texts}


# ---------------- dynamic retrieval window (0 LLM) ----------------

def _query_text(message: str, history: list[dict]) -> str:
    """相关性检索的匹配源 = 当前用户消息（不拼接历史）。

    历史对话（尤其 assistant 长回复）会带入上一轮话题的术语，使字面匹配
    命中数百个无关本体、挤占预算（实测拼历史后命中 4 → 391，关系被挤到
    只剩 1 条）。历史仅在 system prompt 里作为对话上下文用于理解指代，
    不作为本体检索的匹配源。`history` 参数保留，供未来做「历史 user 消息
    降权辅助」扩展。"""
    return message


def _match_score(query_lower: str, name: str, definition: str) -> float:
    """Literal-match score (0 LLM). Tiers:
      3.x — the whole name appears in the query (CJK or ascii substring);
      2.0 — an english token (>=3 chars) of the name appears in the query;
      1.0 — a keyword from the definition appears in the query;
      0.0 — no match."""
    nl = (name or "").lower().strip()
    if not nl:
        return 0.0
    if nl in query_lower:
        return 3.0 + min(len(nl), 20) / 20.0
    if any(len(t) >= 3 and t in query_lower for t in _RE_WORD.findall(nl)):
        return 2.0
    for kw in _RE_KW.findall((definition or "").lower()):
        if kw in query_lower:
            return 1.0
    return 0.0


def _score_ontology(query_text: str, ontology: list[dict]) -> list[tuple[int, float]]:
    """Return [(idx, score)] for ontology entities that literal-match the query."""
    ql = query_text.lower()
    hits = []
    for i, o in enumerate(ontology):
        s = _match_score(ql, o.get("name") or "", o.get("definition") or "")
        if s > 0:
            hits.append((i, s))
    return hits


# ---------------- concept-extraction fallback (#106, 仅对话路径) ----------------

def _extract_concepts_via_llm(message: str) -> list[str]:
    """V4-Flash 概念抽取兜底：字面匹配 0 命中时，从用户消息抽 3-8 个检索概念词。

    LLM 只输出概念（自由文本），不接触实体选择权——种子仍由确定性代码从
    active 本体段挑出。任何失败（LLM 不可达 / 非法 JSON / 空结果）都返回 []，
    由调用方降级枢纽中心兜底，对话不中断（兜底链路，非终审链路）。"""
    prompt = (
        "你是概念抽取器。从下面的用户消息中抽取 3-8 个用于知识检索的关键概念词，"
        "中文或英文皆可，专有名词保留英文原文，同义词只保留最具体的一个。"
        '只返回 JSON 数组（如 ["概念一", "concept two"]），不要任何解释。\n'
        f"用户消息：{message}")
    try:
        reply = llm.chat([{"role": "user", "content": prompt}], temperature=0.1)
        data = llm.extract_json(reply)
    except llm.LLMError:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for x in data:
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out[:8]


def _concept_hits(concepts: list[str],
                  ontology: list[dict]) -> list[tuple[int, float]]:
    """概念 ⋈ 本体 双向包含匹配（0 LLM，确定性）。

    复用 `_score_ontology`（概念拼接为 query：整名包含 / 英文 token / 定义
    关键词三档照常），并补充反向包含：概念是本体名的子串（如概念「定位」
    命中本体「视觉定位」——原 `_match_score` 只查「名 in query」方向）。
    返回 [(idx, score)] 按 score 降序。"""
    if not concepts:
        return []
    ql = " · ".join(concepts).lower()
    hits = {i: s for i, s in _score_ontology(ql, ontology)}
    cl = [c.lower().strip() for c in concepts if len(c.strip()) >= 2]
    for i, o in enumerate(ontology):
        nl = (o.get("name") or "").lower().strip()
        if not nl:
            continue
        best = 0.0
        for c in cl:
            if c == nl:
                best = max(best, 3.5)
            elif c in nl or nl in c:
                best = max(best, 2.5 + min(len(c), 10) / 20.0)
        if best > hits.get(i, 0.0):
            hits[i] = best
    return sorted(hits.items(), key=lambda x: -x[1])


def _relation_graph(ontology: list[dict], relations: list[dict]):
    """Adjacency (本体↔本体 edges only) + degree over the ontology entities.
    target names that are NOT ontology entities (free-text, ~34%) are ignored
    for graph traversal but still eligible for relation injection."""
    name_set = {o["name"] for o in ontology}
    adj = defaultdict(set)
    degree = {o["name"]: 0 for o in ontology}
    for r in relations:
        s, t = r["source_name"], r["target_name"]
        if s in name_set and t in name_set:
            adj[s].add(t)
            adj[t].add(s)
            degree[s] += 1
            degree[t] += 1
    return adj, degree


def _retrieve_context(anchors: list[dict], ontology: list[dict],
                      relations: list[dict], query_text: str,
                      budget_tokens: int,
                      extract_concepts=None) -> dict:
    """Dynamic retrieval window (deterministic core), layered by relevance.

    Anchors are always injected in full (the persona's fixed core). The rest is
    filled within `budget_tokens` in strict priority order:
      L1 命中本体 (query literal hits, score desc) — 用户问题里出现的本体
      L2 命中关系 (source ∈ 命中本体 ∪ 锚点本体; 本体↔本体 before 本体→文本)
      L3/L4 自适应扩展 (#107)：d = 1…MAX_EXPAND_DEPTH 逐跳「先本体后关系」，
           预算剩余 > EXPAND_RESERVE_RATIO 才进下一跳；注入本体总数有护栏。

    #106 概念抽取兜底：字面 0 命中且传入 `extract_concepts` 钩子（仅对话
    answer() 路径传入）时，LLM 抽概念词 → `_concept_hits` 确定性双向匹配挑
    种子——LLM 只出概念，不接触实体选择权。benchmark / compare 不传钩子，
    保持 0 LLM、可复现。

    Layering guarantees a hit entity's *relations* follow right after its
    content, instead of being squeezed out by weakly-relevant neighbours
    (the old "fill all ontology first, relations get the leftovers" bug)."""
    by_name = {o["name"]: o for o in ontology}
    name_set = set(by_name)

    anchor_cost = sum(_estimate_tokens(_fmt_anchor(a)) for a in anchors)
    remaining = max(0, budget_tokens - anchor_cost)

    scored = _score_ontology(query_text, ontology)
    scored.sort(key=lambda x: -x[1])
    hit_names = [ontology[i]["name"] for i, _ in scored]
    fallback = not hit_names

    # #106 概念抽取兜底（仅对话路径；LLM 只出概念，种子仍由代码挑）
    llm_fallback = {"used": False, "concepts": [], "hits": 0}
    concept_seeds: list[str] = []
    if fallback and extract_concepts is not None:
        llm_fallback["used"] = True
        concepts = extract_concepts(query_text)
        llm_fallback["concepts"] = concepts
        c_scored = _concept_hits(concepts, ontology)
        if c_scored:
            llm_fallback["hits"] = len(c_scored)
            fallback = False
            concept_seeds = [ontology[i]["name"] for i, _
                             in c_scored[:CONCEPT_SEED_LIMIT]]

    adj, degree = _relation_graph(ontology, relations)

    # seeds: query/concept hits, or (on empty hit) the top-K highest-degree
    # center entities — a bounded hub set, NOT the entire ontology (that would
    # defeat the "dynamic window" and silently inject everything on any
    # off-topic ask).
    if fallback:
        seeds = sorted(by_name, key=lambda n: -degree.get(n, 0))[:FALLBACK_CENTER_LIMIT]
    else:
        seeds = concept_seeds or hit_names
    seed_set = set(seeds)
    anchor_in_ont = [a["name"] for a in anchors if a["name"] in name_set]
    seed_set |= set(anchor_in_ont)

    # 分层关系：命中关系（source ∈ 命中本体 ∪ 锚点本体）紧跟命中内容注入；
    # 扩展关系在深度循环里逐跳注入。本体↔本体 (target 是本体库实体) 优先于
    # 本体→文本 (target 是自由文本)。
    def _is_oo(r: dict) -> bool:
        return r["target_name"] in name_set

    hit_rel: list[dict] = []
    seen_rel: set[tuple] = set()
    for r in relations:
        key = (r["source_name"], r["target_name"], r["relation_type"])
        if key in seen_rel:
            continue
        seen_rel.add(key)
        if r["source_name"] in seed_set:
            hit_rel.append(r)
    hit_rel.sort(key=lambda r: 0 if _is_oo(r) else 1)

    injected_ont: list[dict] = []
    injected_rel: list[dict] = []
    used = 0

    def _append(cost: int) -> bool:
        nonlocal used
        if used + cost > remaining:
            return False
        used += cost
        return True

    truncated = False

    # L1: 命中本体（排除已在锚点注入的 anchor_in_ont，避免与锚点段重复）
    anchor_names = set(anchor_in_ont)
    l1_count = 0
    for n in seeds:
        if n in anchor_names:
            continue
        if not _append(_estimate_tokens(_fmt_entity(by_name[n]))):
            truncated = True
            break
        injected_ont.append(by_name[n])
        l1_count += 1

    # L2: 命中本体（+锚点本体）的关系 — 紧跟命中内容，不再被邻居挤压
    for r in hit_rel:
        if not _append(_estimate_tokens(_fmt_relation(r))):
            truncated = True
            break
        injected_rel.append(r)

    # L3/L4 自适应扩展（#107）：预算驱动的逐跳扩展。第 1 跳不查富余阈值
    # （与旧版「固定 1 跳」行为一致），第 2 跳起预算剩余低于阈值即停。
    depth_reached = 0
    layer = set(seed_set)          # d=0：种子层（含锚点本体，可作扩展源）
    visited = set(seed_set)
    d = 0
    while d < MAX_EXPAND_DEPTH and len(injected_ont) < MAX_INJECTED_ONTOLOGY:
        d += 1
        if d > 1 and (remaining - used) < remaining * EXPAND_RESERVE_RATIO:
            break
        cand = set()
        for n in layer:
            cand |= adj.get(n, set())
        cand -= visited
        if not cand:
            break
        layer_names: list[str] = []
        for n in sorted(cand, key=lambda x: -degree.get(x, 0)):
            if len(injected_ont) >= MAX_INJECTED_ONTOLOGY:
                break
            if _append(_estimate_tokens(_fmt_entity(by_name[n]))):
                injected_ont.append(by_name[n])
                layer_names.append(n)
            else:
                truncated = True
                break
        if not layer_names:
            break
        depth_reached = d
        layer = set(layer_names)
        visited |= layer
        # 该跳邻居的关系（只注入真正放进上下文的实体作 source）
        layer_set = set(layer_names)
        layer_rel = []
        for r in relations:
            if r["source_name"] not in layer_set:
                continue
            key = (r["source_name"], r["target_name"], r["relation_type"])
            if key in seen_rel:
                continue
            seen_rel.add(key)
            layer_rel.append(r)
        layer_rel.sort(key=lambda r: 0 if _is_oo(r) else 1)
        for r in layer_rel:
            if not _append(_estimate_tokens(_fmt_relation(r))):
                truncated = True
                break
            injected_rel.append(r)

    return {
        "ontology": injected_ont,
        "relations": injected_rel,
        "stats": {
            "total_ontology": len(ontology),
            "total_relations": len(relations),
            "query_hits": len(hit_names),
            "fallback": fallback,
            "expanded": len(injected_ont) - l1_count,
            "depth_reached": depth_reached,
            "llm_fallback": llm_fallback,
            "budget_total": budget_tokens,
            "budget_used": anchor_cost + used,
            "truncated": truncated,
        },
    }


# ---------------- answer / compare ----------------

MAX_ACTION_ROUNDS = 3
_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)(?:</[^>]*>|\Z)", re.DOTALL)


def _actions_block(actions_list: list[dict]) -> str:
    lines = "\n".join(f"- {a['name']}：{a['description']}" for a in actions_list)
    return (
        "\n\n你拥有以下可用动作（需要时通过 <tool_call> 调用）：\n"
        + lines +
        "\n调用动作时，只输出这一行（不要额外文字），严格以 </tool_call> 结尾：\n"
        "<tool_call>{\"name\": \"动作名\", \"args\": {\"query\": \"...\"}}</tool_call>\n"
        "然后停止，等待执行结果。不需要动作时直接回答。"
    )


def _parse_tool_call(reply: str) -> tuple[str, dict] | None:
    m = _TOOL_CALL.search(reply or "")
    if not m:
        return None
    raw = m.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    args = data.get("args") or {}
    if not name or not isinstance(args, dict):
        return None
    return name, args


def _generate(conn, identity_id: int, message: str, use_ontology: bool,
              provider: str, ollama_model: str | None,
              concept_fallback: bool = False, use_rag: bool = False,
              session_id: int | None = None) -> dict:
    """Core one-shot generation (validation + retrieval + assembly + dispatch),
    shared by `answer` (persist) and `compare` (A/B, no persist).

    concept_fallback=True (answer 路径) 时启用 #106 概念抽取兜底：字面 0 命中
    才调 V4-Flash 抽概念词；compare / benchmark 不启用，保持 0 LLM 可复现。

    use_rag=True 时对消息检索语料 top-K 原文片段，以【参考资料】注入 system
    prompt（与 use_ontology 相互独立，可叠加；检索失败优雅降级，不中断对话）。

    Raises llm.LLMError when the responder is configured but unreachable.
    Returns {"ok": False, "error": ...} for deterministic input errors."""
    ident = _identity(conn, identity_id)
    if not ident:
        return {"ok": False, "error": f"数字人 #{identity_id} 不存在"}
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "消息不能为空"}
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"未知模型通道 {provider}"}
    if provider == "ollama" and not ollama_model:
        ollama_model = DEFAULT_OLLAMA_MODEL

    model = _provider_model(provider, ollama_model)
    context_window, model_ctx_len = _context_window(provider, model)
    budget_tokens = int(context_window * ONTOLOGY_BUDGET_RATIO)

    anchors = _approved_anchors(conn, identity_id)
    ontology = _persona_ontology(conn, identity_id)
    relations = _relations(conn, identity_id)
    history = _history_messages(conn, identity_id, HISTORY_LIMIT, session_id)

    if use_ontology:
        query_text = _query_text(message, history)
        retrieved = _retrieve_context(
            anchors, ontology, relations, query_text, budget_tokens,
            extract_concepts=_extract_concepts_via_llm if concept_fallback
            else None)
        injected_ont = retrieved["ontology"]
        injected_rel = retrieved["relations"]
        retrieval_stats = retrieved["stats"]
    else:
        injected_ont, injected_rel = [], []
        retrieval_stats = {
            "total_ontology": len(ontology),
            "total_relations": len(relations),
            "query_hits": 0, "fallback": False, "expanded": 0,
            "depth_reached": 0,
            "llm_fallback": {"used": False, "concepts": [], "hits": 0},
            "budget_total": budget_tokens, "budget_used": 0, "truncated": False,
        }

    # 「使用 RAG」开关：可选注入检索到的原文片段（独立于本体约束）
    rag_info = {"used": False, "hits": 0, "error": None}
    rag_texts: list[str] = []
    if use_rag:
        got = _rag_snippets(conn, message)
        rag_texts = got["texts"]
        rag_info = {"used": got["used"], "hits": got["hits"],
                    "error": got["error"]}

    ctx = {
        "identity": ident,
        "anchors": anchors,
        "ontology": injected_ont,
        "relations": injected_rel,
        "rag": rag_texts,
    }
    system = _system_prompt(ctx, use_ontology=use_ontology)
    # 附上该数字人已批准的动作清单（六元组 actions）
    from . import actions as actions_mod
    approved_acts = actions_mod.approved_actions(conn, identity_id)
    if approved_acts:
        system += _actions_block(approved_acts)

    messages = [{"role": "system", "content": system}] + history + [
        {"role": "user", "content": message}]

    usage: dict = {}
    reply = _dispatch(provider, messages, ollama_model, usage)  # raises LLMError

    # 动作调用循环（tool-use）：LLM 只提名 <tool_call>，guard 确定性裁决，执行后注入
    tool_calls: list[dict] = []
    for _ in range(MAX_ACTION_ROUNDS):
        parsed = _parse_tool_call(reply)
        if not parsed:
            break
        name, args = parsed
        ok, reason, action_row = actions_mod.guard_action(conn, identity_id, name, args)
        if not ok:
            tool_calls.append({"name": name, "ok": False, "reason": reason})
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": f"动作「{name}」被拒绝：{reason}。请直接回答或换一种方式。"})
            reply = _dispatch(provider, messages, ollama_model, usage)
            continue
        result = actions_mod.execute_action(conn, identity_id, action_row, args)
        tool_calls.append({"name": name, "ok": result.get("ok", False),
                           "result": (result.get("result") if result.get("ok")
                                      else result.get("error"))})
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user",
                         "content": "动作「" + name + "」执行结果："
                         + json.dumps(result, ensure_ascii=False)
                         + "。请基于此结果继续回答。"})
        reply = _dispatch(provider, messages, ollama_model, usage)

    prompt_tokens = usage.get("prompt_tokens")
    estimate = not isinstance(prompt_tokens, int)
    if estimate:  # endpoint reported no usage (UT fake / non-compliant) -> estimate
        prompt_tokens = _estimate_tokens(
            "".join(m.get("content") or "" for m in messages))
    sent_chars = sum(len(m.get("content") or "") for m in messages)
    percent = round(prompt_tokens * 100.0 / context_window, 2) if context_window else 0.0

    return {
        "ok": True,
        "reply": reply,
        "context": {
            "provider": provider,
            "model": model,
            "use_ontology": use_ontology,
            "use_rag": use_rag,
            "rag": rag_info,
            "tool_calls": tool_calls,
            "actions_available": [a["name"] for a in approved_acts],
            "anchors": [{"name": a["name"],
                         "definition": a.get("definition") or ""}
                        for a in anchors],
            "ontology": [{"name": o["name"],
                          "definition": o.get("definition") or ""}
                         for o in injected_ont],
            "relations": [{"source": r["source_name"],
                           "type": r["relation_type"],
                           "target": r["target_name"]}
                          for r in injected_rel],
            "counts": {
                "anchors": len(anchors),
                "ontology": len(ontology),     # full set size (display)
                "relations": len(relations),   # full set size (display)
            },
            "injected_ontology": len(injected_ont),
            "injected_relations": len(injected_rel),
            "retrieval": retrieval_stats,
            "truncated": retrieval_stats.get("truncated", False),
            "sent": messages,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "estimate": estimate,
                "sent_chars": sent_chars,
                "context_window": context_window,
                "model_context_length": model_ctx_len,
                "percent": percent,
            },
            "has_user_prompt": bool((ident.get("prompt") or "").strip()),
        },
    }


def answer(conn, identity_id: int, message: str, use_ontology: bool = True,
           provider: str = "llm2", ollama_model: str | None = None,
           use_rag: bool = False, session_id: int | None = None) -> dict:
    """Answer one user message as the persona, grounded by its ontology, and
    persist the turn to chat_messages (grouped under a chat session).

    provider selects the responder (llm2=GLM 5.2 default / llm=DeepSeek
    V4-Flash / ollama=local 7B). use_ontology=False strips the ontology block
    from the system prompt (hallucination A/B: grounded vs ungrounded).
    use_rag=True additionally injects top-K corpus snippets as 参考资料
    (independent of the ontology block, may be combined).

    When session_id is omitted, a new session is auto-created (titled from the
    first message) so the conversation lands in a fresh, named thread; if
    generation then fails, the empty session is rolled back.

    Raises llm.LLMError when the responder is configured but unreachable (the
    caller must surface it — iron law 2). Returns {"ok": False, "error": ...}
    for deterministic input errors (unknown identity / empty message)."""
    created_session = False
    if session_id is None:
        sess = create_session(conn, identity_id, _auto_title(message))
        if sess is None:
            return {"ok": False, "error": f"数字人 #{identity_id} 不存在"}
        session_id = sess["id"]
        created_session = True
    result = _generate(conn, identity_id, message, use_ontology, provider,
                       ollama_model, concept_fallback=True, use_rag=use_rag,
                       session_id=session_id)
    if not result.get("ok"):
        if created_session:
            delete_session(conn, session_id)  # roll back the empty session
        return result
    _save(conn, identity_id, "user", message, session_id)
    _save(conn, identity_id, "assistant", result["reply"], session_id)
    return {
        "ok": True,
        "reply": result["reply"],
        "session_id": session_id,
        "messages": list_messages(conn, identity_id, session_id),
        "context": result["context"],
    }


def compare(conn, identity_id: int, message: str,
            provider: str = "ollama", ollama_model: str | None = None,
            left: dict | None = None, right: dict | None = None) -> dict:
    """A/B comparison: the same message answered twice — left with the persona's
    ontology constraint (use_ontology=True), right without (ungrounded baseline).

    Each arm is independently configurable: left/right accept
    {"use_ontology": bool, "use_rag": bool} so the user can toggle ontology and
    RAG separately per pane (e.g. 本体 vs 本体+资料 vs 仅资料 vs 裸模型).

    Does NOT persist (both answers would pollute the conversation history).
    provider defaults to ollama (the local 7B is the hallucination baseline).

    Raises llm.LLMError if either side's responder fails. Returns
    {"ok": False, "error": ...} for deterministic input errors."""
    l = left or {"use_ontology": True, "use_rag": False}
    r = right or {"use_ontology": False, "use_rag": False}
    left_res = _generate(conn, identity_id, message,
                         bool(l.get("use_ontology", True)), provider,
                         ollama_model, use_rag=bool(l.get("use_rag", False)))
    if not left_res.get("ok"):
        return left_res
    right_res = _generate(conn, identity_id, message,
                          bool(r.get("use_ontology", False)), provider,
                          ollama_model, use_rag=bool(r.get("use_rag", False)))
    if not right_res.get("ok"):
        return right_res
    return {
        "ok": True,
        "left": {"reply": left_res["reply"], "context": left_res["context"]},
        "right": {"reply": right_res["reply"], "context": right_res["context"]},
    }
