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
    _build_cv_payload,
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
    # The model dropped "Priorisation": it is put back, unchanged, at the end.
    assert len(exp["bullets"]) == 3
    assert exp["bullets"][1]["status"] == "added"
    assert exp["bullets"][2] == {
        "text": "Priorisation",
        "status": "unchanged",
        "original_text": None,
        "why": "",
    }


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


def test_no_task_of_the_original_cv_is_lost() -> None:
    original = [
        {
            "title": "PO",
            "company": "Galec",
            "period": "2024",
            "description": (
                "Pilotage de la migration\nCréation d'ETL via Knime\n"
                "Rédaction des user stories"
            ),
        }
    ]
    model = [
        CVOptimizationExperience(
            bullets=[
                # reworded, original kept in original_text
                CVOptimizationBullet(
                    text="Pilotage de la migration vers une solution IA",
                    status="modified",
                    original_text="Pilotage de la migration",
                    why="Vocabulaire de l'offre.",
                ),
                # as is
                CVOptimizationBullet(
                    text="Rédaction des user stories", status="unchanged"
                ),
                # "Création d'ETL via Knime" is left out by the model
            ]
        )
    ]
    bullets = _reconcile_experiences(original, model)[0]["bullets"]
    assert [b["text"] for b in bullets] == [
        "Pilotage de la migration vers une solution IA",
        "Rédaction des user stories",
        "Création d'ETL via Knime",
    ]
    assert bullets[2]["status"] == "unchanged"


def test_a_one_paragraph_description_is_not_duplicated_at_the_end() -> None:
    original = [
        {
            "title": "PO",
            "company": "Galec",
            "period": "2024",
            "description": "Pilotage de la migration de l'application métier.",
        }
    ]
    model = [
        CVOptimizationExperience(
            bullets=[
                CVOptimizationBullet(
                    text="Migration d'une application", status="added"
                ),
                CVOptimizationBullet(text="Pilotage du planning", status="added"),
            ]
        )
    ]
    bullets = _reconcile_experiences(original, model)[0]["bullets"]
    assert len(bullets) == 2


def test_restore_script_only_replaces_clearly_fuller_descriptions() -> None:
    from scripts.cv_restore_bullets import merge_descriptions

    old = [
        {"company": "Galec", "description": "Résumé court.", "title": "PO"},
        {"company": "Sanofi", "description": "Déjà complet\nsur plusieurs lignes"},
    ]
    new = [
        {
            "company": "Groupement Galec",
            "description": "Résumé court.\n" + "puce\n" * 10,
        },
        {"company": "Sanofi", "description": "Déjà complet\nsur plusieurs lignes"},
    ]
    merged = merge_descriptions(old, new)
    assert merged is not None
    assert merged[0]["description"].startswith("Résumé court.\npuce")
    assert merged[0]["title"] == "PO"  # everything else is kept
    assert merged[1] == old[1]  # not fuller enough: untouched
    # different structure (another number of experiences / company): nothing.
    assert merge_descriptions(old, new[:1]) is None
    assert merge_descriptions(old, [new[0], {**new[1], "company": "Autre"}]) is None
    assert merge_descriptions(old, old) is None


def test_reconcile_experiences_cleans_the_bullets_the_model_returned() -> None:
    glued = (
        "Refonte technique de l'application historique|Pilotage de la migration "
        "de l'application de préconisation vers une solution IA | Migration de "
        "SAP Business Object vers Knime"
    )
    original = [
        {
            "title": "PO",
            "company": "Galec",
            "period": "2024",
            "description": glued,
            "tools": [],
        }
    ]
    model = [
        CVOptimizationExperience(
            bullets=[
                {
                    "text": "Pilotage de la qualité des données",
                    "status": "modified",
                    "kind": "task",
                },
                {
                    "text": "Pilotage de la migration de l'application de "
                    "préconisation vers une solution IA",
                    "status": "modified",
                    "kind": "mission",
                },
                {"text": "Migration de SAP Business Object vers Knime"},
                {"text": "Refonte technique de l'application historique"},
                {"text": glued},
            ]
        )
    ]

    bullets = _reconcile_experiences(original, model)[0]["bullets"]

    # The glued copy is gone; the model's "mission" is first and flagged, the
    # other lines (tasks, or no label) stay plain bullets.
    assert [b["text"] for b in bullets] == [
        "Pilotage de la migration de l'application de préconisation vers une "
        "solution IA",
        "Pilotage de la qualité des données",
        "Migration de SAP Business Object vers Knime",
        "Refonte technique de l'application historique",
    ]


def test_build_cv_payload_gives_the_model_one_fact_per_line() -> None:
    profile = CandidateProfile(  # type: ignore[call-arg]
        user_id=uuid.uuid4(),
        experiences=[{"description": "Recette | Mise en production\n- Recette"}],
    )

    payload = _build_cv_payload(profile)

    assert payload["experiences"][0]["description"] == "Recette\nMise en production"


def test_reconcile_skills_keeps_the_model_headings_and_relevance_order() -> None:
    result = _reconcile_skills(
        ["Jira", "SQL", "Scrum"],
        [
            CVOptimizationSkill(skill="SQL", category="Data & BI"),
            CVOptimizationSkill(skill="Scrum", category="Méthodes Agile"),
            CVOptimizationSkill(skill="Jira", category="Méthodes Agile"),
            CVOptimizationSkill(skill="Power BI", added=True, category="Data & BI"),
        ],
    )

    assert result == [
        {"skill": "SQL", "added": False, "category": "Data & BI"},
        {"skill": "Scrum", "added": False, "category": "Méthodes Agile"},
        {"skill": "Jira", "added": False, "category": "Méthodes Agile"},
        {"skill": "Power BI", "added": True, "category": "Data & BI"},
    ]


def test_reconcile_skills_keeps_the_profile_order_when_the_model_skipped_them() -> None:
    result = _reconcile_skills(
        ["Jira", "SQL", "Scrum"], [CVOptimizationSkill(skill="Scrum")]
    )

    assert [item["skill"] for item in result] == ["Jira", "SQL", "Scrum"]


def test_reconcile_experiences_flags_the_mission_the_model_labelled() -> None:
    original = [
        {
            "title": "PO",
            "company": "Weekr",
            "period": "2023",
            "description": (
                "Création d'une plateforme de services en ligne\n"
                "Rédaction des spécifications\nAnimation des points d'étape"
            ),
        }
    ]
    model = [
        CVOptimizationExperience(
            bullets=[
                CVOptimizationBullet(text="Animation des points d'étape", kind="task"),
                CVOptimizationBullet(
                    text="Création d'une plateforme de services en ligne.",
                    kind="mission",
                ),
                CVOptimizationBullet(text="Rédaction des spécifications", kind="task"),
            ]
        )
    ]

    bullets = _reconcile_experiences(original, model)[0]["bullets"]

    assert bullets[0]["text"] == "Création d'une plateforme de services en ligne."
    assert bullets[0]["context"] is True
    assert all("context" not in b for b in bullets[1:])
    assert all("kind" not in b for b in bullets)


def test_a_model_that_labels_nothing_gives_plain_bullets() -> None:
    original = [
        {"title": "PO", "company": "X", "period": "", "description": "A b c\nD e f"}
    ]
    model = [
        CVOptimizationExperience(
            bullets=[
                CVOptimizationBullet(text="A b c"),
                CVOptimizationBullet(text="D e f"),
            ]
        )
    ]

    bullets = _reconcile_experiences(original, model)[0]["bullets"]

    assert not any(b.get("context") for b in bullets)
