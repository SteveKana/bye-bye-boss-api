"""Acknowledgement mail sent when someone joins the waitlist.

The wording lives in `templates/mail/waitlist_ack/<locale>/` — edit those files
to change the copy, no Python change needed.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.modules.mailer import RenderedMail, render_mail

TEMPLATES = Path(__file__).parent / "templates"
TEMPLATE_NAME = "waitlist_ack"


def build_ack_email(locale: str) -> RenderedMail:
    return render_mail(TEMPLATES, TEMPLATE_NAME, locale)


def build_invitation_email(locale: str, link: str, days: int) -> RenderedMail:
    """Launch invitation: the account already exists, `link` lets its owner
    choose a password."""
    login_url = f"{get_settings().APP_URL.rstrip('/')}/login"
    return render_mail(
        TEMPLATES,
        "waitlist_invitation",
        locale,
        link=link,
        login_url=login_url,
        days=days,
    )
