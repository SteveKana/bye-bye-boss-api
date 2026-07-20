"""Acknowledgement mail sent when someone joins the waitlist.

The wording lives in `templates/mail/waitlist_ack/<locale>/` — edit those files
to change the copy, no Python change needed.
"""

from __future__ import annotations

from pathlib import Path

from app.modules.mailer import RenderedMail, render_mail

TEMPLATES = Path(__file__).parent / "templates"
TEMPLATE_NAME = "waitlist_ack"


def build_ack_email(locale: str) -> RenderedMail:
    return render_mail(TEMPLATES, TEMPLATE_NAME, locale)
