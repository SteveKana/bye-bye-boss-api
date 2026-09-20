"""Ingestion: pulls offers from every configured provider and upserts them.

Runs on a schedule (see jobs.py) and can be triggered on demand for testing
via `python -m app.cli sync-offers`. Idempotent -- re-running just refreshes
already-known offers (matched by source + external_id) instead of
duplicating them, so it's always safe to run again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.offers.models import JobOffer
from app.modules.offers.providers import NormalizedOffer, OfferProvider, build_providers
from app.modules.offers.repository import JobOfferRepository

logger = get_logger("offers.ingestion")


@dataclass
class IngestionReport:
    """Per-run counts, surfaced to the scheduled-job logs and the CLI."""

    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped_unconfigured: list[str] = field(default_factory=list)


class OffersIngestionService:
    def __init__(
        self,
        session: AsyncSession,
        providers: list[OfferProvider] | None = None,
    ) -> None:
        self.session = session
        self.offers = JobOfferRepository(session)
        # Overridable for tests; defaults to every registered provider.
        self._providers = providers if providers is not None else build_providers()

    async def sync(self) -> IngestionReport:
        settings = get_settings()
        report = IngestionReport()

        for provider in self._providers:
            if not provider.is_configured():
                report.skipped_unconfigured.append(provider.source_name)
                continue
            for keyword in settings.offers_search_keywords:
                normalized = await provider.search(
                    keywords=keyword, limit=settings.OFFERS_MAX_PER_KEYWORD
                )
                report.fetched += len(normalized)
                for item in normalized:
                    created = await self._upsert(provider.source_name, item)
                    if created:
                        report.created += 1
                    else:
                        report.updated += 1

        await self.session.commit()
        logger.info(
            "offers_sync_complete",
            fetched=report.fetched,
            created=report.created,
            updated=report.updated,
            skipped=report.skipped_unconfigured,
        )
        return report

    async def _upsert(self, source: str, item: NormalizedOffer) -> bool:
        """Create or refresh one offer. Returns True if it was created."""
        existing = await self.offers.get_by_source_external_id(source, item.external_id)
        values = {
            "title": item.title,
            "company_name": item.company_name,
            "description": item.description,
            "location": item.location,
            "contract_type": item.contract_type,
            "remote_policy": item.remote_policy,
            "salary_min": item.salary_min,
            "salary_max": item.salary_max,
            "salary_label": item.salary_label,
            "url": item.url,
            "published_at": item.published_at,
            "raw": item.raw,
        }
        if existing is None:
            await self.offers.create(
                JobOffer(source=source, external_id=item.external_id, **values)
            )
            return True
        await self.offers.update(existing, values)
        return False
