"""Auth transactional emails (verification, password reset). Wording lives in
`templates/mail/<name>/<locale>/`."""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.modules.mailer import RenderedMail, render_mail

TEMPLATES = Path(__file__).parent / "templates"


def _link(path: str, token: str) -> str:
    base = get_settings().APP_URL.rstrip("/")
    return f"{base}{path}?token={token}"


def build_verification_email(locale: str, token: str) -> RenderedMail:
    return render_mail(
        TEMPLATES, "verify_email", locale, link=_link("/verify-email", token)
    )


def build_reset_email(locale: str, token: str) -> RenderedMail:
    settings = get_settings()
    return render_mail(
        TEMPLATES,
        "reset_password",
        locale,
        link=_link("/reset-password", token),
        expires=settings.RESET_TOKEN_TTL_MINUTES,
    )
