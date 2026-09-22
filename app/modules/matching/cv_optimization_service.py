"""Orchestrates "📄 Adapter mon CV pour cette offre".

Unlike MatchingService (see its own docstring), this one runs on demand,
inside the request that a candidate's own click triggers -- not as a
background job. Two reasons that's the right call here and not for matching
itself: this is a rare, explicitly candidate-initiated action (one click per
offer the candidate cares about, not "every candidate x every offer" like
the nightly matching run), and the app already has precedent for a
synchronous in-request LLM call for exactly this kind of low-frequency,
user-triggered action -- see cv/service.py's import_cv, which calls
gateway.structure_cv_text synchronously inside the upload request.

Generation is cached per match (see CVOptimizationRepository.get_by_match):
the first request for a given match pays the ~10-30s LLM latency, every
later request for the same match returns instantly from the DB. There is no
"regenerate" path yet -- a documented limitation, see
cv_optimization_models.CVOptimization's docstring.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import utcnow
from app.modules.cv import CandidateProfile
from app.modules.matching import cv_optimization_gateway
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_repository import CVOptimizationRepository
from app.modules.matching.cv_optimization_schema import (
    CVOptimizationExperience,
    CVOptimizationSkill,
)
from app.modules.matching.models import CandidateMatch
from app.modules.matching.service import _format_offer_text
from app.modules.offers import JobOffer


def _build_cv_payload(profile: CandidateProfile) -> dict:
    """The candidate's CV, as structured input for the optimization prompt
    -- deliberately the STRUCTURED fields (not profile.raw_text, which the
    matching prompt uses), so the model works from a fixed, unambiguous list
    of experiences the service can pair 1:1 with its output. See
    cv_optimization_prompt.py's docstring."""
    return {
        "headline": profile.headline or "",
        "professional_summary": profile.professional_summary or "",
        "total_experience": profile.total_experience or "",
        "skills": profile.skills or [],
        "experiences": [
            {
                "title": exp.get("title", ""),
                "company": exp.get("company", ""),
                "period": exp.get("period", ""),
                "description": exp.get("description", ""),
                "tools": exp.get("tools", []),
            }
            for exp in (profile.experiences or [])
        ],
    }


def _split_bullets(description: str) -> list[str]:
    """Fallback only -- see _reconcile_experiences below. Splits a
    free-text description into lines, stripping any bullet marker the CV
    extraction may have left in (-, •). Not used when the model returned a
    real bulleted breakdown for this experience."""
    if not description:
        return []
    lines = [line.strip(" -•\t") for line in description.splitlines()]
    lines = [line for line in lines if line]
    return lines or [description.strip()]


def _reconcile_experiences(
    original_experiences: list[dict], model_experiences: list[CVOptimizationExperience]
) -> list[dict]:
    """Pairs the model's output experiences with the candidate's real ones
    by position, and always wins on company/period -- these are facts the
    model never gets to rewrite, whatever it returned. The experience count
    and order always match the candidate's real profile: a missing entry
    (the model returned fewer than the profile has) falls back to an
    unmodified pass-through of that experience rather than dropping it or
    shifting the alignment for every experience after it; an extra one (the
    model returned more) is simply ignored, since it can't correspond to a
    real experience slot."""
    reconciled = []
    for index, original in enumerate(original_experiences):
        model_exp = (
            model_experiences[index] if index < len(model_experiences) else None
        )
        title = original.get("title", "")
        if model_exp is not None and model_exp.title.strip():
            title = model_exp.title.strip()

        if model_exp is not None and model_exp.bullets:
            bullets = [
                {
                    "text": bullet.text,
                    "status": bullet.status.value,
                    "original_text": bullet.original_text,
                    "why": bullet.why,
                }
                for bullet in model_exp.bullets
            ]
        else:
            # Honest degrade: no optimization available for this
            # experience (model drift), so it's shown exactly as the
            # candidate already has it, not fabricated.
            bullets = [
                {"text": line, "status": "unchanged", "original_text": None, "why": ""}
                for line in _split_bullets(original.get("description", ""))
            ]

        reconciled.append(
            {
                "title": title,
                "company": original.get("company", ""),
                "period": original.get("period", ""),
                "bullets": bullets,
            }
        )
    return reconciled


def _reconcile_skills(
    original_skills: list[str], model_skills: list[CVOptimizationSkill]
) -> list[dict]:
    """Never trusts the model to faithfully echo back the candidate's whole
    existing skill list -- rebuilds it deterministically instead: every
    skill the candidate's profile already lists, unchanged, plus only the
    genuinely new skills the model flagged `added=True` (see the prompt's
    "raisonnablement déductible" rule) that aren't already a match for an
    existing one. This also silently absorbs the model marking an existing
    skill `added=True` by mistake -- it just never reaches `seen_added`
    duplicated, since it's already in `existing_lower`."""
    existing_lower = {skill.strip().lower() for skill in original_skills if skill}
    result = [{"skill": skill, "added": False} for skill in original_skills]
    seen_added: set[str] = set()
    for item in model_skills:
        if not item.added:
            continue
        name = item.skill.strip()
        key = name.lower()
        if not name or key in existing_lower or key in seen_added:
            continue
        result.append({"skill": name, "added": True})
        seen_added.add(key)
    return result


class CVOptimizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.optimizations = CVOptimizationRepository(session)

    async def get_or_generate(
        self, match: CandidateMatch, profile: CandidateProfile, offer: JobOffer
    ) -> CVOptimization:
        existing = await self.optimizations.get_by_match(match.id)
        if existing is not None:
            return existing

        cv_payload = _build_cv_payload(profile)
        offer_text = _format_offer_text(offer)
        result = await cv_optimization_gateway.optimize_cv(
            cv_payload, offer_text, match.analysis
        )

        optimization = CVOptimization(
            candidate_match_id=match.id,
            headline=result.headline.strip() or profile.headline or "",
            summary=result.summary.strip() or profile.professional_summary or "",
            summary_why=result.summary_why,
            experiences=_reconcile_experiences(
                profile.experiences or [], result.experiences
            ),
            skills=_reconcile_skills(profile.skills or [], result.skills),
            advice=result.advice,
            computed_at=utcnow(),
        )
        optimization = await self.optimizations.create(optimization)
        await self.session.commit()
        return optimization

    async def confirm(self, optimization: CVOptimization) -> CVOptimization:
        optimization = await self.optimizations.update(
            optimization, {"confirmed_at": utcnow()}
        )
        await self.session.commit()
        return optimization
