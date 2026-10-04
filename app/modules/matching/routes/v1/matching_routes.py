from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.core.models import utcnow
from app.modules.auth import CurrentUser
from app.modules.cv import CvService
from app.modules.matching.models import ApplicationStatus, CandidateMatch, MatchStatus
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.schemas import ApplicationStatusUpdate, CandidateMatchRead
from app.modules.matching.skill_labels_service import SkillLabelService
from app.modules.offers import JobOffer, JobOfferRead, JobOfferRepository

router = APIRouter(prefix="/matching", tags=["matching"])


def _to_read(
    match: CandidateMatch, offer: JobOffer, analysis: dict | None = None
) -> CandidateMatchRead:
    """`analysis` is the stored one with French skill labels added (see
    skill_labels.py) -- every caller goes through _read_all/_read_or_404 so
    the labels are always there."""
    return CandidateMatchRead(
        id=match.id,
        status=match.status,
        company_name=match.company_name,
        career_score=match.career_score,
        ats_score=match.ats_score,
        ats_potential=match.ats_potential,
        blocking_message=match.blocking_message,
        analysis=match.analysis if analysis is None else analysis,
        # regret fields removed from CandidateMatchRead 2026-10-03, see
        # schemas.py -- kept out here too, not just left unused, since
        # CandidateMatchRead(...) would otherwise reject these as unknown
        # kwargs.
        # regret_availability=match.regret_availability,
        # regret_score=match.regret_score,
        computed_at=match.computed_at,
        application_status=ApplicationStatus(match.application_status),
        application_status_updated_at=match.application_status_updated_at,
        offer=JobOfferRead.model_validate(offer),
    )


async def _get_owned_match_or_404(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CandidateMatch:
    """Shared by every route below that acts on one match_id -- same
    anti-IDOR 404 (never a 403) as get_match's own docstring explains."""
    profile = await CvService(session).get_for_user(user.id)
    match = await CandidateMatchRepository(session).get(match_id)
    if match is None or match.candidate_profile_id != profile.id:
        raise NotFoundError("Correspondance introuvable.")
    return match


async def _read_all(
    pairs: list[tuple[CandidateMatch, JobOffer]], session: DBSession
) -> list[CandidateMatchRead]:
    """Matches ready for the API, their skill names with French labels --
    one glossary query for the whole list."""
    analyses = await SkillLabelService(session).labelled(
        [match.analysis or {} for match, _ in pairs]
    )
    return [
        _to_read(match, offer, analysis)
        for (match, offer), analysis in zip(pairs, analyses, strict=True)
    ]


async def _read_or_404(match: CandidateMatch, session: DBSession) -> CandidateMatchRead:
    """Shared tail of every single-match route below: a match whose offer
    was since removed from the pool is treated the same as a missing
    match, not returned half-broken."""
    offer = await JobOfferRepository(session).get(match.job_offer_id)
    if offer is None:
        raise NotFoundError("Correspondance introuvable.")
    return (await _read_all([(match, offer)], session))[0]


@router.get("/top", response_model=list[CandidateMatchRead])
async def top_matches(
    session: DBSession, user: CurrentUser
) -> list[CandidateMatchRead]:
    """The candidate's /opportunites history: their MATCHING_HISTORY_LIMIT
    (25) most recent offers, newest first -- including ones still being
    analysed (status != "scored", no scores yet). The frontend does its own
    sorting/filtering (relevance, date, ATS threshold...).

    Nothing is computed here -- see the `matching` module's docstring for
    why scoring runs entirely in the background. A profile that hasn't been
    matched yet (too new, or not "complete") simply has no rows here yet.
    """
    settings = get_settings()
    profile = await CvService(session).get_for_user(user.id)
    matches = await CandidateMatchRepository(session).list_visible_for_profile(
        profile.id, limit=settings.MATCHING_HISTORY_LIMIT
    )

    offers = JobOfferRepository(session)
    pairs: list[tuple[CandidateMatch, JobOffer]] = []
    for match in matches:
        offer = await offers.get(match.job_offer_id)
        if offer is None:
            # The offer was removed from the pool since this match was
            # computed -- skip rather than show a match with no offer to link to.
            continue
        pairs.append((match, offer))
    return await _read_all(pairs, session)


@router.get("/dashboard", response_model=list[CandidateMatchRead])
async def dashboard_matches(
    session: DBSession, user: CurrentUser
) -> list[CandidateMatchRead]:
    """The dashboard's "Top 5 des opportunités" (Steve, 2026-10-04): only
    offers the candidate hasn't been shown on the dashboard before -- or was
    first shown within the last DASHBOARD_WINDOW_HOURS, so the day's list
    survives a page refresh -- never one they already applied to, and, once
    analysed, only with a final ATS score >= DASHBOARD_MIN_ATS. Best first
    (career x ATS potential), offers still being analysed after the scored
    ones. Returning an offer for the first time stamps it, which starts its
    window.

    Registered before "/{match_id}" so "dashboard" is never swallowed by
    that path param.
    """
    settings = get_settings()
    now = utcnow()
    profile = await CvService(session).get_for_user(user.id)
    repo = CandidateMatchRepository(session)
    candidates = await repo.list_dashboard_candidates(
        profile.id,
        shown_since=now - timedelta(hours=settings.DASHBOARD_WINDOW_HOURS),
        min_ats=settings.DASHBOARD_MIN_ATS,
    )

    def rank(match: CandidateMatch) -> tuple[int, int]:
        scored = match.status == MatchStatus.scored.value
        # Scored first, by career x ATS potential; the rest by pre-filter ATS.
        return (
            1 if scored else 0,
            match.career_score * match.ats_potential
            if scored
            else (match.prefilter_score or 0),
        )

    candidates = sorted(candidates, key=rank, reverse=True)

    offers = JobOfferRepository(session)
    pairs: list[tuple[CandidateMatch, JobOffer]] = []
    for match in candidates:
        if len(pairs) >= settings.DASHBOARD_TOP_COUNT:
            break
        offer = await offers.get(match.job_offer_id)
        if offer is None:
            continue
        if match.dashboard_first_shown_at is None:
            await repo.update(match, {"dashboard_first_shown_at": now})
        pairs.append((match, offer))
    results = await _read_all(pairs, session)
    await session.commit()
    return results


@router.get("/applications", response_model=list[CandidateMatchRead])
async def list_applications(
    session: DBSession, user: CurrentUser
) -> list[CandidateMatchRead]:
    """Matches the candidate has a declared application status for -- backs
    the "Candidatures" page. See ApplicationStatus's docstring for how a
    match normally gets here (automatically, via mark_applied below) and
    how it's corrected (update_application_status). Registered before
    "/{match_id}" so "applications" is never swallowed by that path param.
    Most recently updated first.
    """
    profile = await CvService(session).get_for_user(user.id)
    matches = await CandidateMatchRepository(session).list_applications_for_profile(
        profile.id
    )

    offers = JobOfferRepository(session)
    pairs: list[tuple[CandidateMatch, JobOffer]] = []
    for match in matches:
        offer = await offers.get(match.job_offer_id)
        if offer is None:
            continue
        pairs.append((match, offer))
    return await _read_all(pairs, session)


@router.get("/{match_id}", response_model=CandidateMatchRead)
async def get_match(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CandidateMatchRead:
    """One pre-computed match, in full -- backs the "Opportunité" detail
    page (scores, matched skills, ATS gaps, actions...), which /top's
    summary shape doesn't need to carry for every row in the dashboard list.

    A match that exists but doesn't belong to the current user's profile
    returns 404, not 403 -- see core/exceptions.py's anti-IDOR convention:
    never confirm to a caller that a given match_id exists for someone else.
    """
    match = await _get_owned_match_or_404(match_id, session, user)
    return await _read_or_404(match, session)


@router.post("/{match_id}/mark-applied", response_model=CandidateMatchRead)
async def mark_applied(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CandidateMatchRead:
    """Called the instant the candidate clicks "Voir l'offre" on the
    opportunity page -- the click-through itself is the signal, nothing is
    asked of the candidate. Idempotent and one-way: only upgrades a fresh,
    never-touched `not_applied` match to `applied`; a match already further
    along (interview/offer/rejected/withdrawn), or explicitly reset back to
    `not_applied` via update_application_status below, is left untouched --
    so clicking "Voir l'offre" again later never regresses real progress
    the candidate already recorded, and never silently undoes a correction
    either. The `application_manually_corrected` flag is what makes the
    second case possible: a manual reset looks identical to a never-clicked
    match by `application_status` alone (both are `not_applied`), so the
    flag is what this check actually relies on to tell them apart -- see
    CandidateMatch.application_manually_corrected's docstring.
    """
    match = await _get_owned_match_or_404(match_id, session, user)
    if (
        match.application_status == ApplicationStatus.not_applied.value
        and not match.application_manually_corrected
    ):
        match = await CandidateMatchRepository(session).update(
            match,
            {
                "application_status": ApplicationStatus.applied.value,
                "application_status_updated_at": utcnow(),
            },
        )
    return await _read_or_404(match, session)


@router.patch("/{match_id}/application-status", response_model=CandidateMatchRead)
async def update_application_status(
    match_id: uuid.UUID,
    payload: ApplicationStatusUpdate,
    session: DBSession,
    user: CurrentUser,
) -> CandidateMatchRead:
    """Explicit status change -- backs the "Candidatures" page's manual
    corrections: advancing to interview/offer/rejected/withdrawn, or
    resetting a wrongly auto-marked `applied` back to `not_applied` (which
    drops it out of list_applications above). Unlike mark_applied, this
    always applies the given value, including a downgrade.

    Also sets `application_manually_corrected`, permanently for this match:
    once a candidate has explicitly set a status here, mark_applied must
    never auto-upgrade it again, even if it's `not_applied` and the
    candidate later re-clicks "Voir l'offre" on the same offer.
    """
    match = await _get_owned_match_or_404(match_id, session, user)
    match = await CandidateMatchRepository(session).update(
        match,
        {
            "application_status": payload.application_status.value,
            "application_status_updated_at": utcnow(),
            "application_manually_corrected": True,
        },
    )
    return await _read_or_404(match, session)
