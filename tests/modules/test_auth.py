from __future__ import annotations

import re

from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.mailer.models import EmailMessage

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
VERIFY = "/api/v1/auth/verify-email"
RESEND = "/api/v1/auth/resend-verification"
GOOGLE = "/api/v1/auth/google"


def _google_claims(email: str, *, sub: str = "google-sub-1", **overrides) -> dict:
    return {
        "email": email,
        "sub": sub,
        "given_name": "Ada",
        "family_name": "Lovelace",
        "email_verified": True,
        **overrides,
    }


def _mock_google(monkeypatch, claims: dict) -> None:
    # verify_google_id_token itself is covered end-to-end in
    # test_google_oauth.py -- these tests are about what AuthService and the
    # route do with the claims it returns, not about JWT verification again.
    monkeypatch.setattr(get_settings(), "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(
        auth_service, "verify_google_id_token", lambda token, *, client_id: claims
    )


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


async def test_register_signs_in_immediately(client: AsyncClient) -> None:
    # Steve's call: registering behaves like Google sign-in -- a full
    # session right away, no separate login step and no wait on email
    # verification (see test_login_still_works_when_unverified below).
    payload = {"email": "a@b.com", "password": "supersecret", "first_name": "A"}
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 201
    tokens = r.json()
    assert tokens["token_type"] == "bearer"

    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "a@b.com"
    assert body["first_name"] == "A"
    assert body["isadmin"] is False
    assert body["is_verified"] is False
    assert body["subscription"] == "standard"
    assert body["last_rescoring_time"] is None


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

    queued = await _queued_emails("v@b.com")
    assert len(queued) == 1
    assert (
        "confirm" in queued[0].subject.lower()
        or "confirmez" in queued[0].subject.lower()
    )
    assert "/verify-email?token=" in queued[0].body_text


async def test_login_still_works_when_unverified(client: AsyncClient) -> None:
    # Verification is informational only (Steve's call) -- it must never
    # block a login, whether that's the very first one right after
    # registering or, as here, a later one.
    await client.post(
        REGISTER, json={"email": "unverified@b.com", "password": "supersecret"}
    )

    r = await client.post(
        LOGIN, json={"email": "unverified@b.com", "password": "supersecret"}
    )
    assert r.status_code == 200

    me = await client.get(
        ME, headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.json()["is_verified"] is False


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


# ---- Google sign-in -------------------------------------------------------


async def test_google_login_creates_a_new_verified_user(
    client: AsyncClient, monkeypatch
) -> None:
    _mock_google(monkeypatch, _google_claims("newgoogle@b.com"))

    r = await client.post(GOOGLE, json={"id_token": "fake-token"})
    assert r.status_code == 200
    tokens = r.json()
    # Lets the frontend route a brand-new signup into onboarding (CV upload)
    # instead of the dashboard -- same destination a fresh email/password
    # registration gets.
    assert tokens["is_new_user"] is True

    me = await client.get(
        ME, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.json()["email"] == "newgoogle@b.com"
    assert me.json()["first_name"] == "Ada"
    # Google already verified the address -- no confirmation email detour.
    assert me.json()["is_verified"] is True
    assert await _queued_emails("newgoogle@b.com") == []


async def test_google_login_is_not_gated_by_email_verification(
    client: AsyncClient, monkeypatch
) -> None:
    # Password login isn't gated by verification either any more (see
    # test_login_still_works_when_unverified), but a fresh Google account is
    # verified from the moment it's created, regardless.
    _mock_google(monkeypatch, _google_claims("instant@b.com"))
    r = await client.post(GOOGLE, json={"id_token": "fake-token"})
    assert r.status_code == 200


async def test_google_login_signs_into_existing_account_by_email(
    client: AsyncClient, verify_user, monkeypatch
) -> None:
    # A candidate who registered with a password, then later taps "Continuer
    # avec Google" with that same address, lands on the SAME account --
    # never a second, duplicate one (Steve's call).
    await client.post(REGISTER, json={"email": "both@b.com", "password": "supersecret"})
    await verify_user("both@b.com")

    _mock_google(monkeypatch, _google_claims("both@b.com"))
    r = await client.post(GOOGLE, json={"id_token": "fake-token"})

    assert r.status_code == 200
    # Not a new account -- signing into the existing one, so this should
    # land on the dashboard, not onboarding.
    assert r.json()["is_new_user"] is False
    me = await client.get(
        ME, headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    body = me.json()
    assert body["email"] == "both@b.com"

    # Still only one account: the password set at registration still works.
    login = await client.post(
        LOGIN, json={"email": "both@b.com", "password": "supersecret"}
    )
    assert login.status_code == 200


async def test_google_login_rejects_disabled_account(
    client: AsyncClient, verify_user, monkeypatch
) -> None:
    await client.post(
        REGISTER, json={"email": "disabled@b.com", "password": "supersecret"}
    )
    await verify_user("disabled@b.com")
    async with AsyncSessionLocal() as session:
        result = await session.exec(select(User).where(User.email == "disabled@b.com"))
        user = result.one()
        user.is_active = False
        session.add(user)
        await session.commit()

    _mock_google(monkeypatch, _google_claims("disabled@b.com"))
    r = await client.post(GOOGLE, json={"id_token": "fake-token"})

    assert r.status_code == 401


async def test_google_sign_in_not_configured_returns_clear_error(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "GOOGLE_CLIENT_ID", None)
    r = await client.post(GOOGLE, json={"id_token": "fake-token"})
    assert r.status_code == 500


async def test_password_login_rejects_google_only_account(
    client: AsyncClient, monkeypatch
) -> None:
    # An account created via Google (password_hash is None) must not be
    # attackable -- or even usable -- through the password endpoint.
    _mock_google(monkeypatch, _google_claims("googleonly@b.com"))
    await client.post(GOOGLE, json={"id_token": "fake-token"})

    r = await client.post(
        LOGIN, json={"email": "googleonly@b.com", "password": "anything"}
    )
    assert r.status_code == 401


async def test_change_password_rejects_google_only_account_cleanly(
    client: AsyncClient, monkeypatch
) -> None:
    # Must fail like a wrong current password, not crash (password_hash is
    # None here, and bcrypt can't check a password against nothing).
    _mock_google(monkeypatch, _google_claims("googlechangepw@b.com"))
    r = await client.post(GOOGLE, json={"id_token": "fake-token"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await client.post(
        CHANGE_PASSWORD,
        json={"current_password": "anything", "new_password": "brandnewpass1"},
        headers=headers,
    )
    assert r.status_code == 401

    # The reset-password flow (not change-password) is how such a user sets
    # their first password.
    reset = await client.post(RESET_REQUEST, json={"email": "googlechangepw@b.com"})
    token = reset.json()["reset_token"]
    confirm = await client.post(
        RESET_CONFIRM, json={"token": token, "new_password": "brandnewpass1"}
    )
    assert confirm.status_code == 200
    login = await client.post(
        LOGIN, json={"email": "googlechangepw@b.com", "password": "brandnewpass1"}
    )
    assert login.status_code == 200
