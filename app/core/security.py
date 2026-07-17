"""Password hashing (bcrypt) and JWT encode/decode.

Tokens carry a `type` claim (`access` | `refresh`). `decode_token` rejects a
token whose type doesn't match `expected_type`, so a stolen refresh token can't
be used against normal endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

settings = get_settings()

TokenType = Literal["access", "refresh", "reset"]


# ---- Passwords -----------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    ).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False


# ---- JWT -----------------------------------------------------------------
def _create_token(
    subject: str,
    token_type: TokenType,
    ttl_minutes: int,
    claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": str(uuid.uuid4()),
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    # Identity claims only ride on the access token.
    if token_type == "access" and claims:
        payload.update(claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str, claims: dict[str, Any] | None = None) -> str:
    return _create_token(subject, "access", settings.ACCESS_TOKEN_TTL_MINUTES, claims)


def create_refresh_token(subject: str) -> str:
    return _create_token(subject, "refresh", settings.REFRESH_TOKEN_TTL_MINUTES)


def create_reset_token(subject: str) -> str:
    # Short-lived, identity-free (like refresh): the subject is enough to reset.
    return _create_token(subject, "reset", settings.RESET_TOKEN_TTL_MINUTES)


def decode_token(
    token: str, expected_type: TokenType | None = "access"
) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={
                "verify_aud": settings.JWT_AUDIENCE is not None,
                "verify_iss": settings.JWT_ISSUER is not None,
            },
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired token.") from exc

    if expected_type and payload.get("type") != expected_type:
        raise UnauthorizedError(f"Expected a {expected_type} token.")
    return payload
