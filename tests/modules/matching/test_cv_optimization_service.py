from __future__ import annotations

import uuid

from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching import cv_optimization_gateway
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_schema import (
    CVOptimizationBullet,
    CVOptimizationExperience,
    CVOptimizationResult,
    CVOptimizationSkill,
)
from app.modules.matching.cv_optimization_service import (
    CVOptimizationService,
    _reconcile_experiences,
    _reconcile_skills,
    clean_skill_label,
    has_technical_skill_labels,
)
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


def test_reconcile_experiences_uses_model_bullets_when_present() -> None:
    original = [
        {
            "title": "Product Owner",
            "company": "Doctolib",
            "period": "2022-2026",
            "description": "Gestion du backlog\nPriorisation",
            "tools": ["Jira"],
        }
    ]
    model_experiences = [
        CVOptimizationExperience(
            title="Product Owner orienté Data",
            company="Doctolib (nom modifié par le modèle -- ignoré)",
            period="Peu importe -- ignoré",
            bullets=[
                CVOptimizationBullet(text="Gestion du backlog", status="unchanged"),
                CVOptimizationBullet(
                    text="Exploitation de la data (SQL)",
                    status="added",
                    why="Déductible de l'expérience réelle.",
                ),
            ],
        )
    ]

    reconciled = _reconcile_experiences(original, model_experiences)

    assert len(reconciled) == 1
    exp = reconciled[0]
    # Title may be reworded by the model...
    assert exp["title"] == "Product Owner orienté Data"
    # ...but company/period are always the candidate's real facts, never the
    # model's echo.
    assert exp["company"] == "Doctolib"
    assert exp["period"] == "2022-2026"
    assert len(exp["bullets"]) == 2
    assert exp["bullets"][1]["status"] == "added"


def test_reconcile_experiences_keeps_same_count_and_order_as_original() -> None:
    """The model returning fewer experiences than the candidate's real
    profile must never drop or misalign an experience -- see
    _reconcile_experiences' docstring."""
    original = [
        {"title": "Poste A", "company": "A", "period": "2020", "description": "x"},
        {"title": "Poste B", "company": "B", "period": "2019", "description": "y"},
        {"title": "Poste C", "company": "C", "period": "2018", "description": "z"},
    ]
    # Model only returned one experience (drift).
    model_experiences = [
        CVOptimizationExperience(
            title="Poste A",
            company="A",
            period="2020",
            bullets=[CVOptimizationBullet(text="x", status="unchanged")],
        )
    ]

    reconciled = _reconcile_experiences(original, model_experiences)

    assert len(reconciled) == 3
    assert [exp["company"] for exp in reconciled] == ["A", "B", "C"]
    # The two experiences the model dropped fall back to an unmodified
    # pass-through of the original description, not an empty/broken entry.
    assert reconciled[1]["bullets"] == [
        {"text": "y", "status": "unchanged", "original_text": None, "why": ""}
    ]
    assert reconciled[2]["title"] == "Poste C"


def test_reconcile_experiences_ignores_extra_model_experiences() -> None:
    original = [{"title": "Seule", "company": "X", "period": "2021", "description": ""}]
    model_experiences = [
        CVOptimizationExperience(title="Seule", company="X", period="2021", bullets=[]),
        CVOptimizationExperience(
            title="Fantôme -- n'existe pas dans le profil réel",
            company="Y",
            period="2022",
            bullets=[],
        ),
    ]

    reconciled = _reconcile_experiences(original, model_experiences)

    assert len(reconciled) == 1
    assert reconciled[0]["title"] == "Seule"


def test_reconcile_skills_always_keeps_every_existing_skill() -> None:
    original_skills = ["Agile", "Scrum", "JIRA"]
    model_skills = [
        CVOptimizationSkill(skill="Agile", added=True),  # model mistake: not new
        CVOptimizationSkill(skill="SQL", added=True),  # genuinely new
    ]

    result = _reconcile_skills(original_skills, model_skills)

    by_name = {item["skill"]: item["added"] for item in result}
    # Existing skills are never marked "added", even if the model got it
    # wrong -- the deterministic rebuild absorbs the mistake silently.
    assert by_name["Agile"] is False
    assert by_name["Scrum"] is False
    assert by_name["JIRA"] is False
    assert by_name["SQL"] is True
    assert len(result) == 4


def test_reconcile_skills_ignores_non_added_and_duplicate_entries() -> None:
    result = _reconcile_skills(
        ["Agile"],
        [
            CVOptimizationSkill(skill="Power BI", added=False),  # not flagged added
            CVOptimizationSkill(skill="SQL", added=True),
            CVOptimizationSkill(skill="sql", added=True),  # duplicate, different case
        ],
    )

    skills = [item["skill"] for item in result]
    assert "Power BI" not in skills
    assert skills.count("SQL") == 1


def test_clean_skill_label_turns_snake_case_into_a_readable_label() -> None:
    assert clean_skill_label("roadmap_planning") == "Roadmap planning"
    assert (
        clean_skill_label("product_analytics_usage_metrics")
        == "Product analytics usage metrics"
    )
    # Normal labels are left exactly as they are.
    assert clean_skill_label("SQL Server") == "SQL Server"
    assert clean_skill_label("API REST") == "API REST"


def test_reconcile_skills_cleans_snake_case_labels_the_model_echoed() -> None:
    result = _reconcile_skills(
        ["SQL"],
        [CVOptimizationSkill(skill="ux_collaboration", added=True)],
    )
    assert [item["skill"] for item in result] == ["SQL", "Ux collaboration"]


def test_has_technical_skill_labels_ignores_a_confirmed_variant() -> None:
    row = CVOptimization(
        candidate_match_id=uuid.uuid4(),
        skills=[{"skill": "roadmap_planning", "added": True}],
        computed_at=utcnow(),
    )
    assert has_technical_skill_labels(row) is True
    row.confirmed_at = utcnow()
    assert has_technical_skill_labels(row) is False
    row.confirmed_at = None
    row.skills = [{"skill": "Roadmap", "added": True}]
    assert has_technical_skill_labels(row) is False


async def _user_id(email: str) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        return user.id


async def test_get_or_generate_caches_result_and_calls_gateway_once(
    monkeypatch, client, auth_headers
) -> None:
    calls = 0

    async def _fake_optimize_cv(cv, offer_text, analysis, **kwargs):
        nonlocal calls
        calls += 1
        return CVOptimizationResult(headline="Optimisé", advice="Conseil")

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", _fake_optimize_cv)

    user_id = await _user_id("user@example.com")
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id,
                status=ProfileStatus.complete.value,
                raw_text="cv",
                experiences=[
                    {
                        "title": "Dev",
                        "company": "Acme",
                        "period": "2020",
                        "description": "Ligne 1",
                    }
                ],
                skills=["Python"],
            )
        )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id="cv-opt-1",
                title="Offre",
                url="https://example.com/offre",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer.id,
                career_score=70,
                ats_score=60,
                ats_potential=80,
                computed_at=utcnow(),
            )
        )
        await session.commit()

        service = CVOptimizationService(session)
        first = await service.get_or_generate(match, profile, offer)
        second = await service.get_or_generate(match, profile, offer)

    assert calls == 1  # second call served from the cache, no new LLM call
    assert first.id == second.id
    assert first.headline == "Optimisé"


async def test_stale_snake_case_row_is_regenerated_in_place(
    monkeypatch, client, auth_headers
) -> None:
    async def _fake_optimize_cv(cv, offer_text, analysis, **kwargs):
        return CVOptimizationResult(
            headline="Nouveau",
            skills=[
                CVOptimizationSkill(skill="Planification de la roadmap", added=True)
            ],
        )

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", _fake_optimize_cv)

    user_id = await _user_id("user@example.com")
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id,
                status=ProfileStatus.complete.value,
                raw_text="cv",
                skills=["Python"],
            )
        )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id="cv-opt-stale",
                title="Offre",
                url="https://example.com/offre",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer.id,
                career_score=70,
                ats_score=60,
                ats_potential=80,
                computed_at=utcnow(),
            )
        )
        stale = CVOptimization(
            candidate_match_id=match.id,
            headline="Ancien",
            skills=[{"skill": "roadmap_planning", "added": True}],
            computed_at=utcnow(),
        )
        session.add(stale)
        await session.commit()
        stale_id = stale.id

        refreshed = await CVOptimizationService(session).get_or_generate(
            match, profile, offer
        )

    assert refreshed.id == stale_id  # same row, refreshed in place
    assert refreshed.headline == "Nouveau"
    assert {item["skill"] for item in refreshed.skills} == {
        "Python",
        "Planification de la roadmap",
    }
