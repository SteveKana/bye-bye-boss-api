from __future__ import annotations

import uuid

import pytest

from app.core.models import utcnow
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_pdf import build_cv_pdf, cv_pdf_filename


def _profile(**overrides) -> CandidateProfile:
    defaults = {
        "user_id": uuid.uuid4(),
        "status": ProfileStatus.complete.value,
        "raw_text": "cv",
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _optimization(**overrides) -> CVOptimization:
    defaults = {"candidate_match_id": uuid.uuid4(), "computed_at": utcnow()}
    defaults.update(overrides)
    return CVOptimization(**defaults)


def test_build_cv_pdf_defaults_to_sobre_template() -> None:
    """No template argument -- must not crash and must still produce a
    valid PDF, same as explicitly passing template='sobre'."""
    profile = _profile()
    optimization = _optimization()

    pdf_bytes = build_cv_pdf(profile, optimization)

    assert pdf_bytes.startswith(b"%PDF")


def test_build_cv_pdf_visuelle_template_returns_valid_pdf_bytes() -> None:
    """The second template ('visuelle') must render just as reliably as
    the default -- same content, different header/section-title rendering
    (see cv_pdf.py's _draw_visuelle_first_page)."""
    profile = _profile(first_name="Steve", last_name="Kana", email="steve@example.com")
    optimization = _optimization(
        headline="Product Owner IT",
        experiences=[
            {
                "title": "Product Owner",
                "company": "Doctolib",
                "period": "2022-2026",
                "bullets": [{"text": "Gestion du backlog", "status": "unchanged"}],
            }
        ],
    )

    pdf_bytes = build_cv_pdf(profile, optimization, template="visuelle")

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500


def test_build_cv_pdf_rejects_unknown_template() -> None:
    profile = _profile()
    optimization = _optimization()

    with pytest.raises(ValueError, match="flashy"):
        build_cv_pdf(profile, optimization, template="flashy")


def test_build_cv_pdf_returns_valid_pdf_bytes() -> None:
    profile = _profile(
        first_name="Steve",
        last_name="Kana",
        email="steve@example.com",
        location="Saint-Ouen, France",
    )
    optimization = _optimization(
        headline="Product Owner IT",
        summary="Résumé optimisé.",
        experiences=[
            {
                "title": "Product Owner",
                "company": "Doctolib",
                "period": "2022-2026",
                "bullets": [
                    {"text": "Gestion du backlog", "status": "unchanged"},
                    {"text": "Exploitation de la data (SQL)", "status": "added"},
                ],
            }
        ],
        skills=[{"skill": "Agile", "added": False}, {"skill": "SQL", "added": True}],
    )

    pdf_bytes = build_cv_pdf(profile, optimization)

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500


def test_build_cv_pdf_handles_missing_optional_fields() -> None:
    """No first/last name, no experiences/skills, no formations/languages/
    certifications -- a bare-minimum profile must still render, not crash."""
    profile = _profile()
    optimization = _optimization()

    pdf_bytes = build_cv_pdf(profile, optimization)

    assert pdf_bytes.startswith(b"%PDF")


def test_build_cv_pdf_includes_profile_only_sections() -> None:
    """Formations/languages/certifications aren't touched by the LLM
    optimization -- they must still come from the profile."""
    profile = _profile(
        formations=[{"title": "Master Informatique", "school_period": "2015-2018"}],
        languages=[{"name": "Anglais", "level": "courant"}],
        certifications=[{"title": "PSPO I", "issuer_period": "2022"}],
    )
    optimization = _optimization()

    pdf_bytes = build_cv_pdf(profile, optimization)

    assert pdf_bytes.startswith(b"%PDF")


def test_build_cv_pdf_escapes_special_characters() -> None:
    """A bullet or summary containing '<', '>' or '&' (plausible in a real
    CV -- "R&D", "C++ <embedded>") must not break Paragraph's mini-markup
    parser or get swallowed."""
    profile = _profile(first_name="R&D", last_name="Test")
    optimization = _optimization(
        headline="Ingénieur R&D <embedded systems>",
        summary="Expérience en R&D avec des systèmes <temps réel>.",
        experiences=[
            {
                "title": "Ingénieur",
                "company": "Acme & Co",
                "period": "2020",
                "bullets": [
                    {"text": "Développement <firmware> & tests", "status": "unchanged"}
                ],
            }
        ],
    )

    pdf_bytes = build_cv_pdf(profile, optimization)

    assert pdf_bytes.startswith(b"%PDF")


def test_cv_pdf_filename_slugifies_accents_and_spaces() -> None:
    profile = _profile(first_name="Émilie", last_name="Bélanger-Côté")

    filename = cv_pdf_filename(profile)

    assert filename == "CV_Emilie_Belanger_Cote.pdf"


def test_cv_pdf_filename_falls_back_without_name() -> None:
    profile = _profile()

    assert cv_pdf_filename(profile) == "CV_Candidat.pdf"
