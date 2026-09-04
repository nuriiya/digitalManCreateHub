# -*- coding: utf-8 -*-
"""Auth: admin seed + token issue/verify + password change."""
from app import auth


def test_admin_seed_and_password(env):
    auth.ensure_admin(env)
    row = env.execute("SELECT username, role FROM users WHERE username='admin'").fetchone()
    assert row is not None
    assert row["role"] == "admin"
    # default password verifies, wrong one does not
    assert auth.authenticate(env, "admin", "Xyf.748159") is not None
    assert auth.authenticate(env, "admin", "wrong") is None


def test_admin_seed_idempotent(env):
    auth.ensure_admin(env)
    auth.ensure_admin(env)
    rows = env.execute("SELECT COUNT(*) c FROM users WHERE username='admin'").fetchone()
    assert rows["c"] == 1


def test_token_roundtrip_and_reject(env):
    t = auth.issue_token("admin", "admin")
    assert auth.verify_token(t) == ("admin", "admin")
    assert auth.verify_token("garbage") is None
    assert auth.verify_token("") is None


def test_change_password(env):
    auth.ensure_admin(env)
    assert auth.change_password(env, "admin", "wrong-old", "newpass123") is False
    assert auth.change_password(env, "admin", "Xyf.748159", "newpass123") is True
    assert auth.authenticate(env, "admin", "Xyf.748159") is None
    assert auth.authenticate(env, "admin", "newpass123") is not None
