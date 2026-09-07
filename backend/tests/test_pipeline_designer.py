"""Tests for the "Pipeline 创建工程师" platform persona (id=6, category=general).

Two layers:
1. _design_pipeline_via_llm: pure function, mock llm.chat to verify JSON parsing
   across well-formed / markdown-fenced / broken payloads.
2. POST /api/pipelines: HTTP endpoint via TestClient + Bearer auth (mirrors the
   test_local_llm_settings pattern). Verifies the description-empty vs
   description-non-empty paths diverge correctly.
"""
import json
import pytest
from fastapi.testclient import TestClient


def _authed_client(app):
    """Lifespan + login as admin/123456, return shim TestClient stamping Bearer."""
    inner = TestClient(app)
    inner.__enter__()
    r = inner.post("/api/auth/login",
                   json={"username": "admin", "password": "123456"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    class C:
        def get(self, *a, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return inner.get(*a, **kw)
        def post(self, *a, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return inner.post(*a, **kw)
        def put(self, *a, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return inner.put(*a, **kw)
        def delete(self, *a, **kw):
            kw.setdefault("headers", {})["Authorization"] = f"Bearer {token}"
            return inner.delete(*a, **kw)
        def __getattr__(self, name):
            return getattr(inner, name)
    return C()


@pytest.fixture
def client(monkeypatch):
    """Authenticated TestClient + stubbed Ollama/embedding."""
    from app import settings_store, embedding
    import pathlib, tempfile

    tmp = pathlib.Path(tempfile.mkdtemp())
    spath = tmp / "settings.json"
    spath.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", spath)
    monkeypatch.setattr(embedding, "check_ollama",
                        lambda base, model: {"ok": False, "models": [],
                                              "has_model": False,
                                              "error": "stub: offline"})

    from app import main as app_main
    return _authed_client(app_main.app)


# ---------- _design_pipeline_via_llm pure tests ----------

def test_designer_parses_well_formed_json():
    from app import main as m
    payload = {
        "name": "调研demo",
        "tags": ["调研", "demo"],
        "description": "调研后写demo",
        "nodes": [
            {"node_key": "n1", "kind": "nominate", "step_name": "调研主题",
             "persona_query": "需要学术调研能力"},
            {"node_key": "n2", "kind": "deterministic", "step_name": "建数字人"},
        ],
        "relations": [
            {"from": "n1", "to": "n2", "relation_type": "supply",
             "handoff_type": "领域文献", "handoff_schema": "doc_refs[]"},
        ],
    }
    captured = {}
    def _fake_chat(messages, **kw):
        captured["sys"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return json.dumps(payload, ensure_ascii=False)
    import app.main as app_main
    orig = app_main.llm.chat
    app_main.llm.chat = _fake_chat
    try:
        design = m._design_pipeline_via_llm(
            "调研demo", "调研FMEA文献并写demo", ["调研"], [])
    finally:
        app_main.llm.chat = orig
    assert design == payload
    assert "Pipeline 创建工程师" in captured["sys"]
    assert "用户填写的需求描述" in captured["user"]


def test_designer_strips_markdown_fence():
    from app import main as m
    payload = {"name": "x", "tags": [], "nodes": [], "relations": []}
    raw = "```json\n" + json.dumps(payload) + "\n```"
    import app.main as app_main
    orig = app_main.llm.chat
    app_main.llm.chat = lambda msgs, **kw: raw
    try:
        d = m._design_pipeline_via_llm("x", "y", [], [])
    finally:
        app_main.llm.chat = orig
    assert d == payload


def test_designer_returns_none_on_broken_json():
    from app import main as m
    import app.main as app_main
    orig = app_main.llm.chat
    app_main.llm.chat = lambda msgs, **kw: "not json at all, sorry"
    try:
        d = m._design_pipeline_via_llm("x", "y", [], [])
    finally:
        app_main.llm.chat = orig
    assert d is None


def test_designer_returns_none_when_llm_raises():
    from app import main as m
    import app.main as app_main
    orig = app_main.llm.chat
    def boom(msgs, **kw): raise RuntimeError("network down")
    app_main.llm.chat = boom
    try:
        d = m._design_pipeline_via_llm("x", "y", [], [])
    finally:
        app_main.llm.chat = orig
    assert d is None


# ---------- POST /api/pipelines HTTP integration ----------

def test_create_pipeline_empty_desc_creates_empty_pipeline(client):
    """description 空 → 直接建空 pipeline，不调 LLM。"""
    import app.main as app_main
    called = {"n": 0}
    orig = app_main.llm.chat
    def track(msgs, **kw):
        called["n"] += 1
        return "{}"
    app_main.llm.chat = track
    try:
        r = client.post("/api/pipelines",
                        json={"name": "手工空 pipeline",
                              "description": "",
                              "tags": ["test"]})
    finally:
        app_main.llm.chat = orig
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int)
    assert called["n"] == 0
    pr = client.get(f"/api/pipelines/{body['id']}")
    assert pr.status_code == 200
    assert pr.json()["pipeline"]["nodes"] == []
    assert pr.json()["pipeline"]["relations"] == []


def test_create_pipeline_with_desc_invokes_designer_and_inserts_nodes(client):
    """description 非空 → 调 LLM 生成节点 + 关系草案。"""
    design = {
        "name": "调研FMEA并写报告",
        "tags": ["FMEA", "调研"],
        "description": "用户给的描述",
        "nodes": [
            {"node_key": "n1", "kind": "nominate", "step_name": "调研FMEA文献",
             "persona_query": "学术调研"},
            {"node_key": "n2", "kind": "deterministic", "step_name": "建数字人"},
            {"node_key": "n3", "kind": "nominate", "step_name": "写报告",
             "persona_query": "技术写作者"},
        ],
        "relations": [
            {"from": "n1", "to": "n2", "relation_type": "supply",
             "handoff_type": "领域文献", "handoff_schema": "doc_refs[]"},
            {"from": "n2", "to": "n3", "relation_type": "handoff",
             "handoff_type": "数字人本体", "handoff_schema": "persona_pkg"},
        ],
    }
    import app.main as app_main
    orig = app_main.llm.chat
    app_main.llm.chat = lambda msgs, **kw: json.dumps(design, ensure_ascii=False)
    try:
        r = client.post("/api/pipelines",
                        json={"name": "调研FMEA并写报告",
                              "description": "先调研FMEA文献再建一个FMEA数字人，最后让它写报告",
                              "tags": ["FMEA", "调研"]})
    finally:
        app_main.llm.chat = orig
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body.get("note") in (None, "", "")
    pid = body["id"]
    pr = client.get(f"/api/pipelines/{pid}").json()["pipeline"]
    assert len(pr["nodes"]) == 3
    assert len(pr["relations"]) == 2
    node_keys = [n["node_key"] for n in pr["nodes"]]
    assert node_keys == ["n1", "n2", "n3"]
    assert all(n["persona_id"] is None for n in pr["nodes"])
    assert {n["kind"] for n in pr["nodes"]} <= {"nominate", "deterministic"}
    rel_types = [r["relation_type"] for r in pr["relations"]]
    assert "supply" in rel_types
    assert "handoff" in rel_types


def test_create_pipeline_llm_garbage_still_creates_empty_with_note(client):
    """LLM 输出非 JSON → 仍创建空 pipeline + 返回 note 让用户手动编辑。"""
    import app.main as app_main
    orig = app_main.llm.chat
    app_main.llm.chat = lambda msgs, **kw: "我无法输出结构化数据"
    try:
        r = client.post("/api/pipelines",
                        json={"name": "test_designer_fail",
                              "description": "用户给了描述但设计师抽风了",
                              "tags": []})
    finally:
        app_main.llm.chat = orig
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "Pipeline 创建工程师未能解析" in body.get("note", "")
    pid = body["id"]
    pr = client.get(f"/api/pipelines/{pid}").json()["pipeline"]
    assert pr["nodes"] == []
    assert pr["relations"] == []


def test_designer_persona_exists_and_is_general():
    """「Pipeline 创建工程师」必须以 category=general 入驻。"""
    from app import db
    row = db.get_conn().execute(
        "SELECT id, name, category, status FROM identities"
        " WHERE name='Pipeline 创建工程师' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None, "Pipeline 创建工程师 尚未植入"
    assert row["category"] == "general"
    assert row["status"] == "approved"