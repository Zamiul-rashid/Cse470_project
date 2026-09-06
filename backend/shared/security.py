"""Password hashing and JWT issue/verify.

Uses the ``bcrypt`` package directly rather than passlib. passlib 1.7.4 reads
``bcrypt.__about__``, which bcrypt 4.1 removed -- the resulting AttributeError
is a classic "worked on my machine" failure on a fresh install, and the wrapper
it saves us is four lines.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.util import utcnow

TokenKind = Literal["access", "refresh"]

#: bcrypt silently truncates at 72 bytes; do it explicitly so a long password
#: and its 72-byte prefix are never accidentally equivalent.
_BCRYPT_MAX_BYTES = 72


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _create_token(subject: str, role: str, kind: TokenKind, expires: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "kind": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + expires).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_access_token(user_id: str, role: str) -> str:
    return _create_token(
        user_id, role, "access", timedelta(minutes=settings.access_token_minutes)
    )


def create_refresh_token(user_id: str, role: str) -> str:
    return _create_token(
        user_id, role, "refresh", timedelta(days=settings.refresh_token_days)
    )


def decode_token(token: str, expected_kind: TokenKind | None = None) -> dict[str, Any]:
    """Raises ``jwt.PyJWTError`` on anything wrong -- expiry, signature, kind.

    The ``kind`` check is what stops a refresh token being replayed as an
    access token, which would silently extend session life to 14 days.
    """
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    if expected_kind is not None and payload.get("kind") != expected_kind:
        raise jwt.InvalidTokenError(
            f"expected a {expected_kind} token, got {payload.get('kind')!r}"
        )
    return payload


# ---------------------------------------------------------------------------
# opaque token handles (refresh tokens, QR handoff tokens)
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    """Store the digest, never the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_payload(payload: str) -> str:
    """HMAC-SHA256 over an arbitrary string, keyed by the app secret."""
    return hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def refresh_token_expiry() -> datetime:
    return utcnow() + timedelta(days=settings.refresh_token_days)


__all__ = [
    "constant_time_equals",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_password",
    "hash_token",
    "refresh_token_expiry",
    "sign_payload",
    "verify_password",
]
