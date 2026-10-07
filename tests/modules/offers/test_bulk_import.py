from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import select

from app.core import embeddings
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.matching.models import CandidateMatch
from app.modules.matching.purge import purge_old_offers
from app.modules.offers import JobOffer, OffersIngestionService
from app.modules.offers.providers import france_travail
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider
from app.modules.offers.providers.france_travail import FranceTravailProvider

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


def _fake_api(offers_by_dept: dict[str | None, list[tuple[str, datetime]]]):
    """A France Travail double honouring departement, min/maxCreationDate,
    range and the 1,150 cap, so the crawl's splitting is really exercised."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t"})
        params = dict(request.url.params)
        calls.append(params)
        dept = params.get("departement")
        pool = (
            offers_by_dept[dept]
            if dept
            else [o for lst in offers_by_dept.values() for o in lst]
        )
        lo = datetime.strptime(params["minCreationDate"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        hi = datetime.strptime(params["maxCreationDate"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        found = sorted(
            (o for o in pool if lo <= o[1] <= hi), key=lambda o: o[1], reverse=True
        )
        if not found:
            return httpx.Response(204)
        start, end = (int(x) for x in params["range"].split("-"))
        assert end <= 1149, "range beyond the API's cap"
        page = found[start : end + 1]
        return httpx.Response(
            206,
            headers={"Content-Range": f"offres {start}-{end}/{len(found)}"},
            json={
                "resultats": [
                    {
                        "id": oid,
                        "intitule": f"Offre {oid}",
                        "dateCreation": ts.isoformat(),
                    }
                    for oid, ts in page
                ]
            },
        )

    return handler, calls


async def test_crawl_splits_by_departement_and_time_and_loses_nothing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(france_travail, "_CALL_SPACING_SECONDS", 0)
    monkeypatch.setattr(france_travail, "DEPARTEMENTS", ("75", "13"))
    since = NOW - timedelta(days=15)
    step = timedelta(days=15) / 1400
    data = {
        "75": [(f"p{i}", NOW - i * step) for i in range(200)],
        # 1,300 offers: above the 1,150 cap, so this département must be
        # split by time as well.
        "13": [(f"m{i}", NOW - i * step) for i in range(1300)],
    }
    handler, calls = _fake_api(data)
    provider = FranceTravailProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    seen: list[str] = []
    async for page in provider.crawl(since=since, until=NOW):
        assert len(page) <= 150
        seen.extend(o.external_id for o in page)

    expected = {oid for lst in data.values() for oid, _ in lst}
    assert set(seen) == expected  # nothing lost
    assert len(seen) == len(set(seen))  # nothing read twice
    assert any(c.get("departement") == "13" for c in calls)


class _CrawlProvider(OfferProvider):
    source_name = "france_travail"

    def __init__(self, pages: list[list[NormalizedOffer]]) -> None:
        self.pages = pages

    def is_configured(self) -> bool:
        return True

    async def search(self, *, keywords: str, limit: int) -> list[NormalizedOffer]:
        return []

    async def crawl(self, *, since, until, departement=None) -> AsyncIterator[list]:
        for page in self.pages:
            yield page


def _item(ext: str, title: str = "Comptable") -> NormalizedOffer:
    return NormalizedOffer(
        external_id=ext, title=title, url="https://x", description="desc"
    )


async def test_full_sync_embeds_in_batches_and_is_idempotent(monkeypatch) -> None:
    embedded: list[list[str]] = []

    async def fake_get_embeddings(texts: list[str]):
        embedded.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(embeddings, "get_embeddings", fake_get_embeddings)
    provider = _CrawlProvider([[_item("1"), _item("2")], [_item("3"), _item("1")]])

    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(
            session, providers=[provider]
        ).sync_france_travail_full(since=NOW - timedelta(days=1), until=NOW)
    assert (report.fetched, report.created, report.updated) == (4, 3, 1)
    assert len(embedded) == 2  # one batched call per page, not one per offer

    embedded.clear()
    async with AsyncSessionLocal() as session:
        again = await OffersIngestionService(
            session, providers=[provider]
        ).sync_france_travail_full(since=NOW - timedelta(days=1), until=NOW)
    assert again.created == 0
    assert embedded == [[], []]  # unchanged offers are never re-embedded

    async with AsyncSessionLocal() as session:
        stored = (await session.exec(select(JobOffer))).all()
    assert len(stored) == 3
    assert all(o.embedding == [1.0, 0.0, 0.0] for o in stored)

    async with AsyncSessionLocal() as session:
        limited = await OffersIngestionService(
            session, providers=[_CrawlProvider([[_item("1"), _item("2")]])]
        ).sync_france_travail_full(since=NOW - timedelta(days=1), until=NOW, limit=1)
    assert limited.fetched == 1


async def test_get_embeddings_batches_and_keeps_order(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "k")
    sizes: list[int] = []

    def fake_batch(*, api_key, model, timeout, texts):
        sizes.append(len(texts))
        return [[float(len(t))] for t in texts]

    monkeypatch.setattr(embeddings, "_embed_batch_sync", fake_batch)
    texts = ["a" * (i + 1) for i in range(250)] + ["   "]
    result = await embeddings.get_embeddings(texts)

    assert sizes == [100, 100, 50]
    assert result[0] == [1.0] and result[249] == [250.0]
    assert result[250] is None  # blank text


async def test_purge_keeps_recent_and_matched_offers() -> None:
    old = utcnow() - timedelta(days=40)
    async with AsyncSessionLocal() as session:
        keep_recent = JobOffer(source="s", external_id="recent", title="t", url="u")
        stale = JobOffer(
            source="s", external_id="stale", title="t", url="u", created_at=old
        )
        stale.published_at = old
        matched = JobOffer(
            source="s", external_id="matched", title="t", url="u", created_at=old
        )
        matched.published_at = old
        session.add_all([keep_recent, stale, matched])
        await session.commit()
        for o in (keep_recent, stale, matched):
            await session.refresh(o)
        import uuid

        session.add(
            CandidateMatch(
                candidate_profile_id=uuid.uuid4(),
                job_offer_id=matched.id,
                company_name="",
                career_score=0,
                ats_score=0,
                ats_potential=0,
                computed_at=utcnow(),
            )
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        assert await purge_old_offers(session, dry_run=True) == 1
    async with AsyncSessionLocal() as session:
        assert await purge_old_offers(session) == 1

    async with AsyncSessionLocal() as session:
        left = {o.external_id for o in (await session.exec(select(JobOffer))).all()}
    assert left == {"recent", "matched"}
