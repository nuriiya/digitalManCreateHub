# -*- coding: utf-8 -*-
"""Chat tests: persona constraint assembly (0 LLM) + GLM 5.2 answer +
persistence. GLM is a responder grounded by the ontology block; the persona
says it doesn't know rather than hallucinating (iron law 2)."""

from app import llm


def _insert_identity(conn, name="财务制度顾问", mission="解答财务制度与报销流程",
                     anchors=None, ontology=None, description=""):
    cur = conn.execute(
        "INSERT INTO identities(name, mission, description, keywords, status, created_at)"
        " VALUES(?, ?, ?, '[]', 'approved', 0)", (name, mission, description))
    conn.commit()
    iid = cur.lastrowid
    for a in (anchors or []):
        conn.execute(
            "INSERT INTO anchors(identity_id, name, type, definition, status, created_at)"
            " VALUES(?, ?, '概念', '', 'approved', 0)", (iid, a))
    for o in (ontology or []):
        conn.execute(
            "INSERT INTO persona_ontology(identity_id, kind, name, definition,"
            " source_candidate_id, status, created_at)"
            " VALUES(?, 'entity', ?, ?, NULL, 'active', 0)", (iid, o, ""))
    conn.commit()
    return iid


def _insert_candidate_with_relation(conn, name):
    cur = conn.execute(
        "INSERT INTO candidates(kind, name, definition, status, created_at)"
        " VALUES('entity', ?, '', 'approved', 0)", (name,))
    conn.commit()
    return cur.lastrowid


# ---------------- constraint assembly (deterministic, 0 LLM) ----------------

def test_persona_context_and_system_prompt(env):
    from app import chat
    conn = env
    src = _insert_candidate_with_relation(conn, "报销单")
    iid = _insert_identity(conn, name="财务顾问", mission="解答报销流程",
                           anchors=["报销流程", "预算"],
                           ontology=["报销单"])
    # link 报销单 -> 审批节点 as a relation (source_candidate_id back-pointer)
    conn.execute(
        "UPDATE persona_ontology SET source_candidate_id=? WHERE identity_id=?",
        (src, iid))
    conn.execute(
        "INSERT INTO relations(source_id, target_name, relation_type, chunk_id)"
        " VALUES(?, '审批节点', '进入', NULL)", (src,))
    conn.commit()

    ctx = chat.persona_context(conn, iid)
    assert ctx is not None
    assert ctx["identity"]["name"] == "财务顾问"
    assert {a["name"] for a in ctx["anchors"]} == {"报销流程", "预算"}
    assert {o["name"] for o in ctx["ontology"]} == {"报销单"}
    assert len(ctx["relations"]) == 1

    prompt = chat._system_prompt(ctx)
    assert "财务顾问" in prompt
    assert "解答报销流程" in prompt
    assert "报销流程" in prompt and "预算" in prompt
    assert "报销单" in prompt
    assert "报销单 --进入--> 审批节点" in prompt
    assert "我不知道" in prompt  # iron law 2 phrasing


def test_persona_context_missing_identity(env):
    from app import chat
    assert chat.persona_context(env, 99999) is None


# ---------------- answer (GLM responder + persistence) ----------------

def test_answer_calls_glm_and_persists(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn)

    def persona(messages):
        # first message is the system prompt, last is the user message
        assert messages[0]["role"] == "system"
        assert "财务制度顾问" in messages[0]["content"]
        assert messages[-1] == {"role": "user", "content": "报销要几步？"}
        return "报销通常三步：提交、审批、复核。"

    fake_llm["judge"] = persona
    r = chat.answer(conn, iid, "报销要几步？")
    assert r["ok"]
    assert "三步" in r["reply"]
    assert r["context"]["counts"]["anchors"] == 0
    assert r["context"]["counts"]["ontology"] == 0
    assert r["context"]["ontology"] == []
    assert r["context"]["provider"] == "llm2"
    assert r["context"]["use_ontology"] is True
    rows = conn.execute(
        "SELECT role, content FROM chat_messages WHERE identity_id=? ORDER BY id",
        (iid,)).fetchall()
    assert [x["role"] for x in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "报销通常三步：提交、审批、复核。"
    # the response carries the full persisted history
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]


def test_answer_injects_history(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn)
    captured = {}

    def persona(messages):
        captured["roles"] = [m["role"] for m in messages]
        return "回答"

    fake_llm["judge"] = persona
    chat.answer(conn, iid, "第一问")
    chat.answer(conn, iid, "第二问")
    # second turn: system + user + assistant + user
    assert captured["roles"] == ["system", "user", "assistant", "user"]


def test_answer_unknown_identity(env, fake_llm):
    from app import chat
    r = chat.answer(env, 99999, "你好")
    assert not r["ok"] and "不存在" in r["error"]


def test_answer_empty_message(env, fake_llm):
    from app import chat
    iid = _insert_identity(env)
    assert not chat.answer(env, iid, "   ")["ok"]


def test_answer_glm_unreachable_raises(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn)

    def boom(messages):
        raise llm.LLMError("GLM down")

    fake_llm["judge"] = boom
    import pytest
    with pytest.raises(llm.LLMError):
        chat.answer(conn, iid, "你好")
    # nothing persisted on failure
    assert conn.execute("SELECT COUNT(*) c FROM chat_messages").fetchone()["c"] == 0


# ---------------- history persistence ----------------

def test_list_and_clear_messages(env):
    from app import chat
    conn = env
    iid = _insert_identity(conn)
    chat._save(conn, iid, "user", "a")
    chat._save(conn, iid, "assistant", "b")
    assert len(chat.list_messages(conn, iid)) == 2
    assert chat.clear_messages(conn, iid) == 2
    assert chat.list_messages(conn, iid) == []


def test_chat_persona_routes_llm2(env, fake_llm):
    """chat_persona uses the llm2 (GLM) fake, not the generator channel."""
    from app import llm as llm_mod
    fake_llm["judge"] = lambda m: "GLM 回复"
    out = llm_mod.chat_persona([{"role": "user", "content": "hi"}])
    assert out == "GLM 回复"


# ---------------- multi-model responder + ontology toggle ----------------

def test_answer_provider_ollama(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn)

    def ollama(messages):
        assert messages[0]["role"] == "system"
        return "本地 7B 回复"

    fake_llm["ollama"] = ollama
    r = chat.answer(conn, iid, "你好", provider="ollama", ollama_model="qwen2.5:7b")
    assert r["ok"] and r["reply"] == "本地 7B 回复"
    assert r["context"]["provider"] == "ollama"
    assert r["context"]["model"] == "qwen2.5:7b"


def test_answer_provider_llm(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn)

    def generator(messages):
        return "DeepSeek 回复"

    fake_llm["reply"] = generator
    r = chat.answer(conn, iid, "你好", provider="llm")
    assert r["ok"] and r["reply"] == "DeepSeek 回复"
    assert r["context"]["provider"] == "llm"


def test_answer_unknown_provider(env, fake_llm):
    from app import chat
    iid = _insert_identity(env)
    r = chat.answer(env, iid, "你好", provider="nope")
    assert not r["ok"] and "未知模型通道" in r["error"]


def test_answer_use_ontology_off_strips_ontology(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, anchors=["差旅补贴"],
                           ontology=["报销单", "预算"])

    captured = {}

    def persona(messages):
        captured["system"] = messages[0]["content"]
        return "回答"

    fake_llm["judge"] = persona
    r = chat.answer(conn, iid, "报销要几步？", use_ontology=False)
    assert r["ok"]
    # identity/mission still present, but no ontology block
    assert "财务制度顾问" in captured["system"]
    assert "差旅补贴" not in captured["system"]
    assert "报销单" not in captured["system"]
    assert "本体约束" not in captured["system"]
    assert r["context"]["use_ontology"] is False
    assert r["context"]["injected_ontology"] == 0
    # the response still reports the ontology list for display
    assert r["context"]["counts"]["ontology"] == 2


def test_answer_returns_ontology_list(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, anchors=["报销流程"],
                           ontology=["报销单", "预算"])

    fake_llm["judge"] = lambda m: "回答"
    r = chat.answer(conn, iid, "你好")
    assert r["ok"]
    c = r["context"]
    assert [a["name"] for a in c["anchors"]] == ["报销流程"]
    assert {o["name"] for o in c["ontology"]} == {"报销单", "预算"}
    assert c["counts"]["anchors"] == 1
    assert c["counts"]["ontology"] == 2
    assert c["injected_ontology"] == 2
    assert c["truncated"] is False


# ---------------- dynamic retrieval window (相关性检索 + 预算填充) ----------------

def test_retrieve_hits_relevant_entity(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "注意力机制", "definition": "动态关注重要部分的技术"},
        {"kind": "entity", "name": "RefCOCO", "definition": "指代表达理解数据集"},
        {"kind": "entity", "name": "无关概念X", "definition": "完全不同的东西"},
    ]
    r = chat._retrieve_context([], ontology, [], "什么是注意力机制？", budget_tokens=100000)
    names = [o["name"] for o in r["ontology"]]
    assert "注意力机制" in names
    assert "无关概念X" not in names
    assert r["stats"]["query_hits"] == 1
    assert r["stats"]["fallback"] is False


def test_query_text_ignores_history(env):
    from app import chat
    # 历史对话不应进入匹配源（否则上一轮话题的术语会污染本轮相关性检索）
    hist = [
        {"role": "user", "content": "RefCOCO 数据集怎么标注？"},
        {"role": "assistant", "content": "通过边界框标注目标对象..."},
    ]
    assert chat._query_text("什么是视觉定位？", hist) == "什么是视觉定位？"


def test_retrieve_fallback_centers(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
        {"kind": "entity", "name": "C", "definition": "d"},
    ]
    relations = [
        {"source_name": "A", "target_name": "B", "relation_type": "is_a"},
        {"source_name": "A", "target_name": "C", "relation_type": "is_a"},
    ]
    r = chat._retrieve_context([], ontology, relations, "你好", budget_tokens=100000)
    assert r["stats"]["fallback"] is True
    # A 是中心（degree=2），应排在 B/C（degree=1）之前
    assert r["ontology"][0]["name"] == "A"


def test_retrieve_expands_neighbors_and_prioritizes_oo(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
    ]
    relations = [
        {"source_name": "A", "target_name": "自由文本", "relation_type": "has"},
        {"source_name": "A", "target_name": "B", "relation_type": "is_a"},
    ]
    r = chat._retrieve_context([], ontology, relations, "A", budget_tokens=100000)
    # A 命中，B 是 1 跳邻居（本体↔本体）
    assert {o["name"] for o in r["ontology"]} == {"A", "B"}
    # 关系注入：本体↔本体(is_a) 优先于 本体→文本(has)
    assert r["relations"][0]["relation_type"] == "is_a"


def test_retrieve_budget_truncation(env):
    from app import chat
    ontology = [{"kind": "entity", "name": f"概念{i}", "definition": "定义内容" * 10}
                for i in range(50)]
    r = chat._retrieve_context([], ontology, [], "你好", budget_tokens=120)
    # 空命中 → 兜底填「中心本体」（有界枢纽集），但预算只够放部分 → 截断
    assert 0 < len(r["ontology"]) < 50
    # 兜底种子有界：不应因预算充足就平铺全部本体
    assert len(r["ontology"]) <= chat.FALLBACK_CENTER_LIMIT
    assert r["stats"]["truncated"] is True
    assert r["stats"]["fallback"] is True
    assert r["stats"]["budget_used"] <= 120


def test_retrieve_anchor_cost_in_budget(env):
    from app import chat
    anchors = [{"name": "报销流程", "type": "概念", "definition": "核心关注"}]
    r = chat._retrieve_context(anchors, [], [], "你好", budget_tokens=1000)
    assert 0 < r["stats"]["budget_used"] <= 1000  # 锚点成本计入预算


# ---------------- 概念抽取兜底 (#106) + 自适应扩展深度 (#107) ----------------

def test_concept_hits_reverse_containment(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "视觉定位", "definition": "语言到图像区域的映射"},
        {"kind": "entity", "name": "无关概念X", "definition": "完全不同的东西"},
    ]
    # 概念 == 本体名 → 3.5
    hits = chat._concept_hits(["视觉定位"], ontology)
    assert [ontology[i]["name"] for i, _ in hits] == ["视觉定位"]
    assert hits[0][1] == 3.5
    # 反向包含：概念「定位」⊂ 本体名「视觉定位」（原 _match_score 只查正向）
    hits = chat._concept_hits(["定位"], ontology)
    assert [ontology[i]["name"] for i, _ in hits] == ["视觉定位"]
    assert hits[0][1] >= 2.5
    # 无关概念不命中
    assert chat._concept_hits(["量子纠缠"], ontology) == []


def test_concept_fallback_seeds_on_zero_hits(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "视觉定位", "definition": "语言到图像区域的映射"},
        {"kind": "entity", "name": "无关概念X", "definition": "完全不同的东西"},
    ]
    # 字面 0 命中（消息不含任何本体名/定义关键词）→ 概念「定位」反向命中
    r = chat._retrieve_context([], ontology, [], "图里的人在哪？",
                               budget_tokens=100000,
                               extract_concepts=lambda msg: ["定位", "指代表达"])
    names = [o["name"] for o in r["ontology"]]
    assert "视觉定位" in names
    assert "无关概念X" not in names
    st = r["stats"]
    assert st["query_hits"] == 0            # 字面命中仍为 0（真实值）
    assert st["fallback"] is False          # 概念路径接管，不走枢纽兜底
    assert st["llm_fallback"]["used"] is True
    assert st["llm_fallback"]["concepts"] == ["定位", "指代表达"]
    assert st["llm_fallback"]["hits"] == 1


def test_concept_fallback_empty_falls_back_to_hubs(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
    ]
    relations = [{"source_name": "A", "target_name": "B",
                  "relation_type": "is_a"}]
    # 概念抽取失败（返回空）→ 降级现状枢纽中心兜底，不报错
    r = chat._retrieve_context([], ontology, relations, "你好",
                               budget_tokens=100000,
                               extract_concepts=lambda msg: [])
    st = r["stats"]
    assert st["fallback"] is True
    assert st["llm_fallback"]["used"] is True
    assert st["llm_fallback"]["hits"] == 0
    assert r["ontology"][0]["name"] == "A"  # 度数最高的枢纽


def test_extract_concepts_via_llm_parses_and_degrades(env, fake_llm):
    from app import chat
    fake_llm["concepts"] = lambda p: '["视觉定位", "grounding", "定位"]'
    assert chat._extract_concepts_via_llm("图里的人在哪") == \
        ["视觉定位", "grounding", "定位"]
    # 非法 JSON → 空列表（降级，不抛错）
    fake_llm["concepts"] = lambda p: "这不是 JSON"
    assert chat._extract_concepts_via_llm("图里的人在哪") == []
    # LLM 不可达 → 空列表
    from app import llm as llm_mod
    orig = llm_mod.chat
    llm_mod.chat = lambda m, temperature=0.2, usage_out=None: (
        (_ for _ in ()).throw(llm_mod.LLMError("down")))
    try:
        assert chat._extract_concepts_via_llm("图里的人在哪") == []
    finally:
        llm_mod.chat = orig


def test_retrieve_depth_two_when_budget_allows(env):
    from app import chat
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
        {"kind": "entity", "name": "C", "definition": "d"},
    ]
    relations = [
        {"source_name": "A", "target_name": "B", "relation_type": "is_a"},
        {"source_name": "B", "target_name": "C", "relation_type": "is_a"},
    ]
    r = chat._retrieve_context([], ontology, relations, "A",
                               budget_tokens=100000)
    # 链 A→B→C：预算充足 → 1 跳扩到 B，2 跳扩到 C
    assert {o["name"] for o in r["ontology"]} == {"A", "B", "C"}
    assert r["stats"]["depth_reached"] == 2
    assert r["stats"]["expanded"] == 2  # B、C 经扩展注入


def test_retrieve_depth_capped(env, monkeypatch):
    from app import chat
    monkeypatch.setattr(chat, "MAX_EXPAND_DEPTH", 1)
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
        {"kind": "entity", "name": "C", "definition": "d"},
    ]
    relations = [
        {"source_name": "A", "target_name": "B", "relation_type": "is_a"},
        {"source_name": "B", "target_name": "C", "relation_type": "is_a"},
    ]
    r = chat._retrieve_context([], ontology, relations, "A",
                               budget_tokens=100000)
    assert {o["name"] for o in r["ontology"]} == {"A", "B"}  # C 在 2 跳外
    assert r["stats"]["depth_reached"] == 1


def test_retrieve_reserve_ratio_stops_expansion(env, monkeypatch):
    from app import chat
    # 富余阈值 >100%：第 2 跳永不触发（第 1 跳保持与旧版一致）
    monkeypatch.setattr(chat, "EXPAND_RESERVE_RATIO", 1.01)
    ontology = [
        {"kind": "entity", "name": "A", "definition": "d"},
        {"kind": "entity", "name": "B", "definition": "d"},
        {"kind": "entity", "name": "C", "definition": "d"},
    ]
    relations = [
        {"source_name": "A", "target_name": "B", "relation_type": "is_a"},
        {"source_name": "B", "target_name": "C", "relation_type": "is_a"},
    ]
    r = chat._retrieve_context([], ontology, relations, "A",
                               budget_tokens=100000)
    assert "C" not in {o["name"] for o in r["ontology"]}
    assert r["stats"]["depth_reached"] == 1


def test_retrieve_max_injected_guard(env, monkeypatch):
    from app import chat
    monkeypatch.setattr(chat, "MAX_INJECTED_ONTOLOGY", 2)
    ontology = [{"kind": "entity", "name": n, "definition": "d"}
                for n in ["A", "N1", "N2", "N3", "N4", "N5"]]
    relations = [{"source_name": "A", "target_name": f"N{i}",
                  "relation_type": "is_a"} for i in range(1, 6)]
    r = chat._retrieve_context([], ontology, relations, "A",
                               budget_tokens=100000)
    # 注入本体总数（含 L1 种子）受护栏限制
    assert len(r["ontology"]) <= 2


def test_answer_concept_fallback_integration(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, ontology=["视觉定位"])
    fake_llm["judge"] = lambda m: "回答"
    fake_llm["concepts"] = lambda p: '["定位"]'
    # 「图里的人在哪？」字面 0 命中 → V4-Flash 抽概念「定位」→ 反向命中视觉定位
    r = chat.answer(conn, iid, "图里的人在哪？")
    assert r["ok"]
    st = r["context"]["retrieval"]
    assert st["llm_fallback"]["used"] is True
    assert st["llm_fallback"]["hits"] == 1
    assert r["context"]["injected_ontology"] == 1
    assert "视觉定位" in r["context"]["sent"][0]["content"]


def test_compare_stays_deterministic_no_concept_fallback(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, ontology=["视觉定位"])
    fake_llm["judge"] = lambda m: "回答"
    fake_llm["ollama"] = lambda m: "回复"
    calls = {"concepts": 0}

    def _spy(prompt):
        calls["concepts"] += 1
        return '["定位"]'
    fake_llm["concepts"] = _spy
    # compare 的 A/B 两侧都不启用概念兜底（0 LLM 可复现）
    r = chat.compare(conn, iid, "图里的人在哪？")
    assert r["ok"]
    assert calls["concepts"] == 0
    assert r["left"]["context"]["retrieval"]["fallback"] is True
    assert r["left"]["context"]["retrieval"]["llm_fallback"]["used"] is False


def test_answer_returns_retrieval_stats(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, anchors=["报销流程"], ontology=["报销单", "预算"])
    fake_llm["judge"] = lambda m: "回答"
    r = chat.answer(conn, iid, "报销单怎么填？")
    c = r["context"]
    assert c["retrieval"]["query_hits"] == 1      # "报销单" 命中
    assert c["retrieval"]["total_ontology"] == 2
    assert c["injected_ontology"] == 1
    assert c["injected_relations"] == 0


def test_chat_ollama_routes_fake(env, fake_llm):
    from app import llm as llm_mod
    fake_llm["ollama"] = lambda m: "Ollama 回复"
    out = llm_mod.chat_ollama([{"role": "user", "content": "hi"}],
                              model="qwen2.5:7b")
    assert out == "Ollama 回复"


# ---------------- sent content + context-window usage (上下文占比) ----------------

def test_answer_returns_sent_and_usage(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, anchors=["报销流程"])

    fake_llm["judge"] = lambda m: "回答"
    r = chat.answer(conn, iid, "你好")
    assert r["ok"]
    c = r["context"]
    # sent = the exact message list handed to the model (system + user)
    assert [m["role"] for m in c["sent"]] == ["system", "user"]
    assert c["sent"][0]["content"].startswith("你是数字人")
    assert c["sent"][-1] == {"role": "user", "content": "你好"}
    # usage: the fake reports no usage -> estimate=True with the 1M llm2 window
    u = c["usage"]
    assert u["estimate"] is True
    assert isinstance(u["prompt_tokens"], int) and u["prompt_tokens"] > 0
    assert u["context_window"] == 1_000_000
    assert u["model_context_length"] is None  # llm2 has no native-length concept
    assert u["percent"] == round(u["prompt_tokens"] * 100.0 / 1_000_000, 2)
    # sent_chars = total characters across the exact message list sent
    assert u["sent_chars"] == sum(len(m["content"]) for m in c["sent"])


def test_answer_usage_exact_when_reported(env, fake_llm, monkeypatch):
    """When the endpoint reports exact usage, prompt_tokens is NOT re-estimated."""
    from app import chat, llm as llm_mod

    def fake_with_usage(messages, usage_out=None):
        if usage_out is not None:
            usage_out.update({"prompt_tokens": 123, "completion_tokens": 7,
                              "total_tokens": 130})
        return "回答"

    monkeypatch.setattr(llm_mod, "chat_persona", fake_with_usage)
    conn = env
    iid = _insert_identity(conn)
    r = chat.answer(conn, iid, "你好")
    u = r["context"]["usage"]
    assert u["estimate"] is False
    assert u["prompt_tokens"] == 123
    assert u["completion_tokens"] == 7
    assert u["percent"] == round(123 * 100.0 / 1_000_000, 2)


def test_context_window_values(env, monkeypatch):
    from app import chat
    assert chat._context_window("llm", "x") == (1_000_000, None)
    assert chat._context_window("llm2", "x") == (1_000_000, None)
    # ollama runtime num_ctx defaults to 2048 unless the Modelfile pins it
    monkeypatch.setattr(chat, "_ollama_num_ctx", lambda model, base: 2048)
    monkeypatch.setattr(chat, "_ollama_context_length", lambda model, base: 32768)
    assert chat._context_window("ollama", "qwen2.5:7b") == (2048, 32768)
    # the 32k variant pins num_ctx to the model max -> no silent truncation
    monkeypatch.setattr(chat, "_ollama_num_ctx", lambda model, base: 32768)
    assert chat._context_window("ollama", "qwen2.5:7b-32k") == (32768, 32768)


def test_ollama_num_ctx_parses_parameters(env, monkeypatch):
    """_ollama_num_ctx parses num_ctx from /api/show `parameters`, else 2048."""
    from app import chat

    def fake_show(model, base_url):
        if model.endswith("-32k"):
            return {"parameters": "num_ctx                        32768"}
        return {"parameters": ""}

    monkeypatch.setattr(chat, "_ollama_numctx_cache", {})
    monkeypatch.setattr("urllib.request.urlopen", None, raising=False)
    import urllib.request as _ur

    captured = {}

    def _open(req, timeout=None):
        import json as _json
        import io
        model = _json.loads(req.data.decode("utf-8"))["model"]
        return io.BytesIO(_json.dumps(fake_show(model, "")).encode("utf-8"))

    monkeypatch.setattr(_ur, "urlopen", _open)
    assert chat._ollama_num_ctx("qwen2.5:7b", "http://x") == 2048
    assert chat._ollama_num_ctx("qwen2.5:7b-32k", "http://x") == 32768


# ---------------- A/B compare (对比: 有本体 vs 无本体) ----------------

def test_compare_returns_left_right(env, fake_llm):
    from app import chat
    conn = env
    iid = _insert_identity(conn, anchors=["报销流程"], ontology=["报销单"])

    def ollama(messages):
        return "有本体回复" if messages[0]["content"].find("本体约束") >= 0 else "无本体回复"

    fake_llm["ollama"] = ollama
    r = chat.compare(conn, iid, "报销要几步？", provider="ollama",
                     ollama_model="qwen2.5:7b-32k")
    assert r["ok"]
    assert r["left"]["reply"] == "有本体回复"
    assert r["right"]["reply"] == "无本体回复"
    assert r["left"]["context"]["use_ontology"] is True
    assert r["right"]["context"]["use_ontology"] is False
    assert r["left"]["context"]["injected_ontology"] == 1
    assert r["right"]["context"]["injected_ontology"] == 0
    # the left side carries the exact sent messages (system + user)
    assert [m["role"] for m in r["left"]["context"]["sent"]] == ["system", "user"]
    # compare does NOT persist to history
    assert conn.execute("SELECT COUNT(*) c FROM chat_messages").fetchone()["c"] == 0


def test_compare_unknown_identity(env, fake_llm):
    from app import chat
    r = chat.compare(env, 99999, "你好")
    assert not r["ok"] and "不存在" in r["error"]


def test_compare_default_ollama_model(env, fake_llm):
    """With no explicit model, compare resolves the 32k default."""
    from app import chat
    conn = env
    iid = _insert_identity(conn)

    def ollama(messages):
        return "x"

    fake_llm["ollama"] = ollama
    r = chat.compare(conn, iid, "你好", provider="ollama")
    assert r["ok"]
    assert r["left"]["context"]["model"] == chat.DEFAULT_OLLAMA_MODEL
    assert r["left"]["context"]["model"] == "qwen2.5:7b-32k"


def test_estimate_tokens():
    from app import chat
    assert chat._estimate_tokens("你好世界") == 4       # 4 CJK chars
    assert chat._estimate_tokens("hello world") == 3    # 11 ascii chars -> 3
    assert chat._estimate_tokens("") == 0
