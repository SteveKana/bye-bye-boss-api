from __future__ import annotations

import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching.models import CandidateMatch, MatchStatus
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository

TOP = "/api/v1/matching/top"
DASHBOARD = "/api/v1/matching/dashboard"


async def _profile(email: str = "user@example.com") -> CandidateProfile:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user.id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        await session.commit()
        return profile


async def _match(profile, title: str, **values) -> CandidateMatch:
    defaults = {
        "status": MatchStatus.scored.value,
        "career_score": 80,
        "ats_score": 80,
        "ats_potential": 90,
        "computed_at": utcnow(),
    }
    defaults.update(values)
    async with AsyncSessionLocal() as session:
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id=f"{title}-{uuid.uuid4()}",
                title=title,
                url="https://example.com/o",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id, job_offer_id=offer.id, **defaults
            )
        )
        await session.commit()
        return match


async def _titles(client, headers, url) -> list[str]:
    r = await client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return [m["offer"]["title"] for m in r.json()]


# ---- /matching/top (the /opportunites history) -------------------------------


async def test_top_returns_visible_matches_only_including_pending(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    await _match(profile, "scored")
    await _match(profile, "pending", status=MatchStatus.pending.value, career_score=0)
    await _match(
        profile, "placeholder", status=MatchStatus.placeholder.value, career_score=0
    )
    await _match(profile, "hidden-1", status=MatchStatus.shortlisted.value)
    await _match(profile, "hidden-2", status=MatchStatus.filtered_out.value)

    r = await client.get(TOP, headers=auth_headers)

    assert r.status_code == 200
    by_title = {m["offer"]["title"]: m for m in r.json()}
    assert set(by_title) == {"scored", "pending", "placeholder"}
    assert by_title["scored"]["status"] == "scored"
    assert by_title["pending"]["status"] == "pending"
    assert by_title["placeholder"]["analysis"] == {}


async def test_top_is_the_25_most_recent_newest_first(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    now = utcnow()
    for i in range(27):
        await _match(profile, f"offre-{i:02d}", created_at=now + timedelta(minutes=i))

    titles = await _titles(client, auth_headers, TOP)

    assert len(titles) == get_settings().MATCHING_HISTORY_LIMIT == 25
    assert titles[0] == "offre-26"
    assert titles[-1] == "offre-02"  # the two oldest dropped


# ---- /matching/dashboard ----------------------------------------------------


async def test_dashboard_requires_auth(client: AsyncClient) -> None:
    assert (await client.get(DASHBOARD)).status_code == 401


async def test_dashboard_returns_top_5_best_first(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    for i in range(7):
        await _match(profile, f"o{i}", career_score=50 + i * 5, ats_potential=80)

    titles = await _titles(client, auth_headers, DASHBOARD)

    assert titles == ["o6", "o5", "o4", "o3", "o2"]


async def test_dashboard_excludes_applied_hidden_and_low_ats_matches(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    await _match(profile, "ok")
    await _match(profile, "ok-at-threshold", ats_score=75)
    await _match(profile, "applied", application_status="applied")
    await _match(profile, "low-ats", ats_score=74)
    await _match(profile, "hidden", status=MatchStatus.filtered_out.value)
    await _match(profile, "queued", status=MatchStatus.shortlisted.value)

    titles = await _titles(client, auth_headers, DASHBOARD)

    assert sorted(titles) == ["ok", "ok-at-threshold"]


async def test_dashboard_shows_unscored_matches_after_scored_ones(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    await _match(
        profile,
        "pending-high",
        status=MatchStatus.pending.value,
        prefilter_score=95,
        career_score=0,
        ats_score=0,
        ats_potential=0,
    )
    await _match(profile, "scored", career_score=40, ats_potential=80)

    titles = await _titles(client, auth_headers, DASHBOARD)

    assert titles == ["scored", "pending-high"]


async def test_dashboard_keeps_a_shown_offer_for_24h_then_drops_it(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    recent = await _match(profile, "shown-1h-ago")
    expired = await _match(profile, "shown-25h-ago")
    never = await _match(profile, "never-shown")
    async with AsyncSessionLocal() as session:
        repo = CandidateMatchRepository(session)
        await repo.update(
            await repo.get(recent.id),
            {"dashboard_first_shown_at": utcnow() - timedelta(hours=1)},
        )
        await repo.update(
            await repo.get(expired.id),
            {"dashboard_first_shown_at": utcnow() - timedelta(hours=25)},
        )
        await session.commit()

    titles = await _titles(client, auth_headers, DASHBOARD)

    assert sorted(titles) == ["never-shown", "shown-1h-ago"]
    assert never.id  # (kept for readability above)


async def test_dashboard_stamps_first_exposure_but_not_unreturned_offers(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    matches = [
        await _match(profile, f"o{i}", career_score=50 + i * 5) for i in range(6)
    ]

    first = await _titles(client, auth_headers, DASHBOARD)
    # A refresh within the window shows the same five.
    assert await _titles(client, auth_headers, DASHBOARD) == first
    assert len(first) == 5

    async with AsyncSessionLocal() as session:
        repo = CandidateMatchRepository(session)
        stamped = {
            m.id: (await repo.get(m.id)).dashboard_first_shown_at for m in matches
        }
    assert sum(1 for v in stamped.values() if v is not None) == 5
    # The sixth (lowest-ranked) was not shown, so it stays eligible for the
    # next visit instead of being burnt.
    assert stamped[matches[0].id] is None


async def test_dashboard_is_empty_without_matches(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await _profile()
    assert await _titles(client, auth_headers, DASHBOARD) == []


# ---- CV optimisation needs a full analysis ------------------------------------


async def test_cv_optimization_refused_while_the_analysis_is_pending(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile()
    pending = await _match(profile, "pending", status=MatchStatus.pending.value)

    r = await client.post(
        f"/api/v1/matching/{pending.id}/cv-optimization", headers=auth_headers
    )

    assert r.status_code == 400
