# -*- coding: utf-8 -*-
"""Embedding: Ollama (bge-m3 in WSL2) with deterministic hash fallback.

Iron law 1: the embedding model is LOCKED from first ingest (stored in kv).
All vectors in one store must come from one model - comparable space.
"""
import hashlib
import json
import math
import urllib.request

from . import settings_store, db, netutil

HASH_DIM = 512
_fake_embed = None  # UT hook


def set_fake_embed(fn) -> None:
    global _fake_embed
    _fake_embed = fn


def clear_fake_embed() -> None:
    global _fake_embed
    _fake_embed = None


def _hash_embed(text: str, dim: int = HASH_DIM) -> list[float]:
    vec = [0.0] * dim
    if not text:
        return vec
    for n in (1, 2, 3):
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            vec[h % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _ollama_embed(text: str, base_url: str, model: str) -> list[float]:
    # loopback call: bypass the Windows/registry proxy (see app/netutil.py)
    req = netutil.local_request(
        f"{base_url.rstrip('/')}/api/embeddings",
        data=json.dumps({"model": model, "prompt": text}).encode("utf-8"))
    with netutil.local_urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["embedding"]


def active_provider() -> str:
    """Resolve provider: ollama if configured & reachable else hash."""
    if _fake_embed is not None:
        return "fake"
    s = settings_store.load_settings()["embedding"]
    if s.get("provider") == "ollama":
        return "ollama"
    return "hash"


def embed(text: str) -> list[float]:
    """Unified entry. Ollama failure falls back to hash (chain stays alive).

    The hash fallback MUST use the same configured dim as the primary
    provider (settings `dim`, e.g. 1024 for bge-m3), NOT the fixed 512
    default: pgvector columns are dimension-free here, so mixing a 512-dim
    fallback vector into a store of 1024-dim vectors breaks every `<=>`
    query with "different vector dimensions 512 and 1024"."""
    if _fake_embed is not None:
        return _fake_embed(text)
    s = settings_store.load_settings()["embedding"]
    if s.get("provider") == "ollama":
        try:
            return _ollama_embed(text, s["base_url"], s["model"])
        except Exception:
            pass
    dim = int(s.get("dim") or HASH_DIM)
    return _hash_embed(text, dim)


def check_ollama(base_url: str, model: str) -> dict:
    """Connectivity + model presence probe for the settings page."""
    try:
        with netutil.local_urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        has_model = any(m == model or m.split(":")[0] == model for m in models)
        return {"ok": True, "models": models, "has_model": has_model}
    except Exception as e:
        return {"ok": False, "models": [], "has_model": False, "error": str(e)}


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def assert_model_lock(conn) -> None:
    """Iron law 1 enforcement: first ingest locks (provider, model);
    later ingests must use the same one, else raise."""
    s = settings_store.load_settings()["embedding"]
    current = {"provider": active_provider(), "model": s["model"]}
    locked = db.kv_get(conn, "embedding_lock")
    if locked is None:
        db.kv_set(conn, "embedding_lock", current)
        return
    if locked != current:
        raise RuntimeError(
            f"embedding model locked to {locked}, refusing to embed with {current}. "
            "Switching requires a full re-embed job.")


def default_to_reachable_ollama(conn) -> bool:
    """Called by start.ps1 when localhost:11434 answers (host Ollama first).

    Flip the untouched 'hash' default to 'ollama' so the app actually uses the
    Ollama the user already has. Iron law 1 guard: if existing vectors were
    embedded with a different provider (lock present), refuse - a real switch
    needs a deliberate re-embed job, not a silent one on startup."""
    s = settings_store.load_settings()["embedding"]
    if s.get("provider") != "hash":
        return False  # user already made an explicit choice
    locked = db.kv_get(conn, "embedding_lock")
    if locked and locked.get("provider") != "ollama":
        return False
    ok = check_ollama(s.get("base_url", "http://localhost:11434"), s.get("model", "bge-m3"))
    if not ok.get("ok") or not ok.get("has_model"):
        return False
    settings_store.save_settings({"embedding": {"provider": "ollama"}})
    return True
