# -*- coding: utf-8 -*-
"""Settings: persisted at data/settings.json (git-ignored, holds token).

Shape:
{
  "work_dir": "D:/docs",
  "llm": {"base_url": "...", "api_key": "...", "model": "deepseek-v4-flash"},
  "llm2": {"base_url": "...", "api_key": "...", "model": "glm-5.2"},
  "embedding": {"provider": "ollama|hash", "base_url": "http://localhost:11434",
                 "model": "bge-m3", "dim": 1024}
}

llm = 生成器（V4-Flash，闭卷作答/提名）；llm2 = 判别器（GLM 5.2，
开卷判别）——异源交叉核验，判别结果仍只是提名，确定性代码终审。
"""
import json
import os
import re
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULTS = {
    "work_dir": "",
    "llm": {"base_url": "", "api_key": "", "model": "deepseek-v4-flash",
            "timeout": 90, "hard_timeout": 1800},
    "llm2": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key": "",
             "model": "glm-5.2", "timeout": 90, "hard_timeout": 1800},
    "embedding": {"provider": "hash", "base_url": "http://localhost:11434",
                  "model": "bge-m3", "dim": 1024},
    # 主 LLM 提供方: "cloud"=走 V4-Flash(云), "local"=走 WSL2 Ollama 本地。
    # 切换后 /api/chat 不显式传 provider 时按此 mode 解析 (main.persona_chat)。
    "llm_mode": "cloud",
    # 本地 LLM 配置（仅在 llm_mode == "local" 时生效）
    "local_llm": {"base_url": "http://localhost:11434",
                  "model": "qwen2.5:7b-32k"},
}

_lock = threading.RLock()


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    with _lock:
        if not SETTINGS_PATH.exists():
            data = json.loads(json.dumps(DEFAULTS))
        else:
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = json.loads(json.dumps(DEFAULTS))
            data = _deep_merge(DEFAULTS, data)
    # Environment overrides (containerized deploy: backend talks to the `ollama`
    # / `pg` service names instead of localhost; work_dir points at a mounted volume)
    env_embed = os.environ.get("EMBEDDING_BASE_URL", "").strip()
    if env_embed:
        data.setdefault("embedding", {})["base_url"] = env_embed
    env_work = os.environ.get("RAG_WORK_DIR", "").strip()
    if env_work:
        data["work_dir"] = env_work
    return data


def save_settings(patch: dict) -> dict:
    """Deep-merge patch onto current settings, persist, return new settings.

    A masked api_key (from GET /api/settings) round-tripped back here must
    NOT overwrite the real stored key - otherwise the '*' string becomes the
    credential and every LLM call fails auth (regression: 2026-08-30 401)."""
    with _lock:
        current = load_settings()
        patch = json.loads(json.dumps(patch or {}))
        for block in ("llm", "llm2"):
            key = (patch.get(block) or {}).get("api_key")
            if isinstance(key, str) and "*" in key:
                patch[block].pop("api_key", None)  # masked value = "no change"
        merged = _deep_merge(current, patch)
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        return merged


def mask_settings(settings: dict) -> dict:
    """API-safe view: hide the raw token."""
    out = json.loads(json.dumps(settings))
    for block in ("llm", "llm2"):
        key = out.get(block, {}).get("api_key", "")
        out[block]["api_key"] = _mask_secret(key)
    return out


def _mask_secret(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def validate_llm_config(llm: dict) -> list[str]:
    """Structure gate for the settings form."""
    errors = []
    base_url = (llm or {}).get("base_url", "")
    if not base_url:
        errors.append("base_url is required")
    elif not re.match(r"^https?://", base_url):
        errors.append("base_url must start with http:// or https://")
    if not (llm or {}).get("model"):
        errors.append("model is required")
    if not (llm or {}).get("api_key"):
        errors.append("api_key is required")
    return errors


def validate_local_llm_config(local: dict) -> list[str]:
    """Structure gate for the local_llm block (WSL2 Ollama)."""
    errors = []
    base_url = (local or {}).get("base_url", "")
    if not base_url:
        errors.append("base_url is required")
    elif not re.match(r"^https?://", base_url):
        errors.append("base_url must start with http:// or https://")
    model = (local or {}).get("model", "")
    if not model:
        errors.append("model is required")
    return errors


def resolve_provider(settings: dict | None = None) -> tuple[str, str | None]:
    """Decide (provider, ollama_model) when the caller doesn't specify them
    explicitly. Pure data — no LLM / no network.

    "local" mode → ("ollama", settings.local_llm.model)
    "cloud" mode (default) → ("llm2", None)
    Always returns a tuple; never raises.
    """
    s = settings if settings is not None else load_settings()
    if s.get("llm_mode") == "local":
        local = s.get("local_llm") or {}
        m = (local.get("model") or "").strip()
        if m:
            return "ollama", m
    return "llm2", None
