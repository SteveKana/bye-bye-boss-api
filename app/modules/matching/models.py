from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

# JSONB on Postgres, plain JSON elsewhere -- same pattern as
# app/modules/cv/models.py and app/modules/offers/models.py.
_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class ApplicationStatus(enum.StrEnum):
    """Where a candidate stands on a given offer -- entirely self-reported
    (or self-corrected, see below), since MatchCareer never sees what
    happens on the employer's/aggregator's own site.

    `not_applied` is the default for every match; it's what keeps a match
    out of the "Candidatures" list (see matching_routes.list_applications).
    `applied` is normally set automatically -- see
    routes/v1/matching_routes.mark_applied, called the moment the
    candidate clicks "Voir l'offre" on the opportunity page, on the
    (deliberate) assumption that clicking through is itself the signal of
    intent, so nothing is asked of the candidate for the common case. Every
    other transition (interview/offer/rejected/withdrawn), and correcting a
    wrong `applied` back to `not_applied`, is explicit -- see
    update_application_status.
    """

    not_applied = "not_applied"
    applied = "applied"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


class CandidateMatch(BaseModel, table=True):
    """A computed match between one candidate profile and one job offer.

    One row per (candidate_profile, job_offer) pair, refreshed in place
    rather than duplicated -- see MatchingService._upsert. Computed entirely
    in the background (see jobs.py): the dashboard only ever reads rows this
    table already has, so opening it never waits on an LLM call.

    `regret_availability`/`regret_score` are filled in by RegretService (see
    regret_service.py) from CompanyRegretProfile below -- looked up by
    company name at upsert time, never computed inline here. Still
    "unavailable" whenever that lookup has nothing solid to go on: see
    CompanyRegretProfile's own docstring for what "solid" means and why this
    is a real product/legal tradeoff, not a purely technical one (Steve,
    2026-09-30).
    """

    __tablename__ = "candidate_matches"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id",
            "job_offer_id",
            name="uq_candidate_matches_profile_offer",
        ),
    )

    candidate_profile_id: uuid.UUID = Field(index=True, nullable=False)
    job_offer_id: uuid.UUID = Field(index=True, nullable=False)

    company_name: str = Field(default="")
    career_score: int = Field(nullable=False)
    ats_score: int = Field(nullable=False)
    ats_potential: int = Field(nullable=False)
    blocking_message: str = Field(default="")

    # Full LLMAnalysis payload (matches, ats_gaps, actions, explanations,
    # skills...) -- kept as one JSON blob rather than normalized into more
    # tables, mirroring candidate_profiles' own JSON list fields. career_score
    # /ats_score/ats_potential are promoted to real columns above so the
    # dashboard's "top matches" query can sort/filter in SQL.
    analysis: dict = Field(default_factory=dict, sa_column=Column(_JsonColumn))

    # Always "unavailable" until a real review data source is wired in.
    regret_availability: str = Field(default="unavailable", nullable=False)
    regret_score: int | None = Field(default=None)

    computed_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))

    # See ApplicationStatus's docstring. Never touched by MatchingService's
    # re-scoring upsert (see service.py's `values` dict) -- a fresh LLM
    # score must never reset a candidate's declared application progress.
    application_status: str = Field(
        default=ApplicationStatus.not_applied.value, nullable=False, index=True
    )
    application_status_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    # Set once and for all by update_application_status (any explicit
    # change, including a downgrade back to not_applied) -- never by
    # mark_applied. Distinguishes a match the candidate has actually acted
    # on from one that's merely never been clicked, since both look
    # identical as a bare `application_status == "not_applied"` value.
    # mark_applied checks this before auto-upgrading so that a candidate who
    # corrects a false-positive "applied" back to "not_applied" doesn't have
    # it silently flipped back to "applied" the next time they revisit the
    # same offer and click "Voir l'offre" again.
    application_manually_corrected: bool = Field(default=False, nullable=False)


class CompanyRegretProfile(BaseModel, table=True):
    """A cached Regret Index for one employer, keyed by normalized company
    name -- shared across every candidate matched to that employer, so a
    popular employer is only ever looked up once per REGRET_CACHE_TTL_DAYS
    window, not once per candidate x offer pair.

    IMPORTANT CONTEXT (read before touching regret_service.py/
    simplyhired_gateway.py): this table exists because Steve explicitly
    asked (2026-09-30) for employee sentiment on each employer, after being
    told, and independently confirming, that no legitimate/structured
    review API exists at a cost or access level this project can use
    (Glassdoor/Google: scraping either means defeating CAPTCHA/anti-bot
    protection, refused outright regardless of the request; Indeed: partner
    API covers job postings only, no reviews; ChooseMyCompany: a real,
    documented API exists but is access-gated, contact-only). This started
    against Reddit (first its OAuth2 API, then -- after Reddit locked down
    self-serve app creation the same day -- its public search endpoint, an
    LLM turning raw posts into a score), kept in reddit_gateway.py/
    regret_gateway.py/regret_prompt.py/regret_schema.py for reference but no
    longer called. As of 2026-09-30 this is populated from SimplyHired.fr's
    public company-review pages instead (see simplyhired_gateway.py) --
    already-structured data (a star rating, category breakdowns, a review
    count), so no LLM step is needed; `regret_score` is a direct conversion
    of the star rating. Nobody involved in writing this is a lawyer, and
    scraping SimplyHired -- republishing their own computed rating rather
    than our own transformative analysis of raw text, as the Reddit approach
    was -- was accepted as a product decision (Steve, 2026-09-30), not
    resolved. If that decision ever changes, this table (and
    regret_service.py's call site in MatchingService._upsert) is the one
    place to revert.

    "unavailable" is still a real outcome, not just a placeholder for the
    pre-scraping era: no SimplyHired page found for a company, or fewer than
    settings.REGRET_MIN_REVIEWS reviews behind its rating, means
    regret_availability stays "unavailable" rather than showing a candidate
    a number built on a guessed company match or too little material -- same
    never-fabricate principle CandidateMatch's docstring already held.
    """

    __tablename__ = "company_regret_profiles"

    # Folded (core.text.fold) company name -- accent/case-insensitive so
    # "Capgemini" and "CAPGEMINI" share one row. Not a strict identity match
    # (two differently-named legal entities of the same group won't merge),
    # but JobOffer/CandidateMatch only ever carry a free-text company name
    # anyway, so this is the best key available without a company registry.
    company_name_key: str = Field(index=True, unique=True, nullable=False)
    # Original casing, kept only for admin/debugging readability.
    company_name: str = Field(default="")

    regret_availability: str = Field(default="unavailable", nullable=False)
    regret_score: int | None = Field(default=None)
    # How many reviews the score (if any) was actually based on -- named
    # generically (not "review_count") because this table briefly held
    # Reddit *mention* counts before the SimplyHired pivot; lets a future
    # admin view distinguish "score from 5 reviews" from "score from 80"
    # without re-scraping.
    mention_count: int = Field(default=0, nullable=False)

    # ---- SimplyHired-specific detail, added 2026-09-30 ---------------------
    # The raw star rating (out of 5) regret_score was derived from -- kept
    # alongside the derived score so an admin view can show "3.5/5" rather
    # than just the converted 0-100 number.
    overall_rating: float | None = Field(default=None)
    # Per-category breakdown as SimplyHired shows it (work_life_balance,
    # compensation, job_security, management, culture -> a 0-5 float) --
    # see simplyhired_gateway.py's _CATEGORY_LABELS for the exact French
    # labels each key comes from. Empty when a category wasn't found on the
    # page (parsing is best-effort, see that module's docstring).
    category_scores: dict = Field(default_factory=dict, sa_column=Column(_JsonColumn))
    # SimplyHired's "% of employees satisfied with their salary" stat, kept
    # as-is (not folded into regret_score) since it measures something more
    # specific than overall regret. None when not found on the page.
    satisfaction_percent: int | None = Field(default=None)
    # The exact SimplyHired URL this row was scraped from -- admin/debugging
    # readability, same spirit as company_name above.
    source_url: str = Field(default="")

    computed_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
