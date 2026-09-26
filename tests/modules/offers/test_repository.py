from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


async def test_list_missing_contract_type_finds_null_and_empty_string() -> None:
    async with AsyncSessionLocal() as session:
        repo = JobOfferRepository(session)
        with_null = await repo.create(
            JobOffer(
                source="adzuna",
                external_id="null-1",
                title="Product Owner freelance",
                url="https://example.com/null-1",
                contract_type=None,
            )
        )
        with_empty = await repo.create(
            JobOffer(
                source="france_travail",
                external_id="empty-1",
                title="Business Analyst",
                url="https://example.com/empty-1",
                contract_type="",
            )
        )
        already_set = await repo.create(
            JobOffer(
                source="adzuna",
                external_id="set-1",
                title="Chef de projet",
                url="https://example.com/set-1",
                contract_type="CDI",
            )
        )
        await session.commit()

        missing = await repo.list_missing_contract_type()

    missing_ids = {offer.id for offer in missing}
    assert with_null.id in missing_ids
    assert with_empty.id in missing_ids
    assert already_set.id not in missing_ids
