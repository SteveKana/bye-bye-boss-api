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

from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import utcnow
from app.modules.cv import CandidateProfile
from app.modules.matching import cv_optimization_gateway
from app.modules.matching.cv_bullets import (
    clean_bullets,
    description_lines,
    place_mission_first,
)
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_repository import CVOptimizationRepository
from app.modules.matching.cv_optimization_schema import (
    CVOptimizationExperience,
    CVOptimizationSkill,
)
from app.modules.matching.models import CandidateMatch
from app.modules.matching.service import _format_offer_text
from app.modules.matching.skill_labels import clean_skill_label
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
                # One fact per line: a " | " glued line is cut here, so the
                # model never sees (and never copies) the separator.
                "description": "\n".join(description_lines(exp.get("description"))),
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
    lines = description_lines(description)
    return lines or [description.strip()]


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("’", "'").split()).strip(" -•.;:")


def _is_covered(line: str, bullets: list) -> bool:
    """True when the model's bullets already carry this original line, as is
    or lightly reworded (its `text`, or the `original_text` of a rewrite)."""
    target = _norm(line)
    for bullet in bullets:
        for candidate in (bullet.text, bullet.original_text):
            if candidate and (
                SequenceMatcher(None, target, _norm(candidate)).ratio() >= 0.8
            ):
                return True
    return False


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
        model_exp = model_experiences[index] if index < len(model_experiences) else None
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
                    "kind": bullet.kind,
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

        # No task of the real CV may vanish: a bullet of the original list
        # that the model left out is put back at the end, unchanged. Only for
        # a real bullet list (2+ lines); a one-paragraph description is
        # legitimately rewritten into bullets.
        original_lines = _split_bullets(original.get("description", ""))
        if model_exp is not None and model_exp.bullets and len(original_lines) >= 2:
            for line in original_lines:
                if not _is_covered(line, model_exp.bullets):
                    bullets.append(
                        {
                            "text": line,
                            "status": "unchanged",
                            "original_text": None,
                            "why": "",
                        }
                    )

        # Whatever the model returned: no " | " glued line, no fact printed
        # twice, and the sentences describing the mission (as opposed to the
        # tasks) on top, flagged so the PDF prints them as a paragraph.
        bullets = place_mission_first(
            clean_bullets(bullets), original.get("description")
        )

        reconciled.append(
            {
                "title": title,
                "company": original.get("company", ""),
                "period": original.get("period", ""),
                "bullets": bullets,
            }
        )
    return reconciled


def has_technical_skill_labels(optimization: CVOptimization) -> bool:
    """True for a stored optimisation generated before labels were cleaned
    (snake_case skill names): such a row is regenerated -- unless the
    candidate already confirmed it -- the next time it is opened."""
    if optimization.confirmed_at is not None:
        return False
    return any("_" in (item.get("skill") or "") for item in (optimization.skills or []))


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
    duplicated, since it's already in `existing_lower`.

    What the model is trusted for is presentation only: the heading of each
    skill and, when it echoed (most of) the list, the order -- most relevant
    to the offer first -- neither of which can add or drop a skill."""
    existing_lower = {skill.strip().lower() for skill in original_skills if skill}
    result = [{"skill": skill, "added": False} for skill in original_skills]
    seen_added: set[str] = set()
    for item in model_skills:
        if not item.added:
            continue
        name = clean_skill_label(item.skill)
        key = name.lower()
        if not name or key in existing_lower or key in seen_added:
            continue
        result.append({"skill": name, "added": True})
        seen_added.add(key)

    position: dict[str, int] = {}
    heading: dict[str, str] = {}
    for index, item in enumerate(model_skills):
        key = clean_skill_label(item.skill).strip().lower()
        position.setdefault(key, index)
        if item.category.strip():
            heading.setdefault(key, item.category.strip()[:40])
    for entry in result:
        entry["category"] = heading.get(entry["skill"].strip().lower(), "")

    echoed = sum(1 for skill in original_skills if skill.strip().lower() in position)
    if original_skills and echoed >= len(original_skills) / 2:
        late = len(model_skills)
        result = [
            entry
            for _, entry in sorted(
                enumerate(result),
                key=lambda pair: (
                    position.get(pair[1]["skill"].strip().lower(), late),
                    pair[0],
                ),
            )
        ]
    return result


class CVOptimizationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.optimizations = CVOptimizationRepository(session)

    async def get_or_generate(
        self, match: CandidateMatch, profile: CandidateProfile, offer: JobOffer
    ) -> CVOptimization:
        existing = await self.optimizations.get_by_match(match.id)
        if existing is not None and not has_technical_skill_labels(existing):
            return existing

        cv_payload = _build_cv_payload(profile)
        offer_text = _format_offer_text(offer)
        result = await cv_optimization_gateway.optimize_cv(
            cv_payload, offer_text, match.analysis
        )

        values = {
            "headline": result.headline.strip() or profile.headline or "",
            "summary": result.summary.strip() or profile.professional_summary or "",
            "summary_why": result.summary_why,
            "experiences": _reconcile_experiences(
                profile.experiences or [], result.experiences
            ),
            "skills": _reconcile_skills(profile.skills or [], result.skills),
            "advice": result.advice,
            "computed_at": utcnow(),
        }
        if existing is not None:
            # Stale row (see has_technical_skill_labels): refreshed in place,
            # candidate_match_id is unique.
            optimization = await self.optimizations.update(existing, values)
        else:
            optimization = await self.optimizations.create(
                CVOptimization(candidate_match_id=match.id, **values)
            )
        await self.session.commit()
        return optimization

    async def generate_from_text(
        self, profile: CandidateProfile, offer_text: str
    ) -> CVOptimization:
        """Admin tool: adapts the CV to an offer pasted as plain text. Same
        model call and same safeguards (experiences always paired with the
        real ones, skills rebuilt deterministically) as get_or_generate, but
        nothing is stored: the returned row only lives in memory (no match,
        so no candidate_match_id, and no commit)."""
        result = await cv_optimization_gateway.optimize_cv(
            _build_cv_payload(profile), offer_text.strip(), None
        )
        return CVOptimization(  # type: ignore[call-arg]  # in-memory, no match
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

    async def confirm(self, optimization: CVOptimization) -> CVOptimization:
        optimization = await self.optimizations.update(
            optimization, {"confirmed_at": utcnow()}
        )
        await self.session.commit()
        return optimization
