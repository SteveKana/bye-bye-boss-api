"""Matching's one transactional email: sent once, right after a brand-new
profile's very first (immediate) matching run finishes -- see
jobs.run_matching_for_new_profile. Wording lives in
`templates/mail/first_matches_ready/<locale>/`.

Always sent, even when that first run found zero matches (Steve's explicit
call): it confirms the analysis actually ran, rather than leaving a
candidate wondering, and the copy itself handles both cases via
`match_count`.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.modules.mailer import RenderedMail, render_mail

TEMPLATES = Path(__file__).parent / "templates"


def build_first_matches_ready_email(locale: str, *, match_count: int) -> RenderedMail:
    settings = get_settings()
    link = f"{settings.APP_URL.rstrip('/')}/dashboard"
    return render_mail(
        TEMPLATES, "first_matches_ready", locale, link=link, match_count=match_count
    )
