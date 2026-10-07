from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import ARRAY
from sqlmodel import desc, or_, select

from app.core.repository import BaseRepository
from app.modules.offers.models import JobOffer
from app.modules.offers.preferences import OfferPreferences


class JobOfferRepository(BaseRepository[JobOffer]):
    model = JobOffer

    async def get_by_source_external_id(
        self, source: str, external_id: str
    ) -> JobOffer | None:
        return await self.find_one(source=source, external_id=external_id)

    async def list_missing_contract_type(self) -> Sequence[JobOffer]:
        """Offers with no contract_type at all -- backs the one-off
        `backfill-contract-type` CLI command that applies core/contract_type's
        keyword guess to offers ingested before either provider had that
        fallback wired in (see adzuna.py/france_travail.py's `_normalize`).
        `filters` on `list()` only does equality, so this needs its own
        query for "is null or empty string"."""
        stmt = self._base_select().where(
            or_(JobOffer.contract_type.is_(None), JobOffer.contract_type == "")  # type: ignore[attr-defined]
        )
        return (await self.session.exec(stmt)).all()

    async def list_recent(self, *, since: datetime) -> Sequence[JobOffer]:
        """Offers ingested on or after `since`, most recently published
        first -- used by the matching module to bound the pool of offers it
        even considers shortlisting (see MATCHING_MAX_OFFER_POOL_DAYS)."""
        stmt = (
            self._base_select()
            .where(JobOffer.created_at >= since)
            .order_by(desc(JobOffer.published_at))
        )
        return (await self.session.exec(stmt)).all()

    async def list_distinct_company_names(self) -> Sequence[str]:
        """Every distinct non-blank employer name across all known offers --
        backs regret_jobs.py's monthly Regret Index bulk refresh, which
        needs "every company we've ever seen", not just the ones a candidate
        currently matches (this table, not candidate_matches, is the
        complete set: an employer stays known here even once its offers age
        out of MATCHING_MAX_OFFER_POOL_DAYS)."""
        stmt = (
            select(JobOffer.company_name)
            .where(
                JobOffer.company_name.is_not(None),  # type: ignore[attr-defined]
                JobOffer.company_name != "",
            )
            .distinct()
        )
        if self._soft_delete:
            stmt = stmt.where(JobOffer.deleted_at.is_(None))  # type: ignore[attr-defined]
        return (await self.session.exec(stmt)).all()

    async def vector_search_available(self) -> bool:
        """True on Postgres once the pgvector column exists (see
        vector_ddl.py); False on SQLite and on servers without the
        extension, where callers keep the in-memory path."""
        bind = self.session.get_bind()
        if bind.dialect.name != "postgresql":
            return False
        found = await self.session.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'job_offers' AND column_name = 'embedding_vec'"
            )
        )
        return found.first() is not None

    async def nearest(
        self,
        embedding: list[float],
        *,
        created_since: datetime,
        published_since: datetime,
        exclude_ids: Sequence[uuid.UUID] = (),
        per_source: int,
        preferences: OfferPreferences | None = None,
    ) -> list[JobOffer]:
        """The `per_source` offers closest (cosine) to `embedding` for each
        source, within the freshness windows. Per source because matching
        compares offers against their own source's baseline (see
        matching/shortlist.py). Postgres + pgvector only (see
        `vector_search_available`). An exact scan: no index, so no recall
        loss from filtered approximate search -- fine while the pool is the
        last few weeks of offers.

        `preferences` narrows the pool to what the candidate asked for
        *before* taking the nearest ones (see offers/preferences.py); the one
        thing SQL cannot decide there, Hybride vs Sur site, is checked in
        Python on a longer list so enough rows survive."""
        vector_literal = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
        sources = (
            await self.session.execute(
                sa.text("SELECT DISTINCT source FROM job_offers")
            )
        ).scalars()
        found: list[JobOffer] = []
        for source in list(sources):
            stmt = (
                self._base_select()
                .where(JobOffer.source == source)  # type: ignore[arg-type]
                .where(JobOffer.created_at >= created_since)  # type: ignore[arg-type]
                .where(
                    sa.func.coalesce(JobOffer.published_at, JobOffer.created_at)  # type: ignore[arg-type]
                    >= published_since
                )
                .where(sa.text("job_offers.embedding_vec IS NOT NULL"))
            )
            if exclude_ids:
                stmt = stmt.where(
                    sa.text("NOT (job_offers.id = ANY(:exclude_ids))").bindparams(
                        sa.bindparam(
                            "exclude_ids", list(exclude_ids), type_=ARRAY(sa.Uuid())
                        )
                    )
                )
            python_remote = preferences is not None and preferences.needs_python_remote
            if preferences is not None:
                stmt = preferences.apply(stmt)
            stmt = stmt.order_by(
                sa.text(
                    "job_offers.embedding_vec <=> CAST(:query_vec AS vector)"
                ).bindparams(query_vec=vector_literal)
            ).limit(per_source * 4 if python_remote else per_source)
            rows = list((await self.session.exec(stmt)).all())
            if python_remote and preferences is not None:
                rows = [row for row in rows if preferences.matches_remote(row)]
            found.extend(rows[:per_source])
        return found

    async def get_many(self, ids: Sequence[uuid.UUID]) -> list[JobOffer]:
        if not ids:
            return []
        stmt = self._base_select().where(JobOffer.id.in_(list(ids)))  # type: ignore[attr-defined]
        return list((await self.session.exec(stmt)).all())

    async def get_existing(
        self, source: str, external_ids: Sequence[str]
    ) -> dict[str, JobOffer]:
        """Already-stored offers of `source` among `external_ids`, by external
        id -- one query for a whole page instead of one per offer."""
        if not external_ids:
            return {}
        stmt = (
            self._base_select()
            .where(JobOffer.source == source)  # type: ignore[arg-type]
            .where(JobOffer.external_id.in_(list(external_ids)))  # type: ignore[attr-defined]
        )
        return {o.external_id: o for o in (await self.session.exec(stmt)).all()}

    async def purge_older_than(
        self, cutoff: datetime, *, keep: sa.Select, dry_run: bool = False
    ) -> int:
        """Deletes offers last seen/published before `cutoff` unless their id
        is returned by the `keep` subquery (offers a candidate already has a
        match on). Returns how many rows were (or, with `dry_run`, would be)
        deleted."""
        stale = sa.func.coalesce(JobOffer.published_at, JobOffer.created_at) < cutoff
        conditions = (
            stale,
            JobOffer.created_at < cutoff,  # type: ignore[arg-type]
            JobOffer.id.not_in(keep),  # type: ignore[attr-defined]
        )
        if dry_run:
            count = await self.session.execute(
                sa.select(sa.func.count()).select_from(JobOffer).where(*conditions)  # type: ignore[arg-type]
            )
            return int(count.scalar_one())
        result = await self.session.execute(sa.delete(JobOffer).where(*conditions))  # type: ignore[arg-type]
        return int(result.rowcount or 0)  # type: ignore[attr-defined]
