"""The French skill-label glossary: lookup at read time, translation in the
background.

Why a glossary and not labels inside the analysis itself: the analysis
prompt is validated and its snake_case concepts are what make offer-vs-CV
matching reliable, so it stays untouched. Each distinct concept is
translated by a cheap model exactly once and reused for every candidate and
every offer -- the cost drops towards zero as the glossary fills up.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.matching import labels_gateway
from app.modules.matching.models import CandidateMatch, SkillLabel
from app.modules.matching.repository import (
    CandidateMatchRepository,
    SkillLabelRepository,
)
from app.modules.matching.skill_labels import (
    apply_labels,
    collect_keys,
    unique_keys,
)

logger = get_logger("matching.skill_labels")


class SkillLabelService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.glossary = SkillLabelRepository(session)
        self.matches = CandidateMatchRepository(session)

    async def labelled(self, analyses: Sequence[Mapping]) -> list[dict]:
        """Each analysis with a `label` on every skill item: the glossary's
        French wording when known, the underscore-free fallback otherwise
        (still being translated, or the translation failed)."""
        labels = await self.glossary.labels_for(sorted(unique_keys(analyses)))
        return [apply_labels(dict(a), labels) for a in analyses]

    async def _store(self, labels: Mapping[str, str]) -> None:
        for key, label in labels.items():
            try:
                # Savepoint: a concurrent run having stored the same concept
                # first must not abort the whole transaction.
                async with self.session.begin_nested():
                    await self.glossary.create(SkillLabel(key=key, label=label))
            except IntegrityError:
                continue

    async def translate_keys(self, keys: Iterable[str]) -> set[str]:
        """Makes sure every key is in the glossary, translating the missing
        ones. Returns the keys that are in the glossary afterwards."""
        wanted = sorted(set(keys))
        known = await self.glossary.labels_for(wanted)
        missing = [k for k in wanted if k not in known]
        if missing:
            fresh = await labels_gateway.translate_keys(missing)
            if fresh:
                await self._store(fresh)
                known.update(fresh)
        return set(known)

    async def process_pending(self, *, limit: int) -> int:
        """Background catch-up: runs up to `limit` analysed matches through
        the glossary and flags them done. A match whose concepts could not
        all be translated this time (OpenAI down...) stays unflagged and is
        retried by the next run. Returns how many matches got flagged."""
        pending = await self.matches.list_awaiting_labels(limit=limit)
        if not pending:
            return 0
        keys_by_match: list[tuple[CandidateMatch, set[str]]] = [
            (m, collect_keys(m.analysis or {})) for m in pending
        ]
        all_keys: set[str] = set().union(*(keys for _, keys in keys_by_match))
        known = await self.translate_keys(all_keys)

        done = 0
        for match, keys in keys_by_match:
            if keys <= known:
                await self.matches.update(match, {"labels_done": True})
                done += 1
        await self.session.commit()
        if done < len(pending):
            logger.warning(
                "skill_labels_incomplete", pending=len(pending), flagged=done
            )
        return done
