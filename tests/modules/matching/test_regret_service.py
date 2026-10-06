from __future__ import annotations

from datetime import timedelta

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.matching import indeed_gateway, simplyhired_gateway
from app.modules.matching.indeed_gateway import IndeedReview
from app.modules.matching.models import CompanyRegretProfile
from app.modules.matching.regret_service import RegretService
from app.modules.matching.repository import (
    CompanyRegretRepository,
    CompanyReviewRepository,
)
from app.modules.matching.simplyhired_gateway import (
    SimplyHiredRatings,
    SimplyHiredReview,
)


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


async def _no_reviews(company_name):
    """Default stub for fetch_company_reviews -- every test below patches
    it unless it's specifically exercising review-fetch/storage behavior,
    so that no test makes a real network call (see RegretService.
    get_or_compute, which now fetches reviews on every fresh compute)."""
    return []


async def test_get_or_compute_unavailable_when_no_page_found(monkeypatch) -> None:
    async def _no_page(company_name):
        return None

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _no_page)
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_unavailable_below_min_reviews(monkeypatch) -> None:
    async def _few_reviews(company_name):
        return _ratings(1)

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _few_reviews)
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
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


def _review(**overrides) -> SimplyHiredReview:
    defaults = {
        "overall_rating": 4.0,
        "job_title": "Chargé de Recrutement (H/F)",
        "location": "Rueil-Malmaison (92)",
        "review_date": None,
        "title": "Semaine type",
        "text": "Bonne ambiance, management de proximité.",
        "pros": "Ambiance, télétravail",
        "cons": "Salaire",
        "source_url": "https://www.simplyhired.fr/browse-jobs/companies/Astek",
    }
    defaults.update(overrides)
    return SimplyHiredReview(**defaults)


async def test_get_or_compute_stores_fetched_reviews(monkeypatch) -> None:
    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _reviews_fn(company_name):
        return [_review(), _review(title="Autre avis", overall_rating=2.0)]

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _reviews_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    assert len(stored) == 2
    assert {row.title for row in stored} == {"Semaine type", "Autre avis"}
    assert stored[0].job_title == "Chargé de Recrutement (H/F)"


async def test_get_or_compute_stores_reviews_even_when_rating_unavailable(
    monkeypatch,
) -> None:
    # A company can have individual review text even if its aggregate
    # rating doesn't clear REGRET_MIN_REVIEWS -- Steve asked for the review
    # text itself, not just as a side effect of a usable score.
    async def _few_reviews_rating(company_name):
        return _ratings(1)

    async def _reviews_fn(company_name):
        return [_review()]

    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_ratings", _few_reviews_rating
    )
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _reviews_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        availability, _ = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    assert len(stored) == 1


async def test_get_or_compute_replaces_stale_reviews_on_refresh(monkeypatch) -> None:
    calls = {"count": 0}

    async def _ratings_fn(company_name):
        return _ratings(50, overall_rating=4.0)

    async def _reviews_fn(company_name):
        calls["count"] += 1
        return [_review(title=f"Avis {calls['count']}")]

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _reviews_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    # The second refresh's single review replaced the first's -- never both
    # accumulated (see CompanyReview's docstring).
    assert len(stored) == 1
    assert stored[0].title == "Avis 2"


async def test_get_or_compute_review_fetch_failure_does_not_break_rating(
    monkeypatch,
) -> None:
    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _broken_reviews(company_name):
        raise RuntimeError("boom")

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _broken_reviews)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "available"
    assert score == 30


async def test_get_or_compute_combines_and_tags_both_review_sources(
    monkeypatch,
) -> None:
    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _simplyhired_reviews(company_name):
        return [_review(title="Avis SimplyHired")]

    async def _indeed_reviews(company_name):
        return [
            IndeedReview(
                overall_rating=2.0,
                job_title="RH",
                location="Paris (75)",
                review_date=None,
                title="Avis Indeed",
                text="Texte Indeed.",
                source_url="https://fr.indeed.com/cmp/Astek/reviews",
            )
        ]

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_reviews", _simplyhired_reviews
    )
    monkeypatch.setattr(indeed_gateway, "is_configured", lambda: True)
    monkeypatch.setattr(indeed_gateway, "fetch_company_reviews", _indeed_reviews)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    by_title = {row.title: row for row in stored}
    assert len(stored) == 2
    assert by_title["Avis SimplyHired"].source == "simplyhired"
    assert by_title["Avis Indeed"].source == "indeed"
    assert by_title["Avis Indeed"].pros == ""
    assert by_title["Avis Indeed"].job_title == "RH"


async def test_get_or_compute_indeed_failure_does_not_drop_simplyhired_reviews(
    monkeypatch,
) -> None:
    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _simplyhired_reviews(company_name):
        return [_review()]

    async def _broken_indeed(company_name):
        raise RuntimeError("brightdata down")

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_reviews", _simplyhired_reviews
    )
    monkeypatch.setattr(indeed_gateway, "is_configured", lambda: True)
    monkeypatch.setattr(indeed_gateway, "fetch_company_reviews", _broken_indeed)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    assert len(stored) == 1
    assert stored[0].source == "simplyhired"


async def test_get_or_compute_preserves_existing_indeed_reviews_once_disabled(
    monkeypatch,
) -> None:
    """The exact scenario Steve plans to use (2026-10-02): run one
    Bright-Data-backed pull, then remove BRIGHTDATA_API_KEY from the server
    so he's never charged again. The next refresh (monthly job, or any
    TTL-expired recompute) must NOT delete the Indeed reviews already
    stored -- "not configured" and "configured but found nothing" must not
    look the same to the repository, or turning the service off would
    silently destroy the very data it was turned off to stop paying for."""

    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _simplyhired_reviews(company_name):
        return [_review(title="Avis SimplyHired")]

    async def _indeed_reviews(company_name):
        return [
            IndeedReview(
                overall_rating=2.0,
                job_title="RH",
                location="Paris (75)",
                review_date=None,
                title="Avis Indeed",
                text="Texte Indeed.",
                source_url="https://fr.indeed.com/cmp/Astek/reviews",
            )
        ]

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_reviews", _simplyhired_reviews
    )
    monkeypatch.setattr(indeed_gateway, "is_configured", lambda: True)
    monkeypatch.setattr(indeed_gateway, "fetch_company_reviews", _indeed_reviews)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    # First refresh: Bright Data is configured -- the Indeed review gets
    # fetched and stored, same as Steve's one-time pull.
    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    # Steve removes BRIGHTDATA_API_KEY from the server -- simulated here by
    # flipping is_configured() back to False.
    monkeypatch.setattr(indeed_gateway, "is_configured", lambda: False)

    # Next refresh (e.g. next month's bulk job, or a forced recompute).
    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    by_source = {row.source: row for row in stored}
    assert "indeed" in by_source  # still there, not silently wiped
    assert by_source["indeed"].title == "Avis Indeed"
    assert "simplyhired" in by_source  # unaffected, refreshed as normal


async def test_get_or_compute_preserves_existing_simplyhired_reviews_on_fetch_failure(
    monkeypatch,
) -> None:
    """Confirmed in production 2026-10-02: SimplyHired started returning 403
    for every company. fetch_company_reviews used to collapse that into []
    exactly like a genuine "zero reviews" result, which would have wiped
    every previously-stored SimplyHired review on the next refresh --
    exactly the outcome Steve explicitly said he doesn't want. A failed
    fetch (None) must leave existing rows untouched; only a successful
    fetch (even an empty list, e.g. a confirmed 404) replaces them."""

    async def _ratings_fn(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _simplyhired_reviews_ok(company_name):
        return [_review(title="Avis SimplyHired")]

    async def _simplyhired_reviews_failed(company_name):
        return None  # simulates a 403/network failure, not "zero reviews"

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_fn)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    # First refresh succeeds -- the review gets stored.
    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_reviews", _simplyhired_reviews_ok
    )
    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    # SimplyHired starts failing (403) -- same as the real incident.
    monkeypatch.setattr(
        simplyhired_gateway, "fetch_company_reviews", _simplyhired_reviews_failed
    )
    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek", force=True)
        await session.commit()

    async with AsyncSessionLocal() as session:
        stored = await CompanyReviewRepository(session).list_by_key("astek")

    assert len(stored) == 1  # still there, not silently wiped
    assert stored[0].title == "Avis SimplyHired"


async def test_get_or_compute_preserves_existing_score_on_ratings_fetch_failure(
    monkeypatch,
) -> None:
    """The other half of the same incident (2026-10-03): fetch_company_
    ratings returning None used to be treated exactly like "too few
    reviews" -- both just overwrote the existing CompanyRegretProfile with
    regret_availability="unavailable". While SimplyHired kept returning 403,
    every forced refresh (the monthly job, or a manual one-off run) was
    silently erasing every previously-computed regret score. A failed fetch
    must leave the existing score untouched; only a successful fetch (even
    one that legitimately lands on "unavailable", e.g. too few reviews)
    may overwrite it."""

    async def _ratings_ok(company_name):
        return _ratings(80, overall_rating=3.5)

    async def _ratings_failed(company_name):
        return None  # simulates a 403/network failure, not "too few reviews"

    monkeypatch.setattr(simplyhired_gateway, "fetch_company_reviews", _no_reviews)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_REVIEWS", 5)

    # First refresh succeeds -- a real score gets stored.
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_ok)
    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute(
            "Astek", force=True
        )
        await session.commit()
    assert availability == "available"
    assert score == 30

    # SimplyHired starts failing (403) -- same as the real incident.
    monkeypatch.setattr(simplyhired_gateway, "fetch_company_ratings", _ratings_failed)
    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute(
            "Astek", force=True
        )
        await session.commit()

    # The previously-computed score must still be reported, not wiped.
    assert availability == "available"
    assert score == 30

    async with AsyncSessionLocal() as session:
        cached = await CompanyRegretRepository(session).get_by_key("astek")

    assert cached is not None
    assert cached.regret_availability == "available"
    assert cached.regret_score == 30
