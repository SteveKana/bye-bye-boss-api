from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.matching import regret_jobs, simplyhired_gateway
from app.modules.matching.repository import CompanyRegretRepository
from app.modules.matching.simplyhired_gateway import SimplyHiredRatings
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


async def _make_offer(**overrides) -> JobOffer:
    defaults = {
        "source": "test",
        "external_id": str(uuid.uuid4()),
        "title": "Poste",
        "url": "https://example.com/offre",
    }
    defaults.update(overrides)
    async with AsyncSessionLocal() as session:
        offer = await JobOfferRepository(session).create(JobOffer(**defaults))
        await session.commit()
        return offer


async def test_refresh_all_computes_every_distinct_company(monkeypatch) -> None:
    await _make_offer(company_name="Astek")
    await _make_offer(company_name="Onepoint")
    # Same company again, different offer -- must not be scraped twice.
    await _make_offer(company_name="Astek")
    # Blank company name -- must be skipped entirely.
    await _make_offer(company_name="")

    seen: list[str] = []

    async def _fake_ratings(company_name):
        seen.append(company_name)
        return SimplyHiredRatings(
            overall_rating=4.0, review_count=20, source_url="https://example.test"
        )

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _fake_ratings)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    await regret_jobs.refresh_all_company_regret_profiles()

    assert sorted(seen) == ["Astek", "Onepoint"]

    async with AsyncSessionLocal() as session:
        repo = CompanyRegretRepository(session)
        astek = await repo.get_by_key("astek")
        onepoint = await repo.get_by_key("onepoint")

    assert astek is not None and astek.regret_availability == "available"
    assert onepoint is not None and onepoint.regret_availability == "available"


async def test_refresh_all_skips_a_failing_company_without_aborting(
    monkeypatch,
) -> None:
    await _make_offer(company_name="Astek")
    await _make_offer(company_name="BrokenCo")

    async def _flaky(company_name):
        if company_name == "BrokenCo":
            raise RuntimeError("simplyhired down")
        return SimplyHiredRatings(
            overall_rating=4.0, review_count=20, source_url="https://example.test"
        )

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _flaky)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    await regret_jobs.refresh_all_company_regret_profiles()

    async with AsyncSessionLocal() as session:
        astek = await CompanyRegretRepository(session).get_by_key("astek")

    assert astek is not None and astek.regret_availability == "available"


async def test_refresh_all_does_nothing_when_disabled(monkeypatch) -> None:
    await _make_offer(company_name="Astek")

    calls = {"count": 0}

    async def _fake_ratings(company_name):
        calls["count"] += 1
        return None

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _fake_ratings)
    monkeypatch.setattr(get_settings(), "REGRET_MONTHLY_REFRESH_ENABLED", False)

    await regret_jobs.refresh_all_company_regret_profiles()

    assert calls["count"] == 0
