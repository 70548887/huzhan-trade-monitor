"""Single-administrator authentication with persistent sessions and throttling."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

from . import db

PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60
ATTEMPT_WINDOW_SECONDS = 15 * 60
ATTEMPT_LIMIT = 5
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.@-]{3,64}\Z", re.ASCII)
_DUMMY_SALT = b"huzhan-monitor-unknown-user-salt"
_DUMMY_HASH = bytes(32)

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_admin (
    id INTEGER PRIMARY KEY CHECK (id=1),
    username TEXT NOT NULL UNIQUE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES auth_admin(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_attempts (
    client_hash TEXT PRIMARY KEY,
    window_started INTEGER NOT NULL,
    attempts INTEGER NOT NULL
);
"""


class AdminAlreadyConfigured(ValueError):
    """Replacing the existing administrator requires an explicit reset."""


def initialize() -> None:
    """Create authentication tables without changing existing accounts."""
    now = int(time.time())
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
        conn.execute(
            "DELETE FROM auth_attempts WHERE window_started<=?",
            (now - ATTEMPT_WINDOW_SECONDS,),
        )


def is_configured() -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM auth_admin WHERE id=1").fetchone() is not None


def _validate_credentials(username: str, password: str) -> None:
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        raise ValueError("用户名需为 3–64 位英文字母、数字或 _ . @ -")
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError("密码长度需为 12–1024 个字符")


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS,
    )


def set_admin(username: str, password: str, reset: bool = False) -> None:
    """Create the sole administrator; an explicit reset invalidates every session."""
    username = username.strip() if isinstance(username, str) else username
    _validate_credentials(username, password)
    salt = secrets.token_bytes(32)
    password_hash = _password_hash(password, salt)
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute("SELECT 1 FROM auth_admin WHERE id=1").fetchone()
        if exists and not reset:
            raise AdminAlreadyConfigured("管理员已存在；确认重置时请添加 --reset")
        conn.execute(
            "INSERT INTO auth_admin(id,username,password_salt,password_hash,updated_at) "
            "VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "username=excluded.username,password_salt=excluded.password_salt,"
            "password_hash=excluded.password_hash,updated_at=excluded.updated_at",
            (username, salt, password_hash, int(time.time())),
        )
        conn.execute("DELETE FROM auth_sessions")
        conn.execute("DELETE FROM auth_attempts")


def bootstrap_from_environment() -> None:
    """Use deployment credentials only while no administrator exists."""
    if is_configured():
        return
    password = os.environ.get("HUZHAN_ADMIN_PASSWORD")
    if not password:
        return
    username = os.environ.get("HUZHAN_ADMIN_USERNAME", "admin")
    try:
        set_admin(username, password)
    except AdminAlreadyConfigured:
        # Another web worker may have completed the first-time bootstrap.
        return


def _token_hash(token: str | None) -> str | None:
    if not isinstance(token, str) or not 32 <= len(token) <= 128:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(username: str, password: str, client_id: str) -> dict:
    """Reserve an attempt atomically before verifying credentials."""
    now = int(time.time())
    client_hash = hashlib.sha256(str(client_id).encode("utf-8")).hexdigest()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        admin = conn.execute("SELECT * FROM auth_admin WHERE id=1").fetchone()
        if admin is None:
            return {"status": "unconfigured"}
        conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
        conn.execute(
            "DELETE FROM auth_attempts WHERE window_started<=?",
            (now - ATTEMPT_WINDOW_SECONDS,),
        )
        attempt = conn.execute(
            "SELECT * FROM auth_attempts WHERE client_hash=?", (client_hash,),
        ).fetchone()
        if attempt is not None and attempt["attempts"] >= ATTEMPT_LIMIT:
            return {"status": "throttled"}
        conn.execute(
            "INSERT INTO auth_attempts(client_hash,window_started,attempts) VALUES(?,?,1) "
            "ON CONFLICT(client_hash) DO UPDATE SET attempts=attempts+1",
            (client_hash, now),
        )
        snapshot = dict(admin)

    # A missing username still does a full password hash. Oversized input is
    # bounded before hashing so a request cannot cause unbounded work.
    normalized_name = username.strip() if isinstance(username, str) else ""
    valid_shape = (
        isinstance(password, str) and 12 <= len(password) <= 1024
        and bool(USERNAME_PATTERN.fullmatch(normalized_name))
    )
    known_name = valid_shape and hmac.compare_digest(
        normalized_name.encode("utf-8"), snapshot["username"].encode("utf-8"),
    )
    candidate = _password_hash(
        password if valid_shape else "invalid-password-input",
        snapshot["password_salt"] if known_name else _DUMMY_SALT,
    )
    expected = snapshot["password_hash"] if known_name else _DUMMY_HASH
    if not (hmac.compare_digest(candidate, expected) and known_name):
        return {"status": "invalid"}

    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    issued_at = int(time.time())
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT password_hash FROM auth_admin WHERE id=1").fetchone()
        if current is None or not hmac.compare_digest(
            current["password_hash"], snapshot["password_hash"],
        ):
            # Do not create a session from credentials reset during hashing.
            return {"status": "invalid"}
        conn.execute(
            "INSERT INTO auth_sessions(token_hash,admin_id,csrf_token,created_at,expires_at) "
            "VALUES(?,1,?,?,?)",
            (_token_hash(token), csrf_token, issued_at, issued_at + SESSION_SECONDS),
        )
        conn.execute("DELETE FROM auth_attempts WHERE client_hash=?", (client_hash,))
    return {"status": "ok", "token": token}


def get_session(token: str | None) -> dict | None:
    token_hash = _token_hash(token)
    if token_hash is None:
        return None
    now = int(time.time())
    with db.connect() as conn:
        row = conn.execute(
            "SELECT a.username,s.csrf_token,s.expires_at FROM auth_sessions s "
            "JOIN auth_admin a ON a.id=s.admin_id WHERE s.token_hash=?",
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
            return None
        return {"username": row["username"], "csrf_token": row["csrf_token"]}


def logout(token: str | None) -> None:
    token_hash = _token_hash(token)
    if token_hash is not None:
        with db.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
