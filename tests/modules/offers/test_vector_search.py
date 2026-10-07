"""Nearest-offer search on Postgres + pgvector.

Skipped unless TEST_PG_VECTOR_URL points at a Postgres with the `vector`
extension available (e.g. postgresql+asyncpg://user@localhost:5544/testdb);
the regular suite runs on SQLite, where `vector_search_available()` is False.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository
from app.modules.offers.vector_ddl import UPGRADE_STATEMENTS

PG_URL = os.environ.get("TEST_PG_VECTOR_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_PG_VECTOR_URL not set")

NOW = datetime.now(UTC)


def _offer(source: str, vec: list[float] | None, **kw) -> JobOffer:
    values = {
        "source": source,
        "external_id": str(uuid.uuid4()),
        "title": "t",
        "url": "https://example.com",
        "embedding": vec,
        "published_at": NOW,
    }
    values.update(kw)
    return JobOffer(**values)


@pytest.fixture
async def session():
    engine = create_async_engine(PG_URL)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
        for statement in UPGRADE_STATEMENTS:
            await conn.execute(sa.text(statement))
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s
    await engine.dispose()


async def test_nearest_orders_by_cosine_per_source_and_applies_filters(
    session: AsyncSession,
) -> None:
    repo = JobOfferRepository(session)
    assert await repo.vector_search_available()

    exact = _offer("a", [1.0, 0.0, 0.0])
    close = _offer("a", [0.7, 0.7, 0.0])
    far = _offer("a", [0.0, 0.0, 1.0])
    other_source = _offer("b", [0.9, 0.1, 0.0])
    no_embedding = _offer("a", None)
    json_null_like = _offer("a", [])  # empty array: no usable vector
    too_old = _offer("a", [1.0, 0.0, 0.0], published_at=NOW - timedelta(days=30))
    for o in (exact, close, far, other_source, no_embedding, json_null_like, too_old):
        session.add(o)
    await session.commit()

    result = await repo.nearest(
        [1.0, 0.0, 0.0],
        created_since=NOW - timedelta(days=1),
        published_since=NOW - timedelta(days=5),
        per_source=10,
    )
    by_source: dict[str, list[uuid.UUID]] = {}
    for o in result:
        by_source.setdefault(o.source, []).append(o.id)
    assert by_source["a"] == [exact.id, close.id, far.id]
    assert by_source["b"] == [other_source.id]

    limited = await repo.nearest(
        [1.0, 0.0, 0.0],
        created_since=NOW - timedelta(days=1),
        published_since=NOW - timedelta(days=5),
        exclude_ids=[exact.id],
        per_source=1,
    )
    assert sorted(o.id for o in limited) == sorted([close.id, other_source.id])


async def test_trigger_keeps_vector_in_sync_on_update(session: AsyncSession) -> None:
    repo = JobOfferRepository(session)
    offer = _offer("a", [0.0, 1.0, 0.0])
    session.add(offer)
    await session.commit()

    offer.embedding = [1.0, 0.0, 0.0]
    session.add(offer)
    await session.commit()

    result = await repo.nearest(
        [1.0, 0.0, 0.0],
        created_since=NOW - timedelta(days=1),
        published_since=NOW - timedelta(days=5),
        per_source=5,
    )
    assert [o.id for o in result] == [offer.id]
    cosine = (
        await session.execute(
            sa.text(
                "SELECT 1 - (embedding_vec <=> CAST('[1,0,0]' AS vector)) "
                "FROM job_offers"
            )
        )
    ).scalar_one()
    assert math.isclose(cosine, 1.0, abs_tol=1e-6)


async def test_nearest_applies_search_preferences_before_taking_the_nearest(
    session: AsyncSession,
) -> None:
    """The closest offers are picked among the ones that fit the candidate's
    preferences -- not picked first and filtered afterwards."""
    from app.modules.offers import OfferPreferences

    repo = JobOfferRepository(session)
    best_but_elsewhere = _offer("a", [1.0, 0.0, 0.0], region="Bretagne")
    fits = _offer("a", [0.6, 0.8, 0.0], region="Île-de-France", contract_type="CDI")
    wrong_contract = _offer(
        "a", [0.9, 0.1, 0.0], region="Île-de-France", contract_type="Stage"
    )
    hybrid = _offer(
        "a",
        [0.5, 0.5, 0.0],
        region="Île-de-France",
        contract_type="CDI",
        description="2 jours de télétravail par semaine, 3 jours sur site.",
    )
    for o in (best_but_elsewhere, fits, wrong_contract, hybrid):
        session.add(o)
    await session.commit()

    kwargs = {
        "created_since": NOW - timedelta(days=1),
        "published_since": NOW - timedelta(days=7),
        "per_source": 10,
    }
    prefs = OfferPreferences(
        regions=("Île-de-France",),
        include_unknown_region=False,
        contract_types=("CDI",),
    )
    found = await repo.nearest([1.0, 0.0, 0.0], preferences=prefs, **kwargs)
    assert [o.id for o in found] == [hybrid.id, fits.id]

    hybrid_only = OfferPreferences(remote_modes=("Hybride",))
    found = await repo.nearest([1.0, 0.0, 0.0], preferences=hybrid_only, **kwargs)
    assert [o.id for o in found] == [hybrid.id]
