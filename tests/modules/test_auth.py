from __future__ import annotations

import re

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.mailer.models import EmailMessage

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/verify-email"
RESEND = "/api/v1/auth/resend-verification"


async def _queued_emails(to_email: str) -> list[EmailMessage]:
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(EmailMessage).where(EmailMessage.to_email == to_email)
        )
        return list(result.all())


def _token_from_link(body: str) -> str:
    match = re.search(r"token=([\w.\-]+)", body)
    assert match, "no verification link in the email body"
    return match.group(1)


async def test_register_and_login_and_me(client: AsyncClient, verify_user) -> None:
    payload = {"email": "a@b.com", "password": "supersecret", "first_name": "A"}
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "a@b.com"
    assert body["first_name"] == "A"
    assert body["isadmin"] is False
    assert body["is_verified"] is False
    assert body["subscription"] == "standard"
    assert body["last_rescoring_time"] is None

    await verify_user("a@b.com")
    r = await client.post(LOGIN, json={"email": "a@b.com", "password": "supersecret"})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer"

    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"


async def test_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@b.com", "password": "supersecret"}
    assert (await client.post(REGISTER, json=payload)).status_code == 201
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_bad_credentials(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "c@b.com", "password": "supersecret"})
    r = await client.post(LOGIN, json={"email": "c@b.com", "password": "wrong"})
    assert r.status_code == 401


async def test_refresh_token_rejected_as_access(
    client: AsyncClient, verify_user
) -> None:
    await client.post(REGISTER, json={"email": "d@b.com", "password": "supersecret"})
    await verify_user("d@b.com")
    tokens = (
        await client.post(LOGIN, json={"email": "d@b.com", "password": "supersecret"})
    ).json()
    # A refresh token must not authenticate normal endpoints.
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert r.status_code == 401

    # But it works at the refresh endpoint.
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


RESET_REQUEST = "/api/v1/auth/reset-password/request"
RESET_CONFIRM = "/api/v1/auth/reset-password/confirm"


async def test_password_reset_flow(client: AsyncClient, verify_user) -> None:
    await client.post(REGISTER, json={"email": "r@b.com", "password": "supersecret"})
    await verify_user("r@b.com")

    r = await client.post(RESET_REQUEST, json={"email": "r@b.com"})
    assert r.status_code == 202
    token = r.json()["reset_token"]  # surfaced in debug (test env)
    assert token

    # A reset email is queued with the reset link.
    reset_mail = next(
        m for m in await _queued_emails("r@b.com") if "initialis" in m.subject.lower()
    )
    assert "/reset-password?token=" in reset_mail.body_text

    r = await client.post(
        RESET_CONFIRM, json={"token": token, "new_password": "brandnewpass"}
    )
    assert r.status_code == 200

    # Old password no longer works, new one does.
    assert (
        await client.post(LOGIN, json={"email": "r@b.com", "password": "supersecret"})
    ).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": "r@b.com", "password": "brandnewpass"})
    ).status_code == 200


async def test_password_reset_unknown_email_no_enumeration(client: AsyncClient) -> None:
    r = await client.post(RESET_REQUEST, json={"email": "ghost@b.com"})
    assert r.status_code == 202
    assert r.json()["reset_token"] is None
    assert await _queued_emails("ghost@b.com") == []


async def test_reset_token_rejected_as_access(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "rt@b.com", "password": "supersecret"})
    token = (await client.post(RESET_REQUEST, json={"email": "rt@b.com"})).json()[
        "reset_token"
    ]
    # A reset token must not authenticate normal endpoints.
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401


ME = "/api/v1/auth/me"
CHANGE_PASSWORD = "/api/v1/auth/change-password"


async def test_update_profile_changes_name(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.patch(
        ME, json={"first_name": "New", "last_name": "Name"}, headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["first_name"] == "New"
    assert r.json()["last_name"] == "Name"

    # Persisted for the next request.
    me = await client.get(ME, headers=auth_headers)
    assert me.json()["first_name"] == "New"
    assert me.json()["last_name"] == "Name"


async def test_update_profile_requires_auth(client: AsyncClient) -> None:
    r = await client.patch(ME, json={"first_name": "X"})
    assert r.status_code == 401


async def test_change_password_flow(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # auth_headers registers user@example.com / supersecret
    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": "supersecret", "new_password": "evenbetter1"},
        headers=auth_headers,
    )
    assert r.status_code == 200

    old = await client.post(
        LOGIN, json={"email": "user@example.com", "password": "supersecret"}
    )
    assert old.status_code == 401
    new = await client.post(
        LOGIN, json={"email": "user@example.com", "password": "evenbetter1"}
    )
    assert new.status_code == 200


async def test_change_password_rejects_wrong_current(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": "wrongpass", "new_password": "evenbetter1"},
        headers=auth_headers,
    )
    assert r.status_code == 401


# ---- Email verification -------------------------------------------------


async def test_register_queues_verification_email(client: AsyncClient) -> None:
    r = await client.post(
        REGISTER,
        json={"email": "v@b.com", "password": "supersecret", "locale": "fr"},
    )
    assert r.status_code == 201
    assert r.json()["is_verified"] is False

    queued = await _queued_emails("v@b.com")
    assert len(queued) == 1
    assert (
        "confirm" in queued[0].subject.lower()
        or "confirmez" in queued[0].subject.lower()
    )
    assert "/verify-email?token=" in queued[0].body_text


async def test_login_blocked_until_verified(client: AsyncClient) -> None:
    await client.post(
        REGISTER, json={"email": "block@b.com", "password": "supersecret"}
    )

    r = await client.post(
        LOGIN, json={"email": "block@b.com", "password": "supersecret"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "email_not_verified"


async def test_verify_email_then_login(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "ve@b.com", "password": "supersecret"})
    token = _token_from_link((await _queued_emails("ve@b.com"))[0].body_text)

    r = await client.post(VERIFY, json={"token": token})
    assert r.status_code == 200

    login = await client.post(
        LOGIN, json={"email": "ve@b.com", "password": "supersecret"}
    )
    assert login.status_code == 200

    me = await client.get(
        ME, headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert me.json()["is_verified"] is True


async def test_verify_email_rejects_bad_token(client: AsyncClient) -> None:
    r = await client.post(VERIFY, json={"token": "not-a-token"})
    assert r.status_code == 401


async def test_resend_verification_queues_another_email(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "rs@b.com", "password": "supersecret"})
    assert len(await _queued_emails("rs@b.com")) == 1

    r = await client.post(RESEND, json={"email": "rs@b.com"})
    assert r.status_code == 202
    assert len(await _queued_emails("rs@b.com")) == 2


async def test_resend_verification_no_enumeration(client: AsyncClient) -> None:
    r = await client.post(RESEND, json={"email": "ghost@b.com"})
    assert r.status_code == 202
    assert await _queued_emails("ghost@b.com") == []
