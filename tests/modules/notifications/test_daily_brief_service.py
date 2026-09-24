from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import BadRequestError
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)
from app.modules.notifications.repository import (
    NotificationBriefEntryRepository,
    NotificationPreferenceRepository,
)
from app.modules.notifications.service import DailyBriefService
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


async def _user_id(email: str = "user@example.com") -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        return user.id


async def _profile_with_matches(*, n_matches: int = 1) -> CandidateProfile:
    user_id = await _user_id()
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id,
                status=ProfileStatus.complete.value,
                raw_text="cv",
            )
        )
        for i in range(n_matches):
            offer = await JobOfferRepository(session).create(
                JobOffer(
                    source="test",
                    external_id=f"brief-offer-{i}",
                    title=f"Offre {i}",
                    company_name=f"Entreprise {i}",
                    url=f"https://example.com/offre-{i}",
                )
            )
            await CandidateMatchRepository(session).create(
                CandidateMatch(
                    candidate_profile_id=profile.id,
                    job_offer_id=offer.id,
                    career_score=90 - i,
                    ats_score=60,
                    ats_potential=80,
                    computed_at=utcnow(),
                )
            )
        await session.commit()
    return profile


async def _register(client, email: str = "user@example.com") -> None:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "first_name": "Steve"},
    )


async def test_send_daily_briefs_skips_user_with_no_channel_enabled(
    client: AsyncClient, verify_user
) -> None:
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches()
    async with AsyncSessionLocal() as session:
        # No NotificationPreference row at all -- never opted into anything.
        report = await DailyBriefService(session).send_daily_briefs()

    assert report.users_considered == 1
    assert report.users_skipped_no_channel == 1
    assert report.users_sent == 0

    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert entries == []


async def test_send_daily_briefs_sends_email_by_default_and_records_entries(
    client: AsyncClient, verify_user
) -> None:
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=2)
    async with AsyncSessionLocal() as session:
        # email_enabled defaults to True -- a bare default-constructed
        # preference row is enough to opt in, same as the route's
        # get_or_create.
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()

    assert report.users_sent == 1
    assert report.items_sent == 2

    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert len(entries) == 2
    assert all(e.channels_sent == ["email"] for e in entries)


async def test_send_daily_briefs_never_resends_an_already_sent_match(
    client: AsyncClient, verify_user
) -> None:
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=1)
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id)
        )
        matches = await CandidateMatchRepository(session).list_top_for_profile(
            profile.id
        )
        await NotificationBriefEntryRepository(session).create(
            NotificationBriefEntry(
                user_id=profile.user_id,
                candidate_match_id=matches[0].id,
                sent_at=utcnow(),
                channels_sent=["email"],
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()

    # The one match this candidate has was already sent -- nothing new to
    # send, and the run must not create a second entry for it.
    assert report.users_skipped_no_new_offers == 1
    assert report.users_sent == 0
    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert len(entries) == 1


async def test_send_daily_briefs_caps_items_at_configured_max(
    client: AsyncClient, verify_user, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "NOTIFICATIONS_BRIEF_MAX_ITEMS", 2)
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=5)
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()

    assert report.items_sent == 2


async def test_send_daily_briefs_discord_dispatch_uses_configured_webhook(
    client: AsyncClient, verify_user, monkeypatch
) -> None:
    calls: list[dict] = []

    async def _fake_send_discord(*, webhook_url, items):
        calls.append({"webhook_url": webhook_url, "items": items})
        return True

    monkeypatch.setattr(
        "app.modules.notifications.service.send_brief_discord", _fake_send_discord
    )

    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=1)
    webhook = "https://discord.com/api/webhooks/1/abc"
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(
                user_id=profile.user_id,
                email_enabled=False,
                discord_enabled=True,
                discord_webhook_url=webhook,
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()

    assert report.users_sent == 1
    assert len(calls) == 1
    assert calls[0]["webhook_url"] == webhook
    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert entries[0].channels_sent == ["discord"]


async def test_send_daily_briefs_whatsapp_unconfigured_is_skipped_not_fatal(
    client: AsyncClient, verify_user
) -> None:
    """WHATSAPP_* is unset in the test environment (see conftest) -- opting
    in must not crash the run, just fail to actually deliver that one
    channel while email still goes out."""
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=1)
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(
                user_id=profile.user_id,
                whatsapp_enabled=True,
                whatsapp_phone_number="+33612345678",
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()

    assert report.users_sent == 1
    assert report.channel_failures.get("whatsapp") == 1
    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert entries[0].channels_sent == ["email"]  # whatsapp attempted, not delivered


async def test_send_test_brief_requires_a_channel(
    client: AsyncClient, verify_user
) -> None:
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=1)
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id, email_enabled=False)
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        with pytest.raises(BadRequestError):
            await DailyBriefService(session).send_test_brief(profile.user_id)


async def test_send_test_brief_sends_immediately_and_ignores_dedup(
    client: AsyncClient, verify_user
) -> None:
    """Unlike send_daily_briefs, a match already recorded as sent must still
    go out again here -- testing must never be blocked by, or itself
    disturb, the real job's dedup ledger."""
    await _register(client)
    await verify_user("user@example.com")
    profile = await _profile_with_matches(n_matches=1)
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id)
        )
        matches = await CandidateMatchRepository(session).list_top_for_profile(
            profile.id
        )
        await NotificationBriefEntryRepository(session).create(
            NotificationBriefEntry(
                user_id=profile.user_id,
                candidate_match_id=matches[0].id,
                sent_at=utcnow(),
                channels_sent=["email"],
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        channels_sent = await DailyBriefService(session).send_test_brief(
            profile.user_id
        )

    assert channels_sent == ["email"]
    async with AsyncSessionLocal() as session:
        entries = await NotificationBriefEntryRepository(session).list_for_user(
            profile.user_id
        )
    assert len(entries) == 1  # test-send recorded nothing new


async def test_test_send_route_delivers_and_reports_channels(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    profile = await _profile_with_matches(n_matches=1)
    async with AsyncSessionLocal() as session:
        # Default-constructed preference: email_enabled=True, Discord/
        # WhatsApp off -- avoids the route actually reaching out over the
        # network to a fake webhook/WhatsApp endpoint during a test.
        await NotificationPreferenceRepository(session).create(
            NotificationPreference(user_id=profile.user_id)
        )
        await session.commit()

    r = await client.post(
        "/api/v1/notifications/preferences/test-send", headers=auth_headers
    )

    assert r.status_code == 200
    assert r.json()["channels_sent"] == ["email"]
