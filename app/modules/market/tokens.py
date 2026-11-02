"""Signed link for "Ne plus recevoir la veille marché": the e-mail carries a
token naming the user, so one click is enough (no login). Same scheme as the
announcements' link (monitoring/tokens.py), with its own token type so a link
from one list can never unsubscribe from the other."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.core.exceptions import BadRequestError

_TYPE = "market_digest_unsubscribe"


def create_unsubscribe_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "type": _TYPE,
            "iat": now,
            "exp": now + timedelta(days=settings.MONITORING_UNSUBSCRIBE_TOKEN_DAYS),
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def read_unsubscribe_token(token: str) -> uuid.UUID:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False},
        )
        if payload.get("type") != _TYPE:
            raise ValueError("wrong token type")
        return uuid.UUID(payload["sub"])
    except Exception as exc:
        raise BadRequestError(
            "Ce lien n'est pas valide ou a expiré.", code="invalid_unsubscribe_link"
        ) from exc


def unsubscribe_link(user_id: uuid.UUID) -> str:
    base = get_settings().APP_URL.rstrip("/")
    return (
        f"{base}/veille-marche/desinscription?token={create_unsubscribe_token(user_id)}"
    )
