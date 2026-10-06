from __future__ import annotations

import pytest

from app.core.seniority import (
    candidate_years,
    career_score_penalty,
    is_entry_level_contract,
    is_excluded_for_candidate,
    is_junior_offer,
    is_student_profile,
    required_years_range,
)

STAGE_CV = [{"title": "Stagiaire marketing", "company": "Acme"}]
CDI_CV = [{"title": "Product Owner", "company": "Acme"}]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7 ans", 7.0),
        ("3 ans et 6 mois", 3.5),
        ("8 mois", 8 / 12),
        ("0 an", 0.0),
        ("10+ ans", 10.0),
        ("plusieurs années", None),
        ("", None),
        (None, None),
    ],
)
def test_candidate_years(text, expected) -> None:
    result = candidate_years(text)
    assert result == pytest.approx(expected) if expected is not None else result is None


def test_student_needs_two_years_or_less_and_stages() -> None:
    assert is_student_profile("1 an", STAGE_CV)
    assert is_student_profile("2 ans", STAGE_CV)
    assert not is_student_profile("3 ans", STAGE_CV)
    assert not is_student_profile("1 an", CDI_CV)
    assert not is_student_profile("10 ans", STAGE_CV)


def test_cv_with_no_experience_at_all_is_a_beginner() -> None:
    assert is_student_profile("0 an", [])
    assert is_student_profile(None, [])


def test_alternance_on_the_cv_counts_as_student_experience() -> None:
    cv = [{"title": "Alternant développeur", "company": "X"}]
    assert is_student_profile("2 ans", cv)


def test_contract_and_junior_detection() -> None:
    assert is_entry_level_contract("Stage", "Chef de projet")
    assert is_entry_level_contract(None, "Alternance - Assistant RH")
    assert is_entry_level_contract("Contrat apprentissage", "Comptable")
    assert is_entry_level_contract("Contrat professionnalisation", "Comptable")
    assert not is_entry_level_contract("CDI", "Product Owner")
    assert is_junior_offer("Développeur Python Junior")
    assert is_junior_offer("Comptable débutant")
    assert not is_junior_offer("Product Owner confirmé")


def test_experienced_candidate_never_gets_stage_or_alternance() -> None:
    for contract in ("Stage", "Alternance"):
        assert is_excluded_for_candidate(
            total_experience="7 ans",
            experiences=CDI_CV,
            contract_type=contract,
            title="Chef de projet",
        )


def test_junior_dropped_from_three_years_only() -> None:
    kwargs = {"experiences": CDI_CV, "contract_type": "CDI", "title": "Dev junior"}
    assert is_excluded_for_candidate(total_experience="3 ans", **kwargs)
    assert not is_excluded_for_candidate(total_experience="2 ans", **kwargs)


def test_student_receives_everything() -> None:
    for title, contract in (("Stage RH", "Stage"), ("Dev junior", "CDI")):
        assert not is_excluded_for_candidate(
            total_experience="1 an",
            experiences=STAGE_CV,
            contract_type=contract,
            title=title,
        )


def test_unreadable_experience_excludes_nothing() -> None:
    assert not is_excluded_for_candidate(
        total_experience="confirmé",
        experiences=CDI_CV,
        contract_type="Stage",
        title="Stage",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vous justifiez de 5 ans d'expérience en gestion de projet", (5, 5)),
        ("3 à 5 ans d'expérience minimum", (3, 5)),
        ("Expérience de 8 ans minimum", (8, 8)),
        ("5+ ans d'expérience en Java et 2 ans d'expérience AWS", (5, 5)),
        ("Minimum 4 ans sur un poste similaire", (4, 4)),
        ("Notre entreprise a plus de 20 ans d'existence", None),
        ("Poste ouvert à tous", None),
    ],
)
def test_required_years_range(text, expected) -> None:
    assert required_years_range(text) == expected


@pytest.mark.parametrize(
    ("years", "required", "expected"),
    [
        (7, (6, 6), 0),  # inside [6, 10]
        (7, (10, 10), 0),  # upper edge
        (7, (11, 11), 8),  # 1 year beyond
        (7, (13, 13), 24),  # 3 years beyond
        (7, (30, 30), 40),  # capped
        (7, (5, 5), 5),  # 1 year below the window
        (10, (2, 2), 35 - 5),  # gap 7 -> 35, capped at 30
        (7, (3, 5), 5),  # range below the window: distance from its top
        (7, (3, 6), 0),  # range touching the window
        (7, None, 0),
        (None, (5, 5), 0),
    ],
)
def test_career_score_penalty(years, required, expected) -> None:
    assert career_score_penalty(years, required) == expected
