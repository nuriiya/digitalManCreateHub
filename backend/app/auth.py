# -*- coding: utf-8 -*-
"""Login / auth: users in PG, HMAC-signed bearer tokens, bcrypt hashing.

Token layout: base64url(username.role.exp.sig). The signature is keyed with a
persistent random secret stored at data/auth_secret (generated once on first
startup). Stateless — no server-side session table for the MVP; logout is a
client-side token drop.

Passwords are bcrypt-hashed; the plain default password never touches disk.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from pathlib import Path

from . import db

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SECRET_PATH = DATA_DIR / "auth_secret"

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "123456"
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days

_lock = threading.RLock()


def _secret() -> bytes:
    with _lock:
        if SECRET_PATH.exists():
            s = SECRET_PATH.read_bytes()
            if s:
                return s
        s = secrets.token_bytes(32)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_bytes(s)
        return s


def hash_password(pw: str) -> str:
    import bcrypt
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def ensure_admin(conn) -> None:
    """Seed the admin account (default password) if the users table is empty."""
    row = conn.execute("SELECT id FROM users WHERE username=?",
                       (DEFAULT_ADMIN_USER,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at)"
            " VALUES(?,?,?,?)",
            (DEFAULT_ADMIN_USER, hash_password(DEFAULT_ADMIN_PASSWORD),
             "admin", db.now()))
        conn.commit()


def issue_token(username: str, role: str) -> str:
    exp = int(time.time()) + TOKEN_TTL
    body = f"{username}.{role}.{exp}"
    sig = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{body}.{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def verify_token(token: str):
    """Return (username, role) when the token is valid, else None."""
    if not token:
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8")
        body, sig = raw.rsplit(".", 1)
        username, role, exp = body.split(".", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    if int(exp) < time.time():
        return None
    return username, role


def authenticate(conn, username: str, password: str):
    """Return the user row when credentials are valid, else None."""
    row = conn.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username=?",
        (username.strip(),)).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return row


def change_password(conn, username: str, old_pw: str, new_pw: str) -> bool:
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username=?",
        (username,)).fetchone()
    if not row or not verify_password(old_pw, row["password_hash"]):
        return False
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (hash_password(new_pw), row["id"]))
    conn.commit()
    return True
