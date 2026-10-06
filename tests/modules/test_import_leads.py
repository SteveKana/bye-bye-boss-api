from __future__ import annotations

import re

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.leads.importer import import_leads
from app.modules.leads.models import Lead
from app.modules.mailer.models import EmailMessage

LEADS = "/api/v1/leads"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
RESET_CONFIRM = "/api/v1/auth/reset-password/confirm"


async def _rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.exec(select(model))).all())


async def _run(**kwargs):
    async with AsyncSessionLocal() as session:
        return await import_leads(session, **kwargs)


async def _seed(client: AsyncClient) -> None:
    await client.post(LEADS, json={"email": "fr@example.com", "locale": "fr"})
    await client.post(LEADS, json={"email": "en@example.com", "locale": "en"})
    await client.post(LEADS, json={"email": "Own@Example.com", "locale": "fr"})
    # This lead already signed up by itself (different letter case).
    await client.post(
        REGISTER, json={"email": "OWN@example.com", "password": "supersecret"}
    )


async def test_dry_run_changes_nothing(client: AsyncClient) -> None:
    await _seed(client)
    mails_before = len(await _rows(EmailMessage))
    users_before = len(await _rows(User))

    report = await _run()

    assert sorted(report.to_import) == ["en@example.com", "fr@example.com"]
    assert report.already_accounts == ["own@example.com"]
    assert len(await _rows(User)) == users_before
    assert len(await _rows(EmailMessage)) == mails_before


async def test_apply_creates_accounts_and_one_invitation_each(
    client: AsyncClient,
) -> None:
    await _seed(client)
    mails_before = len(await _rows(EmailMessage))

    report = await _run(apply=True)
    assert sorted(report.invitations_queued) == ["en@example.com", "fr@example.com"]

    users = {u.email: u for u in await _rows(User)}
    assert users["fr@example.com"].password_hash is None
    assert users["fr@example.com"].is_verified is False

    new_mails = (await _rows(EmailMessage))[mails_before:]
    by_to = {m.to_email: m for m in new_mails}
    assert set(by_to) == {"fr@example.com", "en@example.com"}
    assert "est ouvert" in by_to["fr@example.com"].subject
    assert "now open" in by_to["en@example.com"].subject
    assert "/reset-password?token=" in by_to["fr@example.com"].body_text
    assert "Choisir mon mot de passe" in (by_to["fr@example.com"].body_html or "")
    assert "Choose my password" in (by_to["en@example.com"].body_html or "")

    # Re-running never duplicates accounts nor mails.
    again = await _run(apply=True)
    assert again.to_import == []
    assert len(again.already_invited) == 2
    assert len(await _rows(EmailMessage)) == mails_before + 2
    assert all(
        lead.invited_at for lead in await _rows(Lead) if lead.email != "own@example.com"
    )


async def test_limit_caps_the_batch(client: AsyncClient) -> None:
    await _seed(client)
    report = await _run(apply=True, limit=1)
    assert len(report.invitations_queued) == 1


async def test_invitation_link_sets_password_and_verifies(
    client: AsyncClient,
) -> None:
    await _seed(client)
    await _run(apply=True)
    mail = next(
        m
        for m in await _rows(EmailMessage)
        if m.to_email == "fr@example.com" and "est ouvert" in m.subject
    )
    token = re.search(r"token=([\w.\-]+)", mail.body_text).group(1)

    r = await client.post(
        RESET_CONFIRM, json={"token": token, "new_password": "chosenpass1"}
    )
    assert r.status_code == 200
    assert (
        await client.post(
            LOGIN, json={"email": "fr@example.com", "password": "chosenpass1"}
        )
    ).status_code == 200
    user = next(u for u in await _rows(User) if u.email == "fr@example.com")
    assert user.is_verified is True
