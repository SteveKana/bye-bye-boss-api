from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.modules.offers import service as offers_service
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider
from app.modules.offers.repository import JobOfferRepository
from app.modules.offers.service import OffersIngestionService


class _FakeProvider(OfferProvider):
    """An in-memory OfferProvider double -- lets the ingestion/upsert logic
    be tested without going anywhere near real HTTP or credentials."""

    def __init__(self, source_name: str, *, configured: bool = True) -> None:
        self.source_name = source_name
        self._configured = configured
        self.offers_by_keyword: dict[str, list[NormalizedOffer]] = {}
        self.calls: list[str] = []

    def is_configured(self) -> bool:
        return self._configured

    async def search(self, *, keywords: str, limit: int) -> list[NormalizedOffer]:
        self.calls.append(keywords)
        return self.offers_by_keyword.get(keywords, [])


def _offer(external_id: str, title: str) -> NormalizedOffer:
    return NormalizedOffer(
        external_id=external_id,
        title=title,
        url=f"https://example.com/{external_id}",
        company_name="Astek",
    )


async def test_sync_creates_new_offers() -> None:
    provider = _FakeProvider("test_source")
    provider.offers_by_keyword = {
        "chef de projet": [_offer("1", "Chef de projet SI")],
        "product owner": [_offer("2", "Product Owner")],
    }

    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(session, providers=[provider]).sync()

    assert report.fetched == 2
    assert report.created == 2
    assert report.updated == 0
    assert report.skipped_unconfigured == []

    async with AsyncSessionLocal() as session:
        offers = await JobOfferRepository(session).list()
    assert {o.external_id for o in offers} == {"1", "2"}


async def test_sync_updates_existing_offer_instead_of_duplicating() -> None:
    provider = _FakeProvider("test_source")
    provider.offers_by_keyword = {"chef de projet": [_offer("1", "Chef de projet SI")]}

    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()

    # Re-run with the same external_id but an updated title.
    provider.offers_by_keyword = {
        "chef de projet": [_offer("1", "Chef de projet SI Senior")]
    }
    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(session, providers=[provider]).sync()

    assert report.created == 0
    assert report.updated == 1

    async with AsyncSessionLocal() as session:
        offers = await JobOfferRepository(session).list()
    assert len(offers) == 1
    assert offers[0].title == "Chef de projet SI Senior"


async def test_sync_skips_unconfigured_providers() -> None:
    configured = _FakeProvider("configured_source", configured=True)
    unconfigured = _FakeProvider("unconfigured_source", configured=False)

    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(
            session, providers=[configured, unconfigured]
        ).sync()

    assert report.skipped_unconfigured == ["unconfigured_source"]
    # An unconfigured provider is never even asked to search.
    assert unconfigured.calls == []


async def test_sync_embeds_new_offers(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_get_embedding(text: str):
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(offers_service.embeddings, "get_embedding", _fake_get_embedding)

    provider = _FakeProvider("test_source")
    provider.offers_by_keyword = {"chef de projet": [_offer("1", "Chef de projet SI")]}

    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()

    assert calls == ["Chef de projet SI "]
    async with AsyncSessionLocal() as session:
        offers = await JobOfferRepository(session).list()
    assert offers[0].embedding == [0.1, 0.2, 0.3]


async def test_sync_does_not_reembed_an_unchanged_offer(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_get_embedding(text: str):
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(offers_service.embeddings, "get_embedding", _fake_get_embedding)

    provider = _FakeProvider("test_source")
    provider.offers_by_keyword = {"chef de projet": [_offer("1", "Chef de projet SI")]}

    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()
    assert len(calls) == 1

    # Re-run with the exact same title/description -- no new embedding call.
    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()
    assert len(calls) == 1


async def test_sync_reembeds_when_title_changes(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_get_embedding(text: str):
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(offers_service.embeddings, "get_embedding", _fake_get_embedding)

    provider = _FakeProvider("test_source")
    provider.offers_by_keyword = {"chef de projet": [_offer("1", "Chef de projet SI")]}
    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()
    assert len(calls) == 1

    provider.offers_by_keyword = {
        "chef de projet": [_offer("1", "Chef de projet SI Senior")]
    }
    async with AsyncSessionLocal() as session:
        await OffersIngestionService(session, providers=[provider]).sync()
    assert len(calls) == 2
