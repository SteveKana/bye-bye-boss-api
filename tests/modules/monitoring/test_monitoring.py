from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.core.monitoring_events import record_ai_usage, report_incident
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.mailer.models import EmailMessage
from app.modules.monitoring.common import normalize_path
from app.modules.monitoring.models import AnnouncementOptOut, Incident, PageView
from app.modules.monitoring.tokens import create_unsubscribe_token

M = "/api/v1/monitoring"


async def _login(client: AsyncClient, email: str, *, admin: bool = False, **fields):
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "first_name": "Julie"},
    )
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).one()
        user.isadmin = admin
        for key, value in fields.items():
            setattr(user, key, value)
        session.add(user)
        await session.commit()
        user_id = user.id
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret"}
    )
    return user_id, {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin(client: AsyncClient):
    return await _login(client, "admin@example.com", admin=True, is_verified=True)


async def test_every_admin_endpoint_refuses_regular_users_and_anonymous(
    client: AsyncClient,
) -> None:
    _, headers = await _login(client, "user@example.com", is_verified=True)
    for path in ("overview", "behavior", "users", "incidents", "announcements"):
        assert (await client.get(f"{M}/{path}")).status_code == 401
        assert (await client.get(f"{M}/{path}", headers=headers)).status_code == 403


async def test_overview_behavior_and_users_answer_on_an_empty_platform(
    client: AsyncClient, admin
) -> None:
    _, headers = admin
    overview = (await client.get(f"{M}/overview", headers=headers)).json()
    assert overview["tiles"]["users_total"] == 1
    assert len(overview["signups"]) == 30
    assert len(overview["matching_funnel"]) == 4
    assert {h["key"] for h in overview["health"]} >= {"api", "database", "email"}

    behavior = (await client.get(f"{M}/behavior", headers=headers)).json()
    assert behavior["tracking_since"] is None
    assert behavior["tiles"]["cost_today_eur"] is None
    assert behavior["funnel"][0]["key"] == "signed_up"

    users = (await client.get(f"{M}/users", headers=headers)).json()
    assert users[0]["email"] == "admin@example.com"
    assert users[0]["is_admin"] is True


async def test_funnel_and_accounts_to_follow(client: AsyncClient, admin) -> None:
    _, headers = admin
    await _login(client, "nover@example.com")  # never confirmed
    nocv_id, _ = await _login(client, "nocv@example.com", is_verified=True)
    cv_id, _ = await _login(client, "cv@example.com", is_verified=True)
    async with AsyncSessionLocal() as session:
        session.add(
            CandidateProfile(
                user_id=cv_id,
                status=ProfileStatus.complete.value,
                verification_completed_at=utcnow(),
            )
        )
        await session.commit()

    behavior = (await client.get(f"{M}/behavior", headers=headers)).json()
    steps = {s["key"]: s["value"] for s in behavior["funnel"]}
    # The admin account is not counted as a user of the product.
    assert steps["signed_up"] == 3
    assert steps["verified"] == 2
    assert steps["cv_imported"] == 1
    assert steps["profile_verified"] == 1
    follow = {a["stage"]: a for a in behavior["accounts_to_follow"]}
    assert follow["unverified"]["action"] == "resend_verification"
    assert follow["unverified"]["email"] == "nover@example.com"
    assert follow["no_cv"]["user_id"] == str(nocv_id)
    assert behavior["signup_modes"][0]["count"] == 3


async def test_page_tracking_stores_normalised_paths_only_for_logged_in_users(
    client: AsyncClient, admin
) -> None:
    _, headers = admin
    uid, user_headers = await _login(client, "u@example.com", is_verified=True)
    offer = uuid.uuid4()
    for path in (f"/opportunites/{offer}?utm=x", "/dashboard", "/admin/users"):
        r = await client.post(
            f"{M}/track/page", json={"path": path}, headers=user_headers
        )
        assert r.status_code == 204
    assert (
        await client.post(f"{M}/track/page", json={"path": "/x"})
    ).status_code == 401

    async with AsyncSessionLocal() as session:
        paths = sorted(v.path for v in (await session.exec(select(PageView))).all())
    assert paths == ["/dashboard", "/opportunites/:id"]

    behavior = (await client.get(f"{M}/behavior", headers=headers)).json()
    assert behavior["tiles"]["page_views"] == 2
    assert behavior["tiles"]["active_users"] == 1
    assert behavior["tracking_since"] is not None
    labels = {p["path"]: p["label"] for p in behavior["top_pages"]}
    assert labels["/opportunites/:id"] == "Détail d'une offre"
    users = (await client.get(f"{M}/users", headers=headers)).json()
    seen = {u["user_id"]: u["last_seen"] for u in users}
    assert seen[str(uid)] is not None


def test_normalize_path() -> None:
    assert normalize_path("/a/b/?x=1#y") == "/a/b"
    assert normalize_path("/offres/12345") == "/offres/:id"
    assert normalize_path("/admin") is None
    assert normalize_path("/annonces/desinscription") is None
    assert normalize_path("pas-un-chemin") is None


async def test_browser_errors_are_grouped_and_reopen_when_they_recur(
    client: AsyncClient, admin
) -> None:
    _, headers = admin
    payload = {
        "message": "Cannot read properties of undefined (reading 'map')",
        "path": "/opportunites",
        "user_agent": "Mozilla/5.0 (iPhone) Safari/604 Mobile",
    }
    for _ in range(3):  # anonymous visitors can report too
        assert (
            await client.post(f"{M}/track/client-error", json=payload)
        ).status_code == 204

    listing = (await client.get(f"{M}/incidents", headers=headers)).json()
    assert len(listing["items"]) == 1
    item = listing["items"][0]
    assert item["count"] == 3 and item["status"] == "new" and item["kind"] == "browser"
    assert item["context"] == "Navigateur · Safari mobile"
    assert listing["tiles"]["browser_errors"] == 3
    assert sum(d["count"] for d in listing["per_day"]) == 3

    patched = await client.patch(
        f"{M}/incidents/{item['id']}", json={"status": "resolved"}, headers=headers
    )
    assert patched.json()["status"] == "resolved" and patched.json()["resolved_at"]
    assert (await client.get(f"{M}/incidents", headers=headers)).json()["items"] == []
    resolved = await client.get(f"{M}/incidents?status=resolved", headers=headers)
    assert len(resolved.json()["items"]) == 1

    await client.post(f"{M}/track/client-error", json=payload)
    again = (await client.get(f"{M}/incidents", headers=headers)).json()["items"][0]
    assert (
        again["status"] == "new"
        and again["count"] == 4
        and again["resolved_at"] is None
    )

    detail = (await client.get(f"{M}/incidents/{item['id']}", headers=headers)).json()
    assert detail["where"] == "/opportunites"
    assert len(detail["occurrences"]) == 4


async def test_reported_incident_keeps_who_was_affected(
    client: AsyncClient, admin
) -> None:
    _, headers = admin
    uid, _ = await _login(client, "victim@example.com", is_verified=True)
    for _ in range(2):
        await report_incident(
            kind="cv",
            fingerprint="cv:import:test",
            title="Import du CV impossible",
            context="Import CV",
            technical_cause="boom",
            user_message="Impossible de lire votre CV",
            user_id=uid,
        )
    listing = (await client.get(f"{M}/incidents", headers=headers)).json()
    assert listing["items"][0]["count"] == 2
    assert listing["items"][0]["affected_users"] == 1
    detail = (
        await client.get(f"{M}/incidents/{listing['items'][0]['id']}", headers=headers)
    ).json()
    assert detail["users"] == ["victim@example.com"]
    assert detail["user_message"] == "Impossible de lire votre CV"


async def test_ai_usage_becomes_an_estimated_cost(client: AsyncClient, admin) -> None:
    _, headers = admin
    await record_ai_usage(
        stage="analysis",
        model="gpt-5",
        requests=10,
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    await record_ai_usage(
        stage="prefilter",
        model="gpt-5-mini",
        requests=100,
        input_tokens=2_000_000,
        output_tokens=0,
    )
    settings = get_settings()
    behavior = (await client.get(f"{M}/behavior", headers=headers)).json()
    # gpt-5: 1.25 + 1.0 = 2.25 USD; gpt-5-mini: 0.5 USD; halved by the Batch
    # discount, converted to EUR.
    expected = (
        (2.25 + 0.5)
        * settings.MONITORING_BATCH_DISCOUNT
        * settings.MONITORING_USD_TO_EUR
    )
    assert behavior["tiles"]["cost_today_eur"] == pytest.approx(expected, abs=0.01)
    today = behavior["cost_daily"][-1]
    assert today["detailed_eur"] > today["prefilter_eur"] > 0
    assert "Estimation" in behavior["cost_note"]


async def _emails():
    async with AsyncSessionLocal() as session:
        return list((await session.exec(select(EmailMessage))).all())


async def test_announcement_flow_needs_a_test_and_a_matching_count(
    client: AsyncClient, admin
) -> None:
    admin_id, headers = admin
    await _login(client, "a@example.com", is_verified=True, first_name="Alice")
    bob_id, _ = await _login(
        client, "b@example.com", is_verified=True, first_name="Bob"
    )
    await _login(client, "c@example.com")  # unverified: not in "verified"
    async with AsyncSessionLocal() as session:
        session.add(AnnouncementOptOut(user_id=bob_id))
        await session.commit()

    aud = {
        a["key"]: a["count"]
        for a in (
            await client.get(f"{M}/announcements/audiences", headers=headers)
        ).json()
    }
    # admin + alice are reachable, bob unsubscribed, c unconfirmed.
    assert aud["verified"] == 2 and aud["unverified"] == 1 and aud["all"] == 3

    content = {"subject": "Nouveau", "body": "Bonjour {{prénom}},\n\nTout est à 8h."}
    send = {**content, "audience": "verified", "expected_count": 2}

    refused = await client.post(f"{M}/announcements", json=send, headers=headers)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "test_required"

    preview = (
        await client.post(f"{M}/announcements/preview", json=content, headers=headers)
    ).json()
    assert "Bonjour Julie," in preview["text"]
    assert "Ne plus recevoir les annonces" in preview["html"]

    assert (
        await client.post(f"{M}/announcements/test", json=content, headers=headers)
    ).status_code == 200
    test_mail = [m for m in await _emails() if m.subject.startswith("[TEST]")]
    assert [m.to_email for m in test_mail] == ["admin@example.com"]

    # Changing the text voids the test.
    other = {**send, "body": "autre texte"}
    assert (
        await client.post(f"{M}/announcements", json=other, headers=headers)
    ).status_code == 400

    stale = await client.post(
        f"{M}/announcements", json={**send, "expected_count": 5}, headers=headers
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "audience_changed"

    done = await client.post(f"{M}/announcements", json=send, headers=headers)
    assert done.status_code == 200
    assert done.json()["sent"] == 2 and done.json()["skipped_unsubscribed"] == 1
    sent = [m for m in await _emails() if m.subject == "Nouveau"]
    assert sorted(m.to_email for m in sent) == ["a@example.com", "admin@example.com"]
    alice = next(m for m in sent if m.to_email == "a@example.com")
    assert "Bonjour Alice," in alice.body_text
    assert "/annonces/desinscription?token=" in alice.body_text
    assert alice.body_html and "/annonces/desinscription?token=" in alice.body_html

    history = (await client.get(f"{M}/announcements", headers=headers)).json()
    assert len(history) == 1 and history[0]["sent"] == 2
    assert history[0]["audience_label"] == "Tous les comptes confirmés"
    assert admin_id


async def test_unsubscribe_link_works_without_login(client: AsyncClient) -> None:
    uid, _ = await _login(client, "x@example.com", is_verified=True)
    token = create_unsubscribe_token(uid)
    info = (await client.get(f"{M}/unsubscribe/info", params={"token": token})).json()
    assert info == {"email": "x***@example.com", "already": False}
    for _ in range(2):  # idempotent
        r = await client.post(f"{M}/unsubscribe", json={"token": token})
        assert r.status_code == 200
    info = (await client.get(f"{M}/unsubscribe/info", params={"token": token})).json()
    assert info["already"] is True
    async with AsyncSessionLocal() as session:
        assert len((await session.exec(select(AnnouncementOptOut))).all()) == 1
    bad = await client.get(f"{M}/unsubscribe/info", params={"token": "nope"})
    assert bad.status_code == 400


async def test_account_actions_resend_link_and_reminder(
    client: AsyncClient, admin
) -> None:
    _, headers = admin
    unverified_id, _ = await _login(client, "late@example.com")
    nocv_id, _ = await _login(client, "nocv@example.com", is_verified=True)
    before = len(await _emails())

    r = await client.post(
        f"{M}/accounts/{unverified_id}/action",
        json={"action": "resend_verification"},
        headers=headers,
    )
    assert r.status_code == 200
    r = await client.post(
        f"{M}/accounts/{nocv_id}/action", json={"action": "reminder"}, headers=headers
    )
    assert r.status_code == 200
    mails = (await _emails())[before:]
    assert {m.to_email for m in mails} == {"late@example.com", "nocv@example.com"}
    reminder = next(m for m in mails if m.to_email == "nocv@example.com")
    assert "CV" in reminder.subject and "/annonces/desinscription" in reminder.body_text

    # Already confirmed -> nothing to resend; unsubscribed -> no reminder.
    refused = await client.post(
        f"{M}/accounts/{nocv_id}/action",
        json={"action": "resend_verification"},
        headers=headers,
    )
    assert refused.status_code == 400
    async with AsyncSessionLocal() as session:
        session.add(AnnouncementOptOut(user_id=nocv_id))
        await session.commit()
    refused = await client.post(
        f"{M}/accounts/{nocv_id}/action", json={"action": "reminder"}, headers=headers
    )
    assert refused.status_code == 400


async def test_deleting_an_account_purges_its_monitoring_data(
    client: AsyncClient, admin
) -> None:
    uid, headers = await _login(client, "bye@example.com", is_verified=True)
    await client.post(f"{M}/track/page", json={"path": "/dashboard"}, headers=headers)
    await report_incident(
        kind="cv", fingerprint="cv:x", title="t", context="c", user_id=uid
    )
    from app.core.events import event_bus
    from app.modules.auth import UserDeletionRequested

    await event_bus.emit(UserDeletionRequested(user_id=uid, email="bye@example.com"))
    async with AsyncSessionLocal() as session:
        assert (await session.exec(select(PageView))).all() == []
        incident = (await session.exec(select(Incident))).one()
        assert incident.user_ids == []


async def test_cv_optimization_indicator(client: AsyncClient, admin) -> None:
    from datetime import timedelta

    from app.modules.matching.cv_optimization_models import CVOptimization
    from app.modules.matching.models import CandidateMatch
    from app.modules.offers.models import JobOffer

    _, headers = admin
    uid, _ = await _login(client, "opt@example.com", is_verified=True)
    other, _ = await _login(client, "idle@example.com", is_verified=True)
    async with AsyncSessionLocal() as session:
        profile = CandidateProfile(
            user_id=uid, status=ProfileStatus.complete.value, raw_text="cv"
        )
        session.add(profile)
        offers = [
            JobOffer(
                source="t",
                external_id=f"o{i}",
                title=f"Offre {i}",
                url=f"https://e.com/{i}",
            )
            for i in range(3)
        ]
        session.add_all(offers)
        await session.flush()
        matches = [
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=o.id,
                career_score=50,
                ats_score=50,
                ats_potential=60,
                computed_at=utcnow(),
            )
            for o in offers
        ]
        session.add_all(matches)
        await session.flush()
        now = utcnow()
        session.add(CVOptimization(candidate_match_id=matches[0].id, computed_at=now))
        session.add(
            CVOptimization(
                candidate_match_id=matches[1].id, computed_at=now, confirmed_at=now
            )
        )
        old = CVOptimization(candidate_match_id=matches[2].id, computed_at=now)
        old.created_at = now - timedelta(days=40)
        session.add(old)
        await session.commit()

    behavior = (await client.get(f"{M}/behavior?days=7", headers=headers)).json()
    cv = behavior["cv_optimization"]
    assert cv["generated_today"] == 2
    assert cv["generated"] == 2
    assert cv["kept"] == 1
    assert cv["users"] == 1
    assert cv["per_user"] == 2.0
    assert {o["title"] for o in cv["top_offers"]} == {"Offre 0", "Offre 1"}
    steps = {s["key"]: s["value"] for s in behavior["funnel"]}
    assert steps["cv_optimized"] == 1
    assert other != uid


async def test_notification_channels_are_counted_per_account(
    client: AsyncClient, admin
) -> None:
    """Admin > Vue d'ensemble counts accounts per configured alert channel (an
    account with several channels counts in each one); Admin > Comptes lists
    the channels of every account. An account with every channel off, or with
    no preference row, has none."""
    from app.modules.notifications.models import NotificationPreference

    _, headers = admin
    both_id, _ = await _login(client, "both@example.com", is_verified=True)
    wa_id, _ = await _login(client, "wa@example.com", is_verified=True)
    off_id, _ = await _login(client, "off@example.com", is_verified=True)
    norow_id, _ = await _login(client, "norow@example.com", is_verified=True)

    async with AsyncSessionLocal() as session:
        # Registration already created a default row (email on) for everyone.
        rows = {
            p.user_id: p
            for p in (await session.exec(select(NotificationPreference))).all()
        }
        both = rows[both_id]
        both.whatsapp_enabled = True
        both.whatsapp_phone_number = "+33612345678"
        wa = rows[wa_id]
        wa.email_enabled = False
        wa.whatsapp_enabled = True
        wa.whatsapp_phone_number = "+33612345679"
        rows[off_id].email_enabled = False
        await session.delete(rows[norow_id])
        await session.commit()

    overview = (await client.get(f"{M}/overview", headers=headers)).json()
    counts = overview["tiles"]["channels_configured"]
    # The admin account (default row, email on) is not a product user but is
    # an active account like any other here: email = admin + both.
    assert counts["whatsapp"] == 2
    assert counts["discord"] == 0
    assert counts["email"] == 2
    assert counts["none"] == 2  # off + norow

    users = {
        u["email"]: u for u in (await client.get(f"{M}/users", headers=headers)).json()
    }
    assert users["both@example.com"]["channels"] == ["email", "whatsapp"]
    assert users["wa@example.com"]["channels"] == ["whatsapp"]
    assert users["off@example.com"]["channels"] == []
    assert users["off@example.com"]["alerts_enabled"] is False
    assert users["norow@example.com"]["channels"] == []
