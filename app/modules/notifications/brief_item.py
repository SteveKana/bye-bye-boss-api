"""Shared shape for "one offer inside a daily brief" -- built once by
DailyBriefService, consumed by every channel in channels/. Deliberately
just the handful of fields a brief message needs (not the full
CandidateMatchRead/JobOfferRead), so a channel can't accidentally reach for
something a WhatsApp template message or a Discord embed has no room for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BriefItem:
    match_id: str
    title: str
    company_name: str
    url: str
    career_score: int
    # The score the WhatsApp message and the daily-brief email show (the email
    # switched from career_score on 2026-10-13, Steve's call). It
    # already prices in a real hard_blocker (stays low), which career_score
    # never does by design (see matching/prompt.py's ETAPE 7). career_score is
    # kept on the item for the Discord embed, which still shows it.
    ats_potential: int
