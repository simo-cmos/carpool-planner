"""Organizer password and session management.

Protects admin access (localhost and the admin tunnel) once a password is
set. Guests never log in: their invite links stay token-based. Everything
uses the standard library — PBKDF2 for hashing, random tokens for sessions
persisted in app settings so logins survive a server restart.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from app.database import get_setting, set_setting
from core.logging_config import LOG_INFO

SESSION_COOKIE_NAME = "dmproject_session"
SESSION_TTL_DAYS = 30

_PBKDF2_ITERATIONS = 240_000
_PASSWORD_SETTING = "auth_password"
_SESSIONS_SETTING = "auth_sessions"


def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return digest.hex()


def is_password_set() -> bool:
    """Return True when an organizer password is configured."""
    record = get_setting(_PASSWORD_SETTING, None)
    return isinstance(record, dict) and bool(record.get("hash"))


def set_password(password: str) -> None:
    """Store a new organizer password and invalidate every existing session."""
    salt = secrets.token_bytes(16)
    set_setting(
        _PASSWORD_SETTING,
        {
            "salt": salt.hex(),
            "hash": _hash_password(password, salt, _PBKDF2_ITERATIONS),
            "iterations": _PBKDF2_ITERATIONS,
        },
    )
    set_setting(_SESSIONS_SETTING, {})
    LOG_INFO("organizer password updated — all sessions invalidated")


def clear_password() -> None:
    """Remove the organizer password and every session (open access again)."""
    set_setting(_PASSWORD_SETTING, None)
    set_setting(_SESSIONS_SETTING, {})
    LOG_INFO("organizer password removed — app is open again")


def verify_password(password: str) -> bool:
    """Check a password attempt against the stored hash in constant time."""
    record = get_setting(_PASSWORD_SETTING, None)
    if not isinstance(record, dict) or not record.get("hash"):
        return False
    salt = bytes.fromhex(record["salt"])
    iterations = int(record.get("iterations", _PBKDF2_ITERATIONS))
    attempt = _hash_password(password, salt, iterations)
    return hmac.compare_digest(attempt, record["hash"])


def _load_sessions() -> dict[str, str]:
    sessions = get_setting(_SESSIONS_SETTING, {})
    return sessions if isinstance(sessions, dict) else {}


def _prune_expired(sessions: dict[str, str]) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    kept = {}
    for token, expires_at in sessions.items():
        try:
            if datetime.fromisoformat(expires_at) > now:
                kept[token] = expires_at
        except ValueError:
            continue
    return kept


def create_session() -> str:
    """Create a new logged-in session and return its cookie token."""
    sessions = _prune_expired(_load_sessions())
    token = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    sessions[token] = expires_at.isoformat()
    set_setting(_SESSIONS_SETTING, sessions)
    return token


def is_valid_session(token: str) -> bool:
    """Return True when the cookie token belongs to an unexpired session."""
    if not token:
        return False
    sessions = _load_sessions()
    expires_at = sessions.get(token)
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def destroy_session(token: str) -> None:
    """Log one session out."""
    sessions = _prune_expired(_load_sessions())
    if token in sessions:
        del sessions[token]
    set_setting(_SESSIONS_SETTING, sessions)
