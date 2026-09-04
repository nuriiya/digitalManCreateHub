# -*- coding: utf-8 -*-
"""Digital-person identity pre-screening: high-freq words -> LLM nominates
identities + anchor ontology -> user approves -> anchors guide extraction.

Iron laws hold throughout:
  - step 1 (word/tag stats) is PURE deterministic code, zero LLM;
  - the LLM only NOMINATES identities/anchors (stored as pending);
  - only user-approved anchors of an approved identity are injected into the
    extraction prompt - as GUIDANCE, never a whitelist (full extraction
    semantics untouched: entities outside the anchors are still nominated).
"""
import json
import re

from . import db, jobs, llm
from .jsonb import maybe_jsonb
from .ontology import ENTITY_TYPES, MAX_NAME_LEN, MAX_DEFINITION_LEN

TOP_WORDS = 80          # high-freq words fed to the nominator
MAX_IDENTITIES = 5      # nominate 3~5 identities
MAX_ANCHORS = 12        # 5~12 anchors per identity
MAX_KEYWORDS = 12

# function-word chars: n-grams containing them are noise, not ontology terms
_STOP_CHARS = set("的了和与及或对为等按其该本各被从到将使由也就都于以及关于对于根据通过进行可以应该需要如果那么因此但是并且不一个这个那些什么怎么")
_STOP_LATIN = {"the", "and", "for", "with", "this", "that", "from", "are", "was",
               "were", "will", "shall", "not", "but", "all", "can", "may", "you",
               "your", "our", "their", "into", "onto", "when", "then", "than"}

MAX_PROMPT_LEN = 4000   # 用户可编辑的附加指令上限（与 chat.py 注入上限一致）


# ---------------- step 1: deterministic high-freq stats (0 LLM) ----------------

def high_freq_words(conn, top: int = TOP_WORDS) -> list[tuple[str, int]]:
    """CJK 2/3-gram + latin-token frequency over all chunk texts.

    Deterministic ranking: (-count, -len); a shorter n-gram is dropped when a
    longer n-gram containing it accounts for (nearly) all of its occurrences
    (e.g. 采购 mostly appears inside 采购订单 -> keep 采购订单 only)."""
    bigrams: dict[str, int] = {}
    trigrams: dict[str, int] = {}
    latin: dict[str, int] = {}
    for row in conn.execute("SELECT text FROM chunks").fetchall():
        text = row["text"] or ""
        for run in re.finditer(r"[\u4e00-\u9fa5]+", text):
            s = run.group(0)
            for i in range(len(s) - 1):
                g2 = s[i:i + 2]
                if not (_STOP_CHARS & set(g2)):
                    bigrams[g2] = bigrams.get(g2, 0) + 1
                if i + 3 <= len(s):
                    g3 = s[i:i + 3]
                    if not (_STOP_CHARS & set(g3)):
                        trigrams[g3] = trigrams.get(g3, 0) + 1
        for w in re.finditer(r"[A-Za-z][A-Za-z0-9\-]{1,30}", text):
            t = w.group(0).lower()
            if t not in _STOP_LATIN:
                latin[t] = latin.get(t, 0) + 1

    # subsumption: drop bigram b if a trigram t containing b has count >= b's
    # (every occurrence of b lives inside t -> t is the real term)
    for b in list(bigrams):
        bc = bigrams[b]
        for t, tc in trigrams.items():
            if b in t and tc >= bc:
                del bigrams[b]
                break
    pool = sorted({**bigrams, **trigrams, **latin}.items(),
                  key=lambda kv: (-kv[1], -len(kv[0])))
    return pool[:top]


def tag_stats(conn, top: int = 20) -> list[tuple[str, int]]:
    """Aggregate chunk tags (already computed at ingest time, 0 LLM)."""
    counts: dict[str, int] = {}
    for row in conn.execute("SELECT tags FROM chunks WHERE tags IS NOT NULL").fetchall():
        tags = maybe_jsonb(row["tags"]) or []
        for t in tags if isinstance(tags, list) else []:
            if isinstance(t, str) and t.strip():
                v = t.strip()
                counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:top]


# ---------------- step 2: LLM nomination (nominate only) ----------------

def _nomination_prompt(words, tags, doc_summaries) -> str:
    w = "、".join(f"{k}({c})" for k, c in words)
    t = "、".join(f"{k}({c})" for k, c in tags)
    s = "\n".join(f"- {x[:120]}" for x in doc_summaries[:40])
    return (
        "你是数字人身份提名器。以下是从语料库确定性统计出的高频词、标签与文档摘要样本。\n"
        f"高频词：{w}\n标签：{t}\n文档摘要样本：\n{s}\n\n"
        "请据此提名 3~5 个该语料可能服务的「数字人身份」（例如：需求分析师、"
        "财务制度顾问、合规审查官），每个身份给出使命描述与 5~12 个细分本体锚点"
        "（该数字人最应该掌握的核心实体）。\n"
        "严格按 JSON 输出：\n"
        '{"identities": [{"name": "...", "mission": "...", "description": "...",'
        ' "keywords": ["高频词", ...], "anchors": [{"name": "...",'
        ' "type": "概念|角色|系统|流程|规则|对象|其他", "definition": "..."}]}]}\n'
        "锚点必须与语料内容相关，不要凭空编造。")


def _sanitize_identities(data) -> list[dict]:
    """Deterministic gates on LLM output: caps, closed enums, dedupe."""
    out: list[dict] = []
    seen_ids = set()
    for ident in (data.get("identities") or []) if isinstance(data, dict) else []:
        if not isinstance(ident, dict):
            continue
        name = str(ident.get("name") or "").strip()
        if not name or len(name) > MAX_NAME_LEN or name in seen_ids:
            continue
        seen_ids.add(name)
        seen_a = set()
        anchors = []
        for a in (ident.get("anchors") or [])[:MAX_ANCHORS]:
            if not isinstance(a, dict):
                continue
            an = str(a.get("name") or "").strip()
            at = str(a.get("type") or "").strip()
            adf = str(a.get("definition") or "").strip()[:MAX_DEFINITION_LEN]
            if not an or len(an) > MAX_NAME_LEN or an in seen_a:
                continue
            if at not in ENTITY_TYPES:
                at = "其他"
            seen_a.add(an)
            anchors.append({"name": an, "type": at, "definition": adf})
        kws = [str(k).strip() for k in (ident.get("keywords") or [])
               if isinstance(k, str) and k.strip()][:MAX_KEYWORDS]
        out.append({"name": name,
                    "mission": str(ident.get("mission") or "").strip()[:MAX_DEFINITION_LEN],
                    "description": str(ident.get("description") or "").strip()[:MAX_DEFINITION_LEN],
                    "keywords": kws, "anchors": anchors})
        if len(out) >= MAX_IDENTITIES:
            break
    return out


def run_nomination(conn, job_id: int) -> None:
    """Identity nomination job (kind=identity): stats -> one LLM call -> pending."""
    rows = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()
    if not rows or rows["c"] == 0:
        jobs.finish_job(conn, job_id, ok=False, error="没有 chunk，请先入库")
        return
    words = high_freq_words(conn)
    tags = tag_stats(conn)
    jobs.emit(conn, job_id, "identity.stats",
              {"words": len(words), "tags": len(tags),
               "top_words": [w for w, _ in words[:20]]})
    summaries = [r["doc_summary"] for r in conn.execute(
        "SELECT doc_summary FROM documents WHERE doc_summary IS NOT NULL"
        " AND TRIM(doc_summary)<>'' ORDER BY id").fetchall()]
    if not llm.llm_configured():
        jobs.finish_job(conn, job_id, ok=False,
                        error="身份提名需要配置 LLM（设置页 base_url + token）")
        return
    try:
        reply = llm.chat([{"role": "user",
                           "content": _nomination_prompt(words, tags, summaries)}])
    except llm.LLMError as e:
        jobs.auto_pause(conn, job_id, f"LLM 访问失败已自动暂停：{e}")
        return
    data = llm.extract_json(reply)
    idents = _sanitize_identities(data)
    if not idents:
        jobs.finish_job(conn, job_id, ok=False,
                        error="LLM 未提名出任何合法身份（回复无法解析或全被结构关拒绝）")
        return
    for ident in idents:
        cur = conn.execute(
            "INSERT INTO identities(name, mission, description, keywords, prompt,"
            " status, created_at) VALUES(?,?,?,?, ?, ?, ?)",
            (ident["name"], ident["mission"], ident["description"],
             json.dumps(ident["keywords"], ensure_ascii=False),
             "", "pending", db.now()))
        conn.commit()
        iid = cur.lastrowid
        for a in ident["anchors"]:
            conn.execute(
                "INSERT INTO anchors(identity_id, name, type, definition, status,"
                " created_at) VALUES(?,?,?,?, 'pending', ?)",
                (iid, a["name"], a["type"], a["definition"], db.now()))
        conn.commit()
    jobs.emit(conn, job_id, "identity.nominated",
              {"identities": len(idents),
               "anchors": sum(len(i["anchors"]) for i in idents)})
    jobs.finish_job(conn, job_id, ok=True)


# ---------------- anchor context for extraction prompt ----------------

def anchor_block(conn) -> str:
    """Prompt block of approved anchors under approved identities ('' if none).

    Read ONCE at extraction start (scheduler thread); injected as guidance,
    never a whitelist - the prompt itself says entities outside the anchors
    are still to be nominated as usual."""
    rows = conn.execute(
        "SELECT i.name iname, a.name, a.type, a.definition"
        " FROM anchors a JOIN identities i ON i.id = a.identity_id"
        " WHERE a.status='approved' AND i.status='approved'"
        " ORDER BY i.id, a.id").fetchall()
    if not rows:
        return ""
    by_ident: dict[str, list[str]] = {}
    for r in rows:
        by_ident.setdefault(r["iname"], []).append(
            f"{r['name']}（{r['type']}）：{r['definition'] or '（无定义）'}")
    lines = ["目标数字人身份与本体锚点（这是引导，不是白名单——"
             "优先并更仔细提取与锚点相关的内容；锚点之外的有价值实体与关系仍照常提名）："]
    for iname, anchors in by_ident.items():
        lines.append(f"身份「{iname}」的锚点：")
        lines.extend(f"- {a}" for a in anchors)
    return "\n".join(lines) + "\n\n"


# ---------------- status / CRUD ----------------

def set_identity_status(conn, identity_id: int, status: str) -> bool:
    if status not in ("approved", "rejected", "pending"):
        return False
    cur = conn.execute("UPDATE identities SET status=? WHERE id=?",
                       (status, identity_id))
    conn.commit()
    return cur.rowcount > 0


def choose_identity(conn, identity_id: int) -> dict | None:
    """Approve the chosen identity and delete all others (cascade anchors).

    This enforces the "one digital-person template per run" rule: the user
    picks one template from the LLM-nominated candidates; the rest are
    discarded automatically so they do not clutter the anchor set.
    """
    row = conn.execute("SELECT id FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE identities SET status='approved' WHERE id=?",
                 (identity_id,))
    cur = conn.execute("DELETE FROM identities WHERE id!=?", (identity_id,))
    deleted = cur.rowcount
    conn.commit()
    return {"approved_id": identity_id, "deleted": deleted}


def delete_identity(conn, identity_id: int) -> bool:
    """Hard-delete an identity template (and its anchors via cascade).

    Used when the user "rejects" a template: from their POV a rejected
    template is noise to be removed, not a status to retain. Returns
    False if the id doesn't exist.
    """
    cur = conn.execute("DELETE FROM identities WHERE id=?", (identity_id,))
    conn.commit()
    return cur.rowcount > 0


def create_identity(conn, name: str, mission: str,
                    description: str = "",
                    seed_candidate_ids: list[int] | None = None,
                    prompt: str = "") -> int | None:
    """Create a digital person from the ontology graph (user-defined).

    The user picks candidate nodes on the graph as SEED ontology; their pick
    is an explicit intent (final adjudication), so each seed is BOTH:
      - copied into persona_ontology (source_candidate_id = traceability), and
      - registered as an approved anchor so it also guides L0 anchor recall
        when the user later runs assembly to pull in more related ontology.
    The new identity is created 'approved' (user-authored, not LLM-nominated),
    so multiple digital persons can coexist.

    `prompt` is the user's optional ADDITIVE system-instruction tail (iron
    laws stay hard-coded ABOVE the tail in chat._system_prompt).
    """
    name = str(name or "").strip()
    mission = str(mission or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    if len(mission) > 500:
        return None
    user_prompt = str(prompt or "").strip()[:MAX_PROMPT_LEN]
    cur = conn.execute(
        "INSERT INTO identities(name, mission, description, keywords, prompt,"
        " status, created_at) VALUES(?,?,?,?, ?, ?, ?)",
        (name, mission, str(description or "").strip()[:MAX_DEFINITION_LEN],
         "[]", user_prompt, "approved", db.now()))
    identity_id = cur.lastrowid
    for cid in (seed_candidate_ids or []):
        cand = conn.execute(
            "SELECT kind, name, definition FROM candidates WHERE id=?",
            (cid,)).fetchone()
        if not cand:
            continue
        cname = cand["name"]
        conn.execute(
            "INSERT INTO anchors(identity_id, name, type, definition, status,"
            " created_at) VALUES(?,?,?,?, 'approved', ?)",
            (identity_id, cname, "概念", cand["definition"] or "", db.now()))
        exists = conn.execute(
            "SELECT id FROM persona_ontology WHERE identity_id=? AND kind=? AND name=?",
            (identity_id, cand["kind"], cname)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO persona_ontology(identity_id, kind, name, definition,"
                " source_candidate_id, status, created_at)"
                " VALUES(?,?,?,?,?, 'active', ?)",
                (identity_id, cand["kind"], cname, cand["definition"] or "",
                 cid, db.now()))
    conn.commit()
    return identity_id


def update_identity(conn, identity_id: int, patch: dict) -> bool:
    """Lightweight edit: name / mission / description / prompt. Anchors are edited
    separately (update_anchor / add_anchor); the assembled ontology 段 is
    untouched (it iterates independently)."""
    sets, vals = [], []
    if "name" in patch:
        name = str(patch["name"] or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            return False
        sets.append("name=?"); vals.append(name)
    if "mission" in patch:
        mission = str(patch["mission"] or "").strip()
        if len(mission) > 500:
            return False
        sets.append("mission=?"); vals.append(mission)
    if "description" in patch:
        d = str(patch["description"] or "").strip()
        if len(d) > MAX_DEFINITION_LEN:
            return False
        sets.append("description=?"); vals.append(d)
    if "prompt" in patch:
        # empty string allowed (clears the user-tail); whitespace normalized.
        # `prompt` may be None from the API (skip), but empty str is a valid clear.
        p_raw = patch.get("prompt")
        if p_raw is None:
            pass
        else:
            user_prompt = str(p_raw).strip()[:MAX_PROMPT_LEN]
            sets.append("prompt=?"); vals.append(user_prompt)
    if not sets:
        return False
    vals.append(identity_id)
    cur = conn.execute(f"UPDATE identities SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return cur.rowcount > 0


def set_anchor_status(conn, anchor_id: int, status: str) -> bool:
    if status not in ("approved", "rejected", "pending"):
        return False
    cur = conn.execute("UPDATE anchors SET status=? WHERE id=?", (status, anchor_id))
    conn.commit()
    return cur.rowcount > 0


def update_anchor(conn, anchor_id: int, patch: dict) -> bool:
    sets, vals = [], []
    if "name" in patch:
        name = str(patch["name"] or "").strip()
        if not name or len(name) > MAX_NAME_LEN:
            return False
        sets.append("name=?"); vals.append(name)
    if "type" in patch:
        t = patch["type"]
        if t is not None and t not in ENTITY_TYPES:
            return False
        sets.append("type=?"); vals.append(t)
    if "definition" in patch:
        d = patch["definition"]
        if d is not None and len(str(d)) > MAX_DEFINITION_LEN:
            return False
        sets.append("definition=?"); vals.append(d)
    if not sets:
        return False
    vals.append(anchor_id)
    cur = conn.execute(f"UPDATE anchors SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return cur.rowcount > 0


def add_anchor(conn, identity_id: int, patch: dict) -> int | None:
    row = conn.execute("SELECT id FROM identities WHERE id=?",
                       (identity_id,)).fetchone()
    if not row:
        return None
    name = str(patch.get("name") or "").strip()
    if not name or len(name) > MAX_NAME_LEN:
        return None
    t = patch.get("type")
    if t not in ENTITY_TYPES:
        t = "其他"
    d = str(patch.get("definition") or "").strip()[:MAX_DEFINITION_LEN]
    cur = conn.execute(
        "INSERT INTO anchors(identity_id, name, type, definition, status,"
        " created_at) VALUES(?,?,?,?, 'pending', ?)",
        (identity_id, name, t, d, db.now()))
    conn.commit()
    return cur.lastrowid


def list_identities(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM identities ORDER BY id DESC").fetchall():
        ident = dict(r)
        ident["keywords"] = maybe_jsonb(ident.get("keywords")) or []
        ident["prompt"] = ident.get("prompt") or ""
        anchors = conn.execute(
            "SELECT id, identity_id, name, type, definition, status FROM anchors"
            " WHERE identity_id=? ORDER BY id", (r["id"],)).fetchall()
        ident["anchors"] = [dict(a) for a in anchors]
        out.append(ident)
    return out
