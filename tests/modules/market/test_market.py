from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.mailer.models import EmailMessage
from app.modules.market import classifier, digest, taxonomy
from app.modules.market.models import (
    MarketGroupWeekly,
    MarketSkillWeekly,
    OfferMarketTag,
    ProfileMarketTag,
)
from app.modules.market.service import (
    MarketCollectionService,
    week_start_of,
)
from app.modules.offers.models import JobOffer

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # a Wednesday
M = "/api/v1/market/admin"


# ------------------------------------------------------------- taxonomy --
def test_normalize_coerces_the_answer_onto_the_closed_list() -> None:
    assert taxonomy.normalize("Data & IA", "Data Analyst") == (
        "Data & IA",
        "Data Analyst",
    )
    assert taxonomy.normalize("Data & IA", "Chef cuisinier") == ("Data & IA", None)
    assert taxonomy.normalize("Inconnu", "Data Analyst") == ("Autre", None)
    assert taxonomy.normalize(None, None) == ("Autre", None)


def test_every_family_has_unique_specialties() -> None:
    for family, specialties in taxonomy.FAMILIES.items():
        assert len(specialties) == len(set(specialties)), family


# ------------------------------------------------------------ classifier --
def test_skill_key_ignores_spelling_differences() -> None:
    assert classifier.skill_key("Power BI") == classifier.skill_key("power-bi")
    assert classifier.skill_key("Node.js") == "nodejs"


def test_parse_offers_answer_filters_and_cleans() -> None:
    raw = """```json
    {"offres": [
      {"n": 1, "famille": "Data & IA", "specialite": "Data Analyst",
       "competences": [{"nom": "SQL", "categorie": "outil"},
                       {"nom": "sql", "categorie": "outil"},
                       {"nom": "Rigueur", "categorie": "bizarre"}]},
      {"n": 9, "famille": "Marketing", "specialite": null, "competences": []}
    ]}
    ```"""
    parsed = classifier.parse_offers_answer(raw, 2)
    assert list(parsed) == [1]  # n=9 is out of range
    assert [s["key"] for s in parsed[1].skills] == ["sql", "rigueur"]
    assert parsed[1].skills[1]["category"] == "metier"  # unknown category


def test_parse_offers_answer_survives_garbage() -> None:
    assert classifier.parse_offers_answer("pas du json", 3) == {}
    assert classifier.parse_offers_answer('{"offres": "x"}', 3) == {}


def test_prompt_lists_the_whole_taxonomy_and_truncates_descriptions() -> None:
    prompt = classifier.build_offers_prompt(
        [classifier.OfferInput("a", "Dev", "x" * 5000)], max_chars=100
    )
    assert "Développeur Back-end" in prompt
    assert "x" * 101 not in prompt


async def test_missing_answer_for_one_offer_falls_back_to_autre(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    get_settings.cache_clear()

    def fake_call(*, prompt, usage):
        return '{"offres": [{"n": 1, "famille": "Marketing", "specialite": null}]}'

    monkeypatch.setattr(classifier, "_call_sync", fake_call)
    tags, _ = await classifier.classify_offers(
        [classifier.OfferInput("a", "t1", ""), classifier.OfferInput("b", "t2", "")]
    )
    assert tags["a"].family == "Marketing"
    assert tags["b"].family == "Autre"
    get_settings.cache_clear()


# ------------------------------------------------------------ collection --
async def _offer(session, external_id: str, created: datetime) -> JobOffer:
    offer = JobOffer(
        source="adzuna",
        external_id=external_id,
        title=f"Offre {external_id}",
        description="desc",
        url=f"https://example.com/{external_id}",
        created_at=created,
    )
    session.add(offer)
    await session.flush()
    return offer


def _tag(offer: JobOffer, specialty: str, skills: list[str]) -> OfferMarketTag:
    return OfferMarketTag(
        job_offer_id=offer.id,
        family="Produit & projet",
        specialty=specialty,
        skills=[
            {"name": s, "key": classifier.skill_key(s), "category": "outil"}
            for s in skills
        ],
        model="test",
    )


async def test_classify_pending_only_files_untagged_offers(monkeypatch) -> None:
    async with AsyncSessionLocal() as session:
        a = await _offer(session, "a", NOW)
        b = await _offer(session, "b", NOW)
        session.add(_tag(a, "Product Owner", ["Jira"]))
        await session.commit()
        b_id = str(b.id)

        seen: list[str] = []

        async def fake_classify(offers):
            seen.extend(o.key for o in offers)
            tags = {
                o.key: classifier.OfferTags("Produit & projet", "Product Owner", [])
                for o in offers
            }
            return tags, classifier.ClassifyUsage(input_tokens=10, output_tokens=2)

        monkeypatch.setattr(classifier, "classify_offers", fake_classify)
        report = await MarketCollectionService(session).classify_pending()

        assert seen == [b_id]
        assert report.classified == 1 and report.failed == 0
        assert len((await session.exec(select(OfferMarketTag))).all()) == 2


async def test_failed_offers_stay_untagged_for_the_next_run(monkeypatch) -> None:
    async with AsyncSessionLocal() as session:
        await _offer(session, "a", NOW)
        await session.commit()

        async def fake_classify(offers):
            return {}, classifier.ClassifyUsage()

        monkeypatch.setattr(classifier, "classify_offers", fake_classify)
        report = await MarketCollectionService(session).classify_pending()
        assert report.failed == 1 and report.classified == 0
        assert (await session.exec(select(OfferMarketTag))).all() == []


async def test_aggregate_counts_per_specialty_and_family_and_is_idempotent() -> None:
    monday = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)  # previous, complete week
    async with AsyncSessionLocal() as session:
        for i, skills in enumerate([["Jira", "SQL"], ["Jira"], ["Scrum"]]):
            offer = await _offer(session, f"po{i}", monday)
            session.add(_tag(offer, "Product Owner", skills))
        offer = await _offer(session, "ba", monday)
        session.add(_tag(offer, "Business Analyst / AMOA", ["SQL"]))
        # An offer older than the retention window must be ignored.
        old = await _offer(session, "old", datetime(2026, 9, 1, tzinfo=UTC))
        session.add(_tag(old, "Product Owner", ["Jira"]))
        await session.commit()

        service = MarketCollectionService(session)
        await service.aggregate_weeks(NOW)
        await service.aggregate_weeks(NOW)  # idempotent

        groups = {
            (g.specialty): g
            for g in (await session.exec(select(MarketGroupWeekly))).all()
        }
        assert groups["Product Owner"].offers_total == 3
        assert groups[""].offers_total == 4  # the whole family
        assert groups["Product Owner"].is_complete is True
        assert groups["Product Owner"].week_start == date(2026, 10, 26)

        skills = {
            (s.specialty, s.skill_key): s.offers_with_skill
            for s in (await session.exec(select(MarketSkillWeekly))).all()
        }
        assert skills[("Product Owner", "jira")] == 2
        assert skills[("", "sql")] == 2
        assert ("Product Owner", "scrum") in skills
        assert all(g.week_start >= date(2026, 10, 20) for g in groups.values())


def test_week_start_is_the_monday_in_paris_time() -> None:
    # Sunday 23:30 UTC is already Monday in Paris (UTC+1 in winter).
    assert week_start_of(datetime(2026, 11, 1, 23, 30, tzinfo=UTC)) == date(2026, 11, 2)


# ---------------------------------------------------------------- digest --
def _week(day: int, total: int, skills: dict[str, int], complete: bool = True):
    return digest.WeekCounts(
        week_start=date(2026, 10, day),
        offers_total=total,
        is_complete=complete,
        skills={classifier.skill_key(n): (n, "outil", c) for n, c in skills.items()},
    )


def test_digest_shares_trends_and_cv_match() -> None:
    weeks = [
        _week(12, 100, {"Jira": 60, "SAFe": 20, "SQL": 20}),
        _week(19, 100, {"Jira": 64, "SAFe": 26, "SQL": 27}),
    ]
    exact, squashed = digest.cv_skill_keys(["Jira"], "Je pilote des projets")
    data = digest.build_digest(
        weeks=weeks,
        label="Product Owner",
        level="specialty",
        first_name="Julie",
        cv_exact=exact,
        cv_squashed=squashed,
    )
    assert data is not None
    by_name = {s.name: s for s in data.top_skills}
    assert by_name["Jira"].share_pct == 64 and by_name["Jira"].in_cv is True
    assert by_name["SAFe"].share_pct == 26 and by_name["SAFe"].trend_pts == 6
    assert by_name["SAFe"].in_cv is False
    assert by_name["SQL"].trend_pts == 7
    assert data.offers_analysed == 200  # both weeks, shares are the latest's
    assert len(data.evolution) == 3 and data.evolution[0].weeks[0][1] == 60
    assert len(data.actions) == 2  # Jira is in the CV, SQL and SAFe are not
    assert any("SAFe" in a for a in data.actions)
    assert data.forecasts == []  # only 2 weeks: no forecast yet


def test_digest_forecasts_need_a_steady_rise_over_three_weeks() -> None:
    weeks = [
        _week(5, 100, {"SAFe": 20}),
        _week(12, 100, {"SAFe": 25}),
        _week(19, 100, {"SAFe": 31}),
    ]
    data = digest.build_digest(
        weeks=weeks, label="PO", level="specialty", first_name=None,
        cv_exact=set(), cv_squashed="",
    )  # fmt: skip
    assert data is not None and len(data.forecasts) == 1
    assert "20 % → 31 %" in data.forecasts[0]


def test_trend_is_hidden_when_a_week_is_too_small() -> None:
    weeks = [_week(12, 10, {"Jira": 5}), _week(19, 100, {"Jira": 60})]
    data = digest.build_digest(
        weeks=weeks, label="PO", level="family", first_name=None,
        cv_exact=set(), cv_squashed="",
    )  # fmt: skip
    assert data is not None and data.top_skills[0].trend_pts is None


def test_short_skill_names_only_match_when_listed_exactly() -> None:
    exact, squashed = digest.cv_skill_keys(["Python"], "expérience en recrutement")
    assert digest.has_skill("python", exact, squashed)
    assert not digest.has_skill("r", exact, squashed)  # would match "recrutement"


# ---------------------------------------------------------------- routes --
async def _login(client: AsyncClient, email: str, *, admin: bool = False):
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "first_name": "Julie"},
    )
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).one()
        user.isadmin = admin
        user.is_verified = True
        session.add(user)
        await session.commit()
        user_id = user.id
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret"}
    )
    return user_id, {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _seed_profile_and_stats(user_id: uuid.UUID, offers_per_week: int) -> None:
    async with AsyncSessionLocal() as session:
        profile = CandidateProfile(
            user_id=user_id,
            status=ProfileStatus.complete.value,
            first_name="Julie",
            headline="Product Owner",
            skills=["Jira"],
        )
        session.add(profile)
        await session.flush()
        session.add(
            ProfileMarketTag(
                profile_id=profile.id,
                family="Produit & projet",
                specialty="Product Owner",
                model="test",
            )
        )
        for day, jira, safe in ((12, 60, 20), (19, 64, 26)):
            for level in ("Product Owner", ""):
                session.add(
                    MarketGroupWeekly(
                        week_start=date(2026, 10, day),
                        family="Produit & projet",
                        specialty=level,
                        offers_total=offers_per_week,
                        is_complete=True,
                    )
                )
                for name, count in (("Jira", jira), ("SAFe", safe)):
                    session.add(
                        MarketSkillWeekly(
                            week_start=date(2026, 10, day),
                            family="Produit & projet",
                            specialty=level,
                            skill_key=classifier.skill_key(name),
                            skill_label=name,
                            category="outil",
                            offers_with_skill=count * offers_per_week // 100,
                        )
                    )
        await session.commit()


async def test_admin_routes_refuse_regular_users(client: AsyncClient) -> None:
    user_id, headers = await _login(client, "user@example.com")
    body = {"user_id": str(user_id)}
    assert (await client.post(f"{M}/digest/send", json=body)).status_code == 401
    resp = await client.post(f"{M}/digest/send", json=body, headers=headers)
    assert resp.status_code == 403


async def test_preview_and_send_a_digest(client: AsyncClient) -> None:
    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    body = {"user_id": str(target_id)}

    preview = (
        await client.post(f"{M}/digest/preview", json=body, headers=admin)
    ).json()
    assert preview["ready"] is True
    assert "Product Owner" in preview["subject"]
    assert "SAFe" in preview["html"] and "absent" in preview["html"]
    assert preview["below_threshold"] is False

    sent = await client.post(f"{M}/digest/send", json=body, headers=admin)
    assert sent.status_code == 200
    async with AsyncSessionLocal() as session:
        mails = (await session.exec(select(EmailMessage))).all()
    digest_mails = [m for m in mails if "veille marché" in m.subject]
    assert len(digest_mails) == 1
    assert digest_mails[0].to_email == "julie@example.com"


async def test_below_threshold_is_refused_unless_the_admin_overrides(
    client: AsyncClient,
) -> None:
    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=20)  # 40 < 100
    body = {"user_id": str(target_id)}

    preview = (
        await client.post(f"{M}/digest/preview", json=body, headers=admin)
    ).json()
    assert preview["ready"] is False and "100" in preview["reason"]
    refused = await client.post(f"{M}/digest/send", json=body, headers=admin)
    assert refused.status_code == 400

    forced = {**body, "ignore_threshold": True}
    ok = await client.post(f"{M}/digest/send", json=forced, headers=admin)
    assert ok.status_code == 200


async def test_profile_without_a_complete_profile_is_not_found(
    client: AsyncClient,
) -> None:
    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "nocv@example.com")
    resp = await client.post(
        f"{M}/digest/send", json={"user_id": str(target_id)}, headers=admin
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("path", ["preview", "send"])
async def test_unknown_user_is_not_found(client: AsyncClient, path: str) -> None:
    _, admin = await _login(client, "admin@example.com", admin=True)
    resp = await client.post(
        f"{M}/digest/{path}", json={"user_id": str(uuid.uuid4())}, headers=admin
    )
    assert resp.status_code == 404


# ----------------------------------------------------------- unsubscribe --
U = "/api/v1/market/unsubscribe"


async def test_digest_carries_a_working_unsubscribe_link(client: AsyncClient) -> None:
    from app.modules.market.tokens import read_unsubscribe_token

    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    await client.post(
        f"{M}/digest/send", json={"user_id": str(target_id)}, headers=admin
    )
    async with AsyncSessionLocal() as session:
        mails = (await session.exec(select(EmailMessage))).all()
    mail = next(m for m in mails if "veille marché" in m.subject)
    assert "/veille-marche/desinscription?token=" in mail.body_html
    assert "Ne plus recevoir la veille marché" in mail.body_text
    token = mail.body_text.split("desinscription?token=")[1].split()[0]
    assert read_unsubscribe_token(token) == target_id


async def test_unsubscribing_blocks_later_digests(client: AsyncClient) -> None:
    from app.modules.market.tokens import create_unsubscribe_token

    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    token = create_unsubscribe_token(target_id)

    info = (await client.get(f"{U}/info", params={"token": token})).json()
    assert info == {"email": "j***@example.com", "already": False}
    assert (await client.post(U, json={"token": token})).status_code == 200
    assert (await client.post(U, json={"token": token})).status_code == 200  # twice ok
    info = (await client.get(f"{U}/info", params={"token": token})).json()
    assert info["already"] is True

    body = {"user_id": str(target_id)}
    preview = (
        await client.post(f"{M}/digest/preview", json=body, headers=admin)
    ).json()
    assert preview["ready"] is False and "désabonnée" in preview["reason"]
    assert (
        await client.post(f"{M}/digest/send", json=body, headers=admin)
    ).status_code == 400


async def test_other_tokens_cannot_unsubscribe_from_the_digest(
    client: AsyncClient,
) -> None:
    from app.modules.monitoring.tokens import create_unsubscribe_token as announce

    target_id, _ = await _login(client, "julie@example.com")
    wrong = announce(target_id)  # the announcements' link
    assert (await client.post(U, json={"token": wrong})).status_code == 400
    assert (await client.get(f"{U}/info", params={"token": "junk"})).status_code == 400


async def test_zero_offers_explains_the_state_of_the_collection(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "MARKET_ENABLED", True)
    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    async with AsyncSessionLocal() as session:
        profile = CandidateProfile(
            user_id=target_id,
            status=ProfileStatus.complete.value,
            headline="Product Owner",
        )
        session.add(profile)
        await session.flush()
        session.add(
            ProfileMarketTag(
                profile_id=profile.id,
                family="Produit & projet",
                specialty="Product Owner",
                model="test",
            )
        )
        await _offer(session, "a", NOW)
        await session.commit()

    body = {"user_id": str(target_id)}
    preview = (
        await client.post(f"{M}/digest/preview", json=body, headers=admin)
    ).json()
    assert preview["ready"] is False
    assert "aucune offre" in preview["reason"] and "1 en base" in preview["reason"]
    assert "0 analysées" not in preview["reason"]


# ------------------------------------------------------- weekly auto send --
MONDAY = datetime(
    2026, 10, 26, 7, 0, tzinfo=UTC
)  # 08:00 in Paris; covers week of 19 Oct


async def _digest_mails() -> list[EmailMessage]:
    async with AsyncSessionLocal() as session:
        mails = (await session.exec(select(EmailMessage))).all()
    return [m for m in mails if "veille marché" in m.subject]


def test_expected_week_is_the_monday_of_the_week_that_just_ended() -> None:
    from app.modules.market.weekly import expected_week

    assert expected_week(MONDAY) == date(2026, 10, 19)
    assert expected_week(datetime(2026, 10, 30, 12, 0, tzinfo=UTC)) == date(
        2026, 10, 19
    )
    assert expected_week(datetime(2026, 11, 2, 9, 0, tzinfo=UTC)) == date(2026, 10, 26)


async def test_weekly_send_mails_each_eligible_profile_once(
    client: AsyncClient,
) -> None:
    from app.modules.market.models import MarketDigestSend
    from app.modules.market.weekly import send_weekly_digests

    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)

    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(session, MONDAY)
    assert report.sent == 1
    mails = await _digest_mails()
    assert [m.to_email for m in mails] == ["julie@example.com"]
    async with AsyncSessionLocal() as session:
        sends = (await session.exec(select(MarketDigestSend))).all()
    assert [(s.user_id, s.week_start, s.source) for s in sends] == [
        (target_id, date(2026, 10, 19), "auto")
    ]

    # The scheduler retries all Monday: a second run sends nothing.
    async with AsyncSessionLocal() as session:
        again = await send_weekly_digests(session, MONDAY)
    assert again.sent == 0 and again.already_sent == 1
    assert len(await _digest_mails()) == 1


async def test_weekly_send_skips_opted_out_unverified_and_stale(
    client: AsyncClient,
) -> None:
    from app.modules.market.models import MarketDigestOptOut
    from app.modules.market.weekly import send_weekly_digests

    out_id, _ = await _login(client, "out@example.com")
    await _seed_profile_and_stats(out_id, offers_per_week=100)
    async with AsyncSessionLocal() as session:
        session.add(MarketDigestOptOut(user_id=out_id))
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(session, MONDAY)
    assert report.opted_out == 1 and report.sent == 0

    # Unverified address.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "supersecret", "first_name": "N"},
    )
    async with AsyncSessionLocal() as session:
        user = (
            await session.exec(select(User).where(User.email == "new@example.com"))
        ).one()
        session.add(
            CandidateProfile(
                user_id=user.id, status=ProfileStatus.complete.value, headline="PO"
            )
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(session, MONDAY)
    assert report.unverified == 1 and report.sent == 0


async def test_weekly_send_never_sends_outdated_data(
    client: AsyncClient,
) -> None:
    from app.modules.market.weekly import send_weekly_digests

    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(
            session, datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
        )
    assert report.sent == 0
    assert report.stale_data + report.below_threshold == 1
    assert await _digest_mails() == []


async def test_weekly_send_respects_the_test_server_allow_list(
    client: AsyncClient, monkeypatch
) -> None:
    from app.modules.market.weekly import send_weekly_digests

    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    monkeypatch.setattr(
        get_settings(), "NOTIFICATIONS_ALLOWED_EMAILS", "someone@else.com"
    )
    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(session, MONDAY)
    assert report.not_allowed == 1 and report.sent == 0
    assert await _digest_mails() == []


async def test_weekly_send_skips_profiles_below_the_threshold(
    client: AsyncClient,
) -> None:
    from app.modules.market.weekly import send_weekly_digests

    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=10)
    async with AsyncSessionLocal() as session:
        report = await send_weekly_digests(session, MONDAY)
    assert report.below_threshold == 1 and report.sent == 0


async def test_an_admin_send_counts_as_the_weeks_digest(client: AsyncClient) -> None:
    from app.modules.market.models import MarketDigestSend

    _, admin = await _login(client, "admin@example.com", admin=True)
    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    resp = await client.post(
        f"{M}/digest/send", json={"user_id": str(target_id)}, headers=admin
    )
    assert resp.status_code == 200
    async with AsyncSessionLocal() as session:
        sends = (await session.exec(select(MarketDigestSend))).all()
    assert [(s.user_id, s.source) for s in sends] == [(target_id, "admin")]


async def test_the_weekly_job_does_nothing_when_switched_off(
    client: AsyncClient, monkeypatch
) -> None:
    from app.modules.market import jobs

    target_id, _ = await _login(client, "julie@example.com")
    await _seed_profile_and_stats(target_id, offers_per_week=100)
    monkeypatch.setattr(get_settings(), "MARKET_WEEKLY_SEND_ENABLED", False)
    await jobs.send_market_digests()
    assert await _digest_mails() == []


def test_nothing_automatic_unless_switched_on_explicitly() -> None:
    from app.core.config import Settings

    fields = Settings.model_fields
    assert fields["MARKET_ENABLED"].default is False
    assert fields["MARKET_WEEKLY_SEND_ENABLED"].default is False
