# -*- coding: utf-8 -*-
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect settings + sqlite into tmp_path, return a fresh connection."""
    from app import settings_store, db
    monkeypatch.setattr(settings_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "_conn", None)
    conn = db.get_conn()
    yield conn
    conn.close()
    monkeypatch.setattr(db, "_conn", None)


@pytest.fixture
def fake_llm(monkeypatch):
    """Deterministic LLM router. Tests set `router.reply = fn(messages)->str`.

    Default reply routes by prompt keyword: summary / aggregation / extraction.
    Override `router.reply` (or `router.extraction`) per test.
    The judge channel (llm2, GLM) defaults to `supported`; override via
    `router.judge` for A/B and fail-attribution scenarios.
    """
    from app import llm, settings_store

    # make llm_configured() True so the fake chat is actually routed
    settings_store.save_settings({"llm": {"base_url": "http://fake.local",
                                          "api_key": "fake-key", "model": "fake-model"}})
    settings_store.save_settings({"llm2": {"base_url": "http://fake2.local",
                                           "api_key": "fake-key2", "model": "fake-judge"}})

    router = {"reply": None, "extraction": None, "judge": None, "ollama": None}

    def _route(messages):
        prompt = messages[-1]["content"]
        if "本体提名器" in prompt:
            if router["extraction"]:
                return router["extraction"](prompt)
            return '{"entities": [], "relations": []}'
        if "概念抽取器" in prompt:
            if router.get("concepts"):
                return router["concepts"](prompt)
            return "[]"
        if router["reply"]:
            return router["reply"](messages)
        if "文档摘要助手" in prompt:
            return '{"summary": "测试段摘要", "tags": ["测试"]}'
        return "测试整篇摘要"

    def _route2(messages):
        if router["judge"]:
            return router["judge"](messages)
        return '{"verdict": "supported", "reason": "fake"}'  # default: pass

    def _route_ollama(messages):
        if router["ollama"]:
            return router["ollama"](messages)
        return "Ollama 回复"

    llm.set_fake_chat(_route)
    llm.set_fake_chat2(_route2)
    llm.set_fake_chat_ollama(_route_ollama)
    router["route"] = _route
    router["route2"] = _route2
    router["route_ollama"] = _route_ollama
    yield router
    llm.clear_fake_chat()


@pytest.fixture
def workdir(tmp_path):
    """A small demo work directory with one markdown file."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text(
        "《报销系统》是公司财务流程的核心系统。员工提交报销单后进入审批流程。"
        "预算超支时需要财务专员复核。", encoding="utf-8")
    return d
