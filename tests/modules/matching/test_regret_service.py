from __future__ import annotations

from datetime import timedelta

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.matching import simplyhired_gateway
from app.modules.matching.models import CompanyRegretProfile
from app.modules.matching.regret_service import RegretService
from app.modules.matching.repository import CompanyRegretRepository
from app.modules.matching.simplyhired_gateway import SimplyHiredRatings


def _ratings(
    review_count: int, overall_rating: float = 3.5, **extra
) -> SimplyHiredRatings:
    return SimplyHiredRatings(
        overall_rating=overall_rating,
        review_count=review_count,
        category_scores=extra.pop("category_scores", {"management": 3.2}),
        satisfaction_percent=extra.pop("satisfaction_percent", 48),
        source_url="https://www.simplyhired.fr/browse-jobs/companies/Astek",
    )


async def test_get_or_compute_unavailable_when_no_page_found(monkeypatch) -> None:
    async def _no_page(company_name):
        return None

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _no_page)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_unavailable_below_min_reviews(monkeypatch) -> None:
    async def _few_reviews(company_name):
        return _ratings(1)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _few_reviews)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_scores_and_caches_when_enough_reviews(
    monkeypatch,
) -> None:
    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "available"
    # (5 - 3.5) / 5 * 100 = 30
    assert score == 30

    async with AsyncSessionLocal() as session:
        cached = await CompanyRegretRepository(session).get_by_key("astek")

    assert cached is not None
    assert cached.regret_score == 30
    assert cached.mention_count == 80
    assert cached.overall_rating == 3.5
    assert cached.category_scores == {"management": 3.2}
    assert cached.satisfaction_percent == 48


async def test_get_or_compute_perfect_rating_scores_zero(monkeypatch) -> None:
    async def _ratings_fn(company_name):
        return _ratings(10, overall_rating=5.0)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "available"
    assert score == 0


async def test_get_or_compute_uses_cache_within_ttl_without_refetching(
    monkeypatch,
) -> None:
    calls = {"count": 0}

    async def _ratings_fn(company_name):
        calls["count"] += 1
        return _ratings(50, overall_rating=4.0)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)
    monkeypatch.setattr(get_settings(), "REGRET_CACHE_TTL_DAYS", 30)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert calls["count"] == 1
    assert availability == "available"
    assert score == 20


async def test_get_or_compute_refetches_once_cache_expired(monkeypatch) -> None:
    async def _ratings_fn(company_name):
        return _ratings(50, overall_rating=4.5)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        repo = CompanyRegretRepository(session)

        await repo.create(
            CompanyRegretProfile(
                company_name_key="astek",
                company_name="Astek",
                regret_availability="available",
                regret_score=10,
                mention_count=5,
                computed_at=utcnow() - timedelta(days=31),
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "available"
    assert score == 10  # (5 - 4.5) / 5 * 100 = 10


async def test_get_or_compute_force_refetches_within_ttl(monkeypatch) -> None:
    calls = {"count": 0}

    async def _ratings_fn(company_name):
        calls["count"] += 1
        return _ratings(50, overall_rating=4.0)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)
    monkeypatch.setattr(get_settings(), "REGRET_CACHE_TTL_DAYS", 30)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    assert calls["count"] == 2


async def test_get_or_compute_unavailable_for_blank_company_name() -> None:
    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("  ")

    assert availability == "unavailable"
    assert score is None
