# -*- coding: utf-8 -*-
import sys
import uuid
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pytest


# Per-test PG schema isolation: one connection pool per process, but every test
# runs inside its own namespace so writes from one test never leak into another
# (the old SQLite fixture relied on a fresh `app.db` file per test, which is
# not portable to PG). The schema name embeds a uuid so parallel test runs
# against the same database don't collide.

PG_TEST_DSN_ENV_KEYS = ("RAG_DATABASE_URL", "DATABASE_URL")


def _resolve_pg_dsn() -> str:
    """Resolve the test PG DSN from env (mirrors db._resolve_dsn priority).

    Falls back to the same DSN as the app so developers can point both at one
    dev database (the schema-prefixed isolation makes parallel writes safe).
    """
    import os
    for k in PG_TEST_DSN_ENV_KEYS:
        dsn = (os.environ.get(k) or "").strip()
        if dsn:
            return dsn
    # last resort: the same default the app uses
    from app import db
    try:
        return db._resolve_dsn()
    except Exception:
        return db.DEFAULT_DSN


@lru_cache(maxsize=1)
def _pg_reachable() -> bool:
    """True if the test PG DSN actually answers (cached for the whole run).

    Every test that needs the `env` fixture is skipped when PostgreSQL is not
    reachable — the sandbox/CI has no Docker/PG, so a connection refusal here
    is an environment gap, NOT a code bug. `pytest -rs` shows the reason.
    """
    try:
        import psycopg
        pg = psycopg.connect(_resolve_pg_dsn(), connect_timeout=3, autocommit=True)
    except Exception:
        return False
    try:
        with pg.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False
    finally:
        try:
            pg.close()
        except Exception:
            pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Per-test isolated PG connection. Sets search_path to a unique schema,
    runs the app's DDL there, and drops the schema on teardown. Returns a
    fresh shim _Conn against a dedicated psycopg3 connection (not the shared
    singleton) so it can be closed without disturbing the global state."""
    from app import settings_store, db
    from app.db import DSN_FILE
    import json as _json

    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达（本机请先 start.ps1 拉起 pgvector；"
                    "沙箱/CI 无 Docker/PG 属预期跳过）")

    import psycopg

    # isolate settings (still JSON, untouched by the DB migration)
    monkeypatch.setattr(settings_store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.json")

    dsn = _resolve_pg_dsn()
    schema = "test_" + uuid.uuid4().hex[:12]
    pg = psycopg.connect(dsn, autocommit=False)
    with pg.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        # Run the app DDL with the schema pinned. _SCHEMA_SQL is CREATE
        # EXTENSION + CREATE TABLE IF NOT EXISTS — both safe in a fresh
        # schema. CREATE EXTENSION in particular is a superuser-level grant;
        # if the test role lacks it we skip silently (vector stays NULL,
        # tests that need it explicitly assert the dim).
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass
        cur.execute(db._SCHEMA_SQL)
    pg.commit()

    # Build a shim Conn around THIS pg connection so the test sees only its
    # own tables and the cleanup at teardown only drops its own schema.
    from pgvector.psycopg import register_vector
    try:
        register_vector(pg)
    except Exception:
        pass

    conn = db._Conn(pg)

    yield conn

    conn.close()
    try:
        pg2 = psycopg.connect(dsn, autocommit=True)
        with pg2.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        pg2.close()
    except Exception:
        pass


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
