from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.events import event_bus
from app.core.models import utcnow
from app.modules.auth import UserDeletionRequested
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.mailer.models import EmailMessage
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.models import CandidateMatch
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)
from app.modules.offers.models import JobOffer

ME = "/api/v1/auth/me"
EMAIL = "user@example.com"


async def _count(model) -> int:
    async with AsyncSessionLocal() as session:
        return len((await session.exec(select(model))).all())


async def _seed_account_data(tmp_path: Path, monkeypatch) -> uuid.UUID:
    """Everything the account owns: profile (+ CV file), a match with an
    optimised CV, a notification preference and a sent-brief entry."""
    monkeypatch.setattr(get_settings(), "CV_UPLOAD_DIR", str(tmp_path))
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == EMAIL))).first()
        profile = CandidateProfile(
            user_id=user.id, status=ProfileStatus.complete.value, raw_text="cv"
        )
        offer = JobOffer(
            source="test", external_id="1", title="PO", description="d", url="https://x"
        )
        session.add(profile)
        session.add(offer)
        await session.flush()
        match = CandidateMatch(
            candidate_profile_id=profile.id,
            job_offer_id=offer.id,
            company_name="Astek",
            career_score=80,
            ats_score=80,
            ats_potential=90,
            computed_at=utcnow(),
        )
        session.add(match)
        await session.flush()
        session.add(CVOptimization(candidate_match_id=match.id, computed_at=utcnow()))
        # (the notification preference row already exists: created at signup)
        session.add(
            NotificationBriefEntry(
                user_id=user.id, candidate_match_id=match.id, sent_at=utcnow()
            )
        )
        await session.commit()
        (tmp_path / f"{user.id}.pdf").write_bytes(b"%PDF-1.4 cv")
        return user.id


async def test_delete_account_requires_auth(client: AsyncClient) -> None:
    r = await client.request("DELETE", ME, json={"email": EMAIL})
    assert r.status_code == 401


async def test_delete_account_rejects_a_wrong_confirmation_email(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.request(
        "DELETE", ME, json={"email": "someone-else@example.com"}, headers=auth_headers
    )
    assert r.status_code == 400
    assert (await client.get(ME, headers=auth_headers)).status_code == 200


async def test_delete_account_removes_the_user_and_everything_they_own(
    client: AsyncClient, auth_headers: dict[str, str], tmp_path: Path, monkeypatch
) -> None:
    user_id = await _seed_account_data(tmp_path, monkeypatch)
    assert (tmp_path / f"{user_id}.pdf").exists()

    # Case/whitespace-insensitive confirmation.
    r = await client.request(
        "DELETE", ME, json={"email": f"  {EMAIL.upper()} "}, headers=auth_headers
    )
    assert r.status_code == 204

    for model in (
        User,
        CandidateProfile,
        CandidateMatch,
        CVOptimization,
        NotificationPreference,
        NotificationBriefEntry,
    ):
        assert await _count(model) == 0, model.__name__
    assert not (tmp_path / f"{user_id}.pdf").exists()
    # The old token no longer opens anything, and a confirmation email was queued.
    assert (await client.get(ME, headers=auth_headers)).status_code == 401
    async with AsyncSessionLocal() as session:
        mails = (
            await session.exec(
                select(EmailMessage).where(EmailMessage.to_email == EMAIL)
            )
        ).all()
    assert any("supprimé" in m.subject for m in mails)


async def test_deleted_email_can_register_again(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.request("DELETE", ME, json={"email": EMAIL}, headers=auth_headers)
    r = await client.post(
        "/api/v1/auth/register", json={"email": EMAIL, "password": "supersecret"}
    )
    assert r.status_code == 201


async def test_admin_account_cannot_be_deleted(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == EMAIL))).first()
        user.isadmin = True
        session.add(user)
        await session.commit()
    r = await client.request("DELETE", ME, json={"email": EMAIL}, headers=auth_headers)
    assert r.status_code == 400
    assert await _count(User) == 1


async def test_a_failed_purge_keeps_the_account_so_it_can_be_retried(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    async def boom(event: UserDeletionRequested) -> None:
        raise RuntimeError("purge failed")

    event_bus.subscribe(UserDeletionRequested, boom)
    try:
        # The test transport re-raises the server error instead of a 500.
        with pytest.raises(RuntimeError, match="purge failed"):
            await client.request(
                "DELETE", ME, json={"email": EMAIL}, headers=auth_headers
            )
        assert await _count(User) == 1
    finally:
        event_bus._handlers[UserDeletionRequested].remove(boom)  # noqa: SLF001
