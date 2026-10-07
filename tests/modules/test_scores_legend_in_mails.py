"""Every email that shows scores carries the "Comprendre nos scores" legend
(Steve, 2026-10-07), same wording as the app's compatibility card."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.mailer import render_mail
from app.modules.matching.emails import build_first_matches_ready_email
from app.modules.notifications.brief_item import BriefItem

NOTIFICATIONS_TEMPLATES = (
    Path(__file__).parent.parent.parent / "app/modules/notifications/templates"
)

LEGEND = {
    "fr": ["Comprendre nos scores", "ATS Potentiel", "Très forte compatibilité"],
    "en": ["Understand our scores", "ATS Potential", "Very strong fit"],
}


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_first_matches_email_has_legend_when_there_are_matches(locale: str) -> None:
    mail = build_first_matches_ready_email(locale, match_count=3)
    for expected in LEGEND[locale]:
        assert expected in mail.text
        assert mail.html is not None and expected in mail.html
    assert "{%" not in mail.text and "{{" not in mail.text


@pytest.mark.parametrize("locale", ["fr", "en"])
def test_first_matches_email_has_no_legend_without_matches(locale: str) -> None:
    mail = build_first_matches_ready_email(locale, match_count=0)
    assert LEGEND[locale][0] not in mail.text
    assert mail.html is not None and LEGEND[locale][0] not in mail.html


def test_daily_brief_labels_the_score_and_has_legend() -> None:
    item = BriefItem(
        match_id="m1",
        title="Chef de projet",
        company_name="Acme",
        url="https://example.test/o1",
        career_score=91,
        ats_potential=95,
    )
    mail = render_mail(
        NOTIFICATIONS_TEMPLATES,
        "daily_brief",
        "fr",
        first_name="Steve",
        items=[item],
        count=1,
        settings_url="https://example.test/settings",
    )
    assert "Career 91/100" in mail.text
    assert mail.html is not None and "Career 91/100" in mail.html
    for expected in LEGEND["fr"]:
        assert expected in mail.text
        assert expected in mail.html
