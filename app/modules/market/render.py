"""Turns a digest into the e-mail (subject, text, HTML)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.config import get_settings
from app.modules.mailer import RenderedMail, render_mail
from app.modules.market.digest import DigestData

TEMPLATES = Path(__file__).parent / "templates"

_MONTHS = [
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
]  # fmt: skip

MAX_BAR_PX = 56


def fr_short(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]}"


def render_digest(data: DigestData) -> RenderedMail:
    peak = max(
        (pct for e in data.evolution for _, pct in e.weeks),
        default=1,
    )
    evolution = [
        {
            "name": e.name,
            "first_pct": e.first_pct,
            "last_pct": e.last_pct,
            "weeks": [
                {
                    "pct": pct,
                    "label": fr_short(week),
                    "height": max(2, round(pct / max(peak, 1) * MAX_BAR_PX)),
                }
                for week, pct in e.weeks
            ],
        }
        for e in data.evolution
    ]
    base = get_settings().APP_URL.rstrip("/")
    return render_mail(
        TEMPLATES,
        "market_digest",
        "fr",
        first_name=data.first_name,
        label=data.label,
        week_label=fr_short(data.week_start),
        offers_analysed=data.offers_analysed,
        weeks_used=data.weeks_used,
        partial_week=data.partial_week,
        top_skills=data.top_skills,
        evolution=evolution,
        actions=data.actions,
        forecasts=data.forecasts,
        link=f"{base}/opportunites",
    )
