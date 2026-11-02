"""Market watch: collection (read offers, keep weekly counts) and the
digest of one candidate profile."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.cv import CandidateProfile
from app.modules.market import classifier
from app.modules.market.digest import (
    DigestData,
    WeekCounts,
    build_digest,
    cv_skill_keys,
)
from app.modules.market.models import (
    FAMILY_LEVEL,
    MarketGroupWeekly,
    MarketSkillWeekly,
    OfferMarketTag,
    ProfileMarketTag,
)
from app.modules.offers import JobOffer

logger = get_logger("market.service")

PARIS = ZoneInfo("Europe/Paris")
WINDOW_WEEKS = 4


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def week_start_of(moment: datetime) -> date:
    """Monday (Paris time) of the week `moment` falls in."""
    local = _aware(moment).astimezone(PARIS).date()
    return local - timedelta(days=local.weekday())


def today_paris(now: datetime | None = None) -> date:
    return _aware(now or datetime.now(UTC)).astimezone(PARIS).date()


@dataclass
class CollectionReport:
    classified: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class MarketCollectionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def classify_pending(self, limit: int | None = None) -> CollectionReport:
        """Files the offers that have no tags yet (newest first)."""
        settings = get_settings()
        limit = limit or settings.MARKET_OFFERS_PER_RUN
        rows = (
            await self.session.execute(
                sa.select(JobOffer.id, JobOffer.title, JobOffer.description)  # type: ignore[call-overload]
                .outerjoin(OfferMarketTag, OfferMarketTag.job_offer_id == JobOffer.id)
                .where(OfferMarketTag.id.is_(None))  # type: ignore[attr-defined]
                .order_by(JobOffer.created_at.desc())  # type: ignore[attr-defined]
                .limit(limit)
            )
        ).all()
        report = CollectionReport()
        if not rows:
            return report

        inputs = [
            classifier.OfferInput(
                key=str(r.id), title=r.title or "", description=r.description or ""
            )
            for r in rows
        ]
        tags, usage = await classifier.classify_offers(inputs)
        report.input_tokens = usage.input_tokens
        report.output_tokens = usage.output_tokens
        for offer in inputs:
            tag = tags.get(offer.key)
            if tag is None:
                report.failed += 1
                continue
            self.session.add(
                OfferMarketTag(
                    job_offer_id=uuid.UUID(offer.key),
                    family=tag.family,
                    specialty=tag.specialty,
                    skills=tag.skills,
                    model=settings.MARKET_MODEL,
                )
            )
            report.classified += 1
        await self.session.commit()
        return report

    async def aggregate_weeks(self, now: datetime | None = None) -> int:
        """Recomputes the counts of every week whose offers are all still in
        the database (offers are deleted after OFFERS_RETENTION_DAYS, so an
        older week would come out partial: it keeps what was stored while it
        was complete). Idempotent. Returns the number of group rows written."""
        settings = get_settings()
        today = today_paris(now)
        earliest = today - timedelta(days=max(settings.OFFERS_RETENTION_DAYS - 1, 0))
        first_week = earliest + timedelta(days=(7 - earliest.weekday()) % 7)
        current_week = today - timedelta(days=today.weekday())
        weeks: list[date] = []
        cursor = first_week
        while cursor <= current_week:
            weeks.append(cursor)
            cursor += timedelta(days=7)
        if not weeks:
            return 0

        since = datetime.combine(weeks[0], datetime.min.time(), tzinfo=PARIS)
        rows = (
            await self.session.execute(
                sa.select(  # type: ignore[call-overload]
                    JobOffer.created_at,
                    OfferMarketTag.family,
                    OfferMarketTag.specialty,
                    OfferMarketTag.skills,
                )
                .join(OfferMarketTag, OfferMarketTag.job_offer_id == JobOffer.id)
                .where(JobOffer.created_at >= since)
            )
        ).all()

        totals: Counter[tuple[date, str, str]] = Counter()
        skill_counts: Counter[tuple[date, str, str, str]] = Counter()
        skill_info: dict[tuple[date, str, str, str], Counter[tuple[str, str]]] = {}
        week_set = set(weeks)
        for created_at, family, specialty, skills in rows:
            week = week_start_of(created_at)
            if week not in week_set:
                continue
            levels = [FAMILY_LEVEL] + ([specialty] if specialty else [])
            for level in levels:
                totals[(week, family, level)] += 1
                for skill in skills or []:
                    slot = (week, family, level, skill["key"])
                    skill_counts[slot] += 1
                    skill_info.setdefault(slot, Counter())[
                        (skill["name"], skill["category"])
                    ] += 1

        await self.session.execute(
            sa.delete(MarketGroupWeekly).where(
                MarketGroupWeekly.week_start.in_(weeks)  # type: ignore[attr-defined]
            )
        )
        await self.session.execute(
            sa.delete(MarketSkillWeekly).where(
                MarketSkillWeekly.week_start.in_(weeks)  # type: ignore[attr-defined]
            )
        )
        for (week, family, level), total in totals.items():
            self.session.add(
                MarketGroupWeekly(
                    week_start=week,
                    family=family,
                    specialty=level,
                    offers_total=total,
                    is_complete=week + timedelta(days=7) <= today,
                )
            )
        for (week, family, level, key), count in skill_counts.items():
            (name, category), _ = skill_info[(week, family, level, key)].most_common(1)[
                0
            ]
            self.session.add(
                MarketSkillWeekly(
                    week_start=week,
                    family=family,
                    specialty=level,
                    skill_key=key,
                    skill_label=name,
                    category=category,
                    offers_with_skill=count,
                )
            )
        await self.session.commit()
        return len(totals)


# ------------------------------------------------------------------ digest --
class DigestUnavailableError(Exception):
    """The digest can't be built; `args[0]` is the reason shown to the admin."""


@dataclass
class DigestResult:
    data: DigestData
    family: str
    specialty: str | None
    below_threshold: bool
    offers_analysed: int
    min_offers: int


def profile_text(profile: CandidateProfile) -> str:
    experiences = []
    for exp in (profile.experiences or [])[:4]:
        if isinstance(exp, dict):
            experiences.append(
                f"{exp.get('title', '')} — {(exp.get('description') or '')[:300]}"
            )
    parts = [
        f"Intitulé : {profile.headline or ''}",
        f"Résumé : {profile.professional_summary or ''}",
        "Métiers identifiés : " + ", ".join(map(str, profile.identified_roles or [])),
        "Compétences : " + ", ".join(map(str, (profile.skills or [])[:30])),
        "Expériences : " + " | ".join(experiences),
    ]
    return "\n".join(parts)


def _profile_free_text(profile: CandidateProfile) -> tuple[list[str], str]:
    skills = [s for s in (profile.skills or []) if isinstance(s, str)]
    chunks: list[str] = [profile.headline or "", profile.professional_summary or ""]
    for exp in profile.experiences or []:
        if isinstance(exp, dict):
            chunks += [exp.get("title") or "", exp.get("description") or ""]
            tools = exp.get("tools") or []
            skills += [t for t in tools if isinstance(t, str)]
    for cert in profile.certifications or []:
        if isinstance(cert, dict):
            chunks.append(cert.get("title") or "")
    return skills, " ".join(chunks)


class MarketDigestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def profile_tag(self, profile: CandidateProfile) -> ProfileMarketTag:
        tag = (
            await self.session.execute(
                sa.select(ProfileMarketTag).where(
                    ProfileMarketTag.profile_id == profile.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if tag is not None:
            return tag
        result = await classifier.classify_profile(profile_text(profile))
        if result is None:
            raise DigestUnavailableError(
                "Impossible de déterminer le métier de ce profil pour le moment "
                "(appel IA indisponible). Réessaie dans un instant."
            )
        family, specialty = result
        tag = ProfileMarketTag(
            profile_id=profile.id,
            family=family,
            specialty=specialty,
            model=get_settings().MARKET_MODEL,
        )
        self.session.add(tag)
        await self.session.commit()
        return tag

    async def _weeks(self, family: str, level: str) -> list[WeekCounts]:
        groups = (
            (
                await self.session.execute(
                    sa.select(MarketGroupWeekly)
                    .where(
                        MarketGroupWeekly.family == family,  # type: ignore[arg-type]
                        MarketGroupWeekly.specialty == level,  # type: ignore[arg-type]
                    )
                    .order_by(MarketGroupWeekly.week_start)  # type: ignore[arg-type]
                )
            )
            .scalars()
            .all()
        )
        if not groups:
            return []
        skills = (
            (
                await self.session.execute(
                    sa.select(MarketSkillWeekly).where(
                        MarketSkillWeekly.family == family,  # type: ignore[arg-type]
                        MarketSkillWeekly.specialty == level,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        by_week: dict[date, dict[str, tuple[str, str, int]]] = {}
        for s in skills:
            by_week.setdefault(s.week_start, {})[s.skill_key] = (
                s.skill_label,
                s.category,
                s.offers_with_skill,
            )
        return [
            WeekCounts(
                week_start=g.week_start,
                offers_total=g.offers_total,
                is_complete=g.is_complete,
                skills=by_week.get(g.week_start, {}),
            )
            for g in groups
        ]

    @staticmethod
    def _usable(weeks: list[WeekCounts], *, allow_partial: bool) -> list[WeekCounts]:
        complete = [w for w in weeks if w.is_complete][-WINDOW_WEEKS:]
        if complete or not allow_partial:
            return complete
        return weeks[-1:]

    async def build(
        self, profile: CandidateProfile, *, ignore_threshold: bool = False
    ) -> DigestResult:
        settings = get_settings()
        tag = await self.profile_tag(profile)
        candidates: list[tuple[str, str | None, str]] = []
        if tag.specialty:
            candidates.append((tag.family, tag.specialty, tag.specialty))
        candidates.append((tag.family, None, FAMILY_LEVEL))

        options: list[tuple[str, str | None, list[WeekCounts], int]] = []
        for family, specialty, level in candidates:
            weeks = self._usable(
                await self._weeks(family, level), allow_partial=ignore_threshold
            )
            options.append(
                (family, specialty, weeks, sum(w.offers_total for w in weeks))
            )
        # The most precise level that is big enough; failing that (admin
        # override only), the most precise level that has any data at all.
        chosen = next((o for o in options if o[3] >= settings.MARKET_MIN_OFFERS), None)
        if chosen is None:
            chosen = next((o for o in options if o[3] > 0), options[0])
        best = chosen[:3]
        best_total = chosen[3]
        family, specialty, weeks = best
        below = best_total < settings.MARKET_MIN_OFFERS
        if below and not ignore_threshold:
            label = specialty or family
            raise DigestUnavailableError(
                f"Pas assez d'offres pour « {label} » : {best_total} analysées "
                f"sur les dernières semaines complètes (seuil : "
                f"{settings.MARKET_MIN_OFFERS})."
            )

        exact, squashed = cv_skill_keys(*_profile_free_text(profile))
        data = build_digest(
            weeks=weeks,
            label=specialty or family,
            level="specialty" if specialty else "family",
            first_name=profile.first_name,
            cv_exact=exact,
            cv_squashed=squashed,
        )
        if data is None:
            raise DigestUnavailableError(
                "Aucune donnée collectée pour ce métier pour le moment."
            )
        return DigestResult(
            data=data,
            family=family,
            specialty=specialty,
            below_threshold=below,
            offers_analysed=best_total,
            min_offers=settings.MARKET_MIN_OFFERS,
        )
