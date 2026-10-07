"""Ingestion: pulls offers from every configured provider and upserts them.

Runs on a schedule (see jobs.py) and can be triggered on demand for testing
via `python -m app.cli sync-offers`. Idempotent -- re-running just refreshes
already-known offers (matched by source + external_id) instead of
duplicating them, so it's always safe to run again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import embeddings
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
    failed_slices: list[str] = field(default_factory=list)


def _embed_text(item: NormalizedOffer) -> str:
    return f"{item.title} {item.description or ''}"


def _needs_embedding(existing: JobOffer | None, item: NormalizedOffer) -> bool:
    """An offer's embedding is (re)computed only when it actually might be
    stale: brand new, never successfully embedded before (e.g. ingested
    before this feature shipped, or a past embedding API call failed), or
    its title/description text has changed. An unchanged offer keeps its
    cached embedding across every hourly refresh instead of re-embedding
    identical text for no reason."""
    if existing is None or existing.embedding is None:
        return True
    return existing.title != item.title or existing.description != item.description


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
                await self._upsert_batch(provider.source_name, normalized, report)

        await self.session.commit()
        logger.info(
            "offers_sync_complete",
            fetched=report.fetched,
            created=report.created,
            updated=report.updated,
            skipped=report.skipped_unconfigured,
        )
        return report

    async def _upsert_batch(
        self, source: str, items: list[NormalizedOffer], report: IngestionReport
    ) -> None:
        # DB reads stay sequential on the single session (AsyncSession isn't
        # safe for concurrent use) -- figure out up front which offer each
        # item already is (if any), and whether its embedding needs
        # (re)computing.
        pairs: list[tuple[NormalizedOffer, JobOffer | None]] = []
        for item in items:
            existing = await self.offers.get_by_source_external_id(
                source, item.external_id
            )
            pairs.append((item, existing))

        # The embedding call is what's slow (network-bound OpenAI call) and
        # touches no shared state, so compute several at once -- same
        # concurrency pattern as matching/service.py's LLM scoring. Skipped
        # entirely for an offer that doesn't need (re)embedding (see
        # _needs_embedding) -- the common case on every run after the first.
        settings = get_settings()
        semaphore = asyncio.Semaphore(settings.OFFERS_EMBEDDING_CONCURRENCY)

        async def _embed_if_needed(
            item: NormalizedOffer, existing: JobOffer | None
        ) -> list[float] | None:
            if not _needs_embedding(existing, item):
                return existing.embedding  # type: ignore[union-attr]
            async with semaphore:
                return await embeddings.get_embedding(_embed_text(item))

        computed = await asyncio.gather(
            *(_embed_if_needed(item, existing) for item, existing in pairs)
        )

        # DB writes, back on the single session.
        for (item, existing), embedding in zip(pairs, computed, strict=True):
            created = await self._upsert(source, item, existing, embedding)
            if created:
                report.created += 1
            else:
                report.updated += 1

    async def _upsert(
        self,
        source: str,
        item: NormalizedOffer,
        existing: JobOffer | None,
        embedding: list[float] | None,
    ) -> bool:
        """Create or refresh one offer. Returns True if it was created."""
        values = {
            "title": item.title,
            "company_name": item.company_name,
            "description": item.description,
            "location": item.location,
            "contract_type": item.contract_type,
            "remote_policy": item.remote_policy,
            "region": item.region,
            "is_full_remote": item.is_full_remote,
            "salary_min": item.salary_min,
            "salary_max": item.salary_max,
            "salary_label": item.salary_label,
            "daily_rate_min": item.daily_rate_min,
            "daily_rate_max": item.daily_rate_max,
            "url": item.url,
            "published_at": item.published_at,
            "raw": item.raw,
            "embedding": embedding,
        }
        if existing is None:
            await self.offers.create(
                JobOffer(source=source, external_id=item.external_id, **values)
            )
            return True
        await self.offers.update(existing, values)
        return False

    # ---- full France Travail crawl (tous métiers) -----------------------------

    async def sync_france_travail_full(
        self,
        *,
        since: datetime,
        until: datetime,
        departement: str | None = None,
        limit: int | None = None,
    ) -> IngestionReport:
        """Imports every France Travail offer created in [since, until]
        (see FranceTravailProvider.crawl), page by page: one lookup, one
        batched embedding call and one commit per page. Idempotent: an offer
        already stored is refreshed, and re-embedded only if its text
        changed, so an interrupted run is simply started again."""
        report = IngestionReport()
        provider = next(
            (p for p in self._providers if p.source_name == "france_travail"), None
        )
        if provider is None or not provider.is_configured():
            report.skipped_unconfigured.append("france_travail")
            return report

        async for page in provider.crawl(  # type: ignore[attr-defined]
            since=since, until=until, departement=departement
        ):
            if limit is not None:
                page = page[: max(limit - report.fetched, 0)]
                if not page:
                    break
            report.fetched += len(page)
            await self._upsert_page(provider.source_name, page, report)
            await self.session.commit()
            logger.info(
                "offers_full_sync_progress",
                fetched=report.fetched,
                created=report.created,
                updated=report.updated,
            )
            if limit is not None and report.fetched >= limit:
                break
        report.failed_slices = list(getattr(provider, "failed_slices", []))
        return report

    async def _upsert_page(
        self, source: str, items: list[NormalizedOffer], report: IngestionReport
    ) -> None:
        unique = list({item.external_id: item for item in items}.values())
        existing = await self.offers.get_existing(
            source, [item.external_id for item in unique]
        )
        to_embed = [
            item
            for item in unique
            if _needs_embedding(existing.get(item.external_id), item)
        ]
        vectors = await embeddings.get_embeddings([_embed_text(i) for i in to_embed])
        fresh = {
            item.external_id: vector
            for item, vector in zip(to_embed, vectors, strict=True)
        }
        for item in unique:
            current = existing.get(item.external_id)
            if item.external_id in fresh:
                embedding = fresh[item.external_id]
            else:
                embedding = current.embedding if current is not None else None
            if await self._upsert(source, item, current, embedding):
                report.created += 1
            else:
                report.updated += 1
