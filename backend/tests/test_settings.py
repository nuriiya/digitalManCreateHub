# -*- coding: utf-8 -*-
"""Settings store tests: persistence, deep merge, masking, validation."""


def test_defaults_when_missing(env):
    from app import settings_store
    s = settings_store.load_settings()
    assert s["llm"]["model"] == "deepseek-v4-flash"
    assert s["embedding"]["provider"] == "hash"


def test_save_and_deep_merge(env):
    from app import settings_store
    settings_store.save_settings({"llm": {"api_key": "sk-1234567890abcdef"}})
    s = settings_store.load_settings()
    # patch only touched api_key; other llm fields keep defaults
    assert s["llm"]["api_key"] == "sk-1234567890abcdef"
    assert s["llm"]["model"] == "deepseek-v4-flash"

    settings_store.save_settings({"work_dir": "D:/docs"})
    s2 = settings_store.load_settings()
    assert s2["work_dir"] == "D:/docs"
    assert s2["llm"]["api_key"] == "sk-1234567890abcdef"  # survived


def test_mask_hides_token_middle(env):
    from app import settings_store
    settings_store.save_settings({"llm": {"api_key": "sk-abcdefgh12345678"}})
    masked = settings_store.mask_settings(settings_store.load_settings())
    assert "abcdefgh" not in masked["llm"]["api_key"]
    assert masked["llm"]["api_key"].startswith("sk-a")
    assert masked["llm"]["api_key"].endswith("5678")


def test_masked_key_roundtrip_does_not_overwrite_real_key(env):
    """Regression (2026-08-30): settings page saves the *masked* value it got
    from GET /api/settings back via PUT - the '*' string must be ignored,
    keeping the real key, not becoming the credential (caused 401)."""
    from app import settings_store
    settings_store.save_settings({"llm": {"base_url": "https://api.deepseek.com",
                                          "api_key": "sk-real-key-1234",
                                          "model": "deepseek-v4-flash"}})
    masked_view = settings_store.mask_settings(settings_store.load_settings())
    # user opens settings page, changes nothing in the token field, hits save
    settings_store.save_settings(masked_view)
    s = settings_store.load_settings()
    assert s["llm"]["api_key"] == "sk-real-key-1234"  # real key survived
    assert "*" not in s["llm"]["api_key"]


def test_validate_llm_config(env):
    from app import settings_store
    errs = settings_store.validate_llm_config({})
    assert any("base_url" in e for e in errs)
    errs2 = settings_store.validate_llm_config(
        {"base_url": "ftp://x", "api_key": "k", "model": "m"})
    assert any("http" in e for e in errs2)
    assert settings_store.validate_llm_config(
        {"base_url": "https://api.example.com/v1", "api_key": "k", "model": "m"}) == []
