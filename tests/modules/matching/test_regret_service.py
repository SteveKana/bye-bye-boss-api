from __future__ import annotations

from datetime import timedelta

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.matching import reddit_gateway, regret_gateway
from app.modules.matching.models import CompanyRegretProfile
from app.modules.matching.regret_schema import RegretAnalysis, RegretAvailability
from app.modules.matching.regret_service import RegretService
from app.modules.matching.repository import CompanyRegretRepository


def _mentions(count: int, body: str = "avis") -> list[dict]:
    return [{"title": f"post {i}", "body": body, "permalink": ""} for i in range(count)]


async def test_get_or_compute_unavailable_when_no_mentions(monkeypatch) -> None:
    async def _no_mentions(company_name, *, limit: int = 20):
        return []

    monkeypatch.setattr(reddit_gateway, "search_mentions", _no_mentions)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_unavailable_below_min_mentions(monkeypatch) -> None:
    async def _few_mentions(company_name, *, limit: int = 20):
        return [{"title": "x", "body": "y", "permalink": ""}]

    async def _fail_if_called(company_name, mentions):
        raise AssertionError("LLM should not be called below REGRET_MIN_MENTIONS")

    monkeypatch.setattr(reddit_gateway, "search_mentions", _few_mentions)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _fail_if_called)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_scores_and_caches_when_enough_mentions(
    monkeypatch,
) -> None:
    async def _search(company_name, *, limit: int = 20):
        return _mentions(3)

    async def _analyse(company_name, mentions):
        return RegretAnalysis(
            availability=RegretAvailability.available, score=42, reasons=["turnover"]
        )

    monkeypatch.setattr(reddit_gateway, "search_mentions", _search)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _analyse)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "available"
    assert score == 42

    async with AsyncSessionLocal() as session:
        cached = await CompanyRegretRepository(session).get_by_key("astek")

    assert cached is not None
    assert cached.regret_score == 42
    assert cached.mention_count == 3


async def test_get_or_compute_unavailable_when_llm_says_insufficient(
    monkeypatch,
) -> None:
    async def _search(company_name, *, limit: int = 20):
        return _mentions(3, body="hors sujet")

    async def _analyse(company_name, mentions):
        return RegretAnalysis(availability=RegretAvailability.insufficient)

    monkeypatch.setattr(reddit_gateway, "search_mentions", _search)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _analyse)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_unavailable_when_llm_fails(monkeypatch) -> None:
    async def _search(company_name, *, limit: int = 20):
        return _mentions(3)

    async def _analyse(company_name, mentions):
        return None

    monkeypatch.setattr(reddit_gateway, "search_mentions", _search)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _analyse)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert availability == "unavailable"
    assert score is None


async def test_get_or_compute_uses_cache_within_ttl_without_refetching(
    monkeypatch,
) -> None:
    calls = {"count": 0}

    async def _search(company_name, *, limit: int = 20):
        calls["count"] += 1
        return _mentions(3)

    async def _analyse(company_name, mentions):
        return RegretAnalysis(availability=RegretAvailability.available, score=55)

    monkeypatch.setattr(reddit_gateway, "search_mentions", _search)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _analyse)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)
    monkeypatch.setattr(get_settings(), "REGRET_CACHE_TTL_DAYS", 30)

    async with AsyncSessionLocal() as session:
        await RegretService(session).get_or_compute("Astek")
        await session.commit()

    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("Astek")
        await session.commit()

    assert calls["count"] == 1
    assert availability == "available"
    assert score == 55


async def test_get_or_compute_refetches_once_cache_expired(monkeypatch) -> None:
    async def _search(company_name, *, limit: int = 20):
        return _mentions(3)

    async def _analyse(company_name, mentions):
        return RegretAnalysis(availability=RegretAvailability.available, score=60)

    monkeypatch.setattr(reddit_gateway, "search_mentions", _search)
    monkeypatch.setattr(regret_gateway, "analyse_regret", _analyse)
    monkeypatch.setattr(get_settings(), "REGRET_MIN_MENTIONS", 3)

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
    assert score == 60


async def test_get_or_compute_unavailable_for_blank_company_name() -> None:
    async with AsyncSessionLocal() as session:
        availability, score = await RegretService(session).get_or_compute("  ")

    assert availability == "unavailable"
    assert score is None
