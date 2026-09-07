"""Tests for the new "WSL2 Ollama local LLM" settings surface.

Auth: the FastAPI app gates non-/api/auth/login endpoints behind a Bearer
token (commit 2e56847). Tests trigger the lifespan (which seeds admin), then
login as admin/123456 and inject the token on every request. We do NOT spin
the full `env` PG-schema fixture; only the lifespan-seeded default PG schema
is used, since these tests touch settings.json (file-scoped) only.

All tests monkeypatch settings_store.SETTINGS_PATH to a sandboxed file, so
they MUST NOT pollute the real backend/data/settings.json.
"""
import json
import pathlib
import tempfile
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sandbox_settings(monkeypatch):
    """Redirect settings_store to a tmp file; restore on teardown."""
    from app import settings_store
    tmp = pathlib.Path(tempfile.mkdtemp())
    spath = tmp / "settings.json"
    spath.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", spath)
    return spath


def _authed_client(app):
    """Log in once, return a shim TestClient that stamps Bearer on every call."""
    inner = TestClient(app)
    inner.__enter__()
    r = inner.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
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
        def __getattr__(self, name):
            return getattr(inner, name)
    return C()


@pytest.fixture
def client(monkeypatch, sandbox_settings):
    """Authenticated TestClient with sandboxed settings.json + stubbed Ollama probe."""
    from app import embedding
    from app import main as app_main
    monkeypatch.setattr(embedding, "check_ollama",
                        lambda base, model: {"ok": False, "models": [], "has_model": False,
                                             "error": "test stub: ollama offline"})
    return _authed_client(app_main.app)


# ---------- settings_store pure-data tests (sandboxed) ----------

def test_default_load_returns_new_keys(sandbox_settings):
    """Even an empty settings.json must surface llm_mode and local_llm."""
    from app import settings_store
    s = settings_store.load_settings()
    assert s["llm_mode"] == "cloud"
    assert "local_llm" in s
    assert s["local_llm"]["base_url"].startswith("http")
    assert s["local_llm"]["model"]


def test_validate_local_llm_structure():
    from app import settings_store
    errs = settings_store.validate_local_llm_config({"base_url": "", "model": "q"})
    assert any("base_url" in e for e in errs)
    errs = settings_store.validate_local_llm_config({"base_url": "ftp://x", "model": "q"})
    assert any("http://" in e for e in errs)
    errs = settings_store.validate_local_llm_config({"base_url": "http://x", "model": ""})
    assert any("model" in e for e in errs)
    errs = settings_store.validate_local_llm_config(
        {"base_url": "http://localhost:11434", "model": "qwen2.5:7b-32k"}
    )
    assert errs == []


def test_resolve_provider_toggles():
    from app import settings_store
    prov, om = settings_store.resolve_provider({"llm_mode": "cloud",
                                                "local_llm": {"model": "q"}})
    assert prov == "llm2" and om is None
    prov, om = settings_store.resolve_provider({"llm_mode": "local",
                                                "local_llm": {"model": "qwen2.5:7b-32k"}})
    assert prov == "ollama" and om == "qwen2.5:7b-32k"
    prov, om = settings_store.resolve_provider({"llm_mode": "local",
                                                "local_llm": {"model": ""}})
    assert prov == "llm2" and om is None


def test_llm_mode_round_trips_via_save_settings(sandbox_settings):
    from app import settings_store
    settings_store.save_settings({"llm_mode": "local",
                                  "local_llm": {"base_url": "http://x:11434",
                                                 "model": "qwen2.5:32k"}})
    s = settings_store.load_settings()
    assert s["llm_mode"] == "local"
    assert s["local_llm"]["model"] == "qwen2.5:32k"


# ---------- HTTP endpoint tests ----------

def test_api_get_ollama_models_graceful_when_offline(client):
    r = client.get("/api/ollama/models")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is False
    assert "models" in d
    assert "selected" in d
    assert "selected_installed" in d
    assert d["selected_installed"] is False


def test_api_get_ollama_models_installed_set(client, monkeypatch, sandbox_settings):
    from app import embedding
    from app import settings_store
    from app import main as app_main
    settings_store.save_settings({
        "local_llm": {"base_url": "http://localhost:11434", "model": "qwen2.5:7b-32k"}
    })
    monkeypatch.setattr(embedding, "check_ollama",
                        lambda base, model: {"ok": True, "models": ["qwen2.5:7b-32k", "phi3:mini"],
                                              "has_model": True, "error": None})
    r = client.get("/api/ollama/models")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "qwen2.5:7b-32k" in d["models"]
    assert d["selected"] == "qwen2.5:7b-32k"
    assert d["selected_installed"] is True


def test_api_pull_status_initial_state(client):
    r = client.get("/api/ollama/pull-status")
    assert r.status_code == 200
    d = r.json()
    assert d["state"] in ("idle", "done", "error")


def test_api_put_local_llm_round_trip(client, sandbox_settings):
    r = client.put("/api/settings/local-llm",
                   json={"base_url": "http://localhost:11434", "model": "qwen2.5:7b-32k"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["local_llm"]["model"] == "qwen2.5:7b-32k"


def test_api_put_local_llm_rejects_bad_shape(client):
    r = client.put("/api/settings/local-llm", json={"base_url": "ftp://x", "model": ""})
    assert r.status_code == 400
    assert "error" in r.json()


def test_api_put_llm_mode_toggles(client, sandbox_settings):
    r = client.put("/api/settings/llm-mode", json={"mode": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"
    r = client.put("/api/settings/llm-mode", json={"mode": "bogus"})
    assert r.status_code == 400
    r = client.put("/api/settings/llm-mode", json={"mode": "cloud"})
    assert r.status_code == 200
    assert r.json()["mode"] == "cloud"


def test_api_pull_rejects_when_already_running(client, monkeypatch):
    from app import main as app_main
    with app_main._PULL_LOCK:
        app_main._OLLAMA_PULL_STATE.update({"state": "running", "model": "phi3:mini"})
    try:
        r = client.post("/api/ollama/pull", json={"model": "llama3:8b"})
        assert r.status_code == 409
    finally:
        with app_main._PULL_LOCK:
            app_main._OLLAMA_PULL_STATE.update({"state": "idle", "model": ""})


def test_pull_rejects_empty_model(client):
    r = client.post("/api/ollama/pull", json={"model": "  "})
    assert r.status_code == 400
    assert "error" in r.json()
