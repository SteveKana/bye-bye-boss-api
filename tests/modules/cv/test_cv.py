from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.cv import extraction, gateway
from app.modules.cv.models import CandidateProfile
from app.modules.matching import jobs as matching_jobs

UPLOAD = "/api/v1/cv/upload"
PROFILE = "/api/v1/cv/profile"
PREFERENCES = "/api/v1/cv/profile/preferences"

_EXTRACTED = {
    "first_name": "Thomas",
    "last_name": "Martin",
    "email": "thomas.martin@email.com",
    "location": "Paris, France",
    "total_experience": "7 ans",
    "experiences": [
        {
            "title": "Product Owner",
            "company": "DataSolutions",
            "period": "2022 – Présent",
            "description": "Pilotage du backlog produit.",
            "tools": ["Jira", "Figma"],
        }
    ],
    "skills": ["Product Management", "Agile"],
    "formations": [
        {"title": "Master MSI", "school_period": "Paris Dauphine • 2014 – 2016"}
    ],
    "languages": [{"name": "Français", "level": "Langue maternelle"}],
    "certifications": [{"title": "PSPO I", "issuer_period": "Scrum.org • 2021"}],
    "professional_summary": "Product Owner orienté data avec 7 ans d'expérience.",
    "identified_roles": ["Product Owner", "Product Manager"],
    "domains": ["Data", "Retail"],
    "skill_categories": [
        {"category": "Product & Delivery", "skills": ["Product Management"]},
        {"category": "Méthodes", "skills": ["Agile"]},
    ],
}


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch, tmp_path):
    # Patch the low-level per-format readers only, so the real `extract_text`
    # still runs its file-type detection and empty-content checks.
    monkeypatch.setattr(extraction, "_extract_pdf", lambda _data: "texte brut du cv")
    monkeypatch.setattr(extraction, "_extract_docx", lambda _data: "texte brut du cv")
    # save_original_file writes real bytes to disk regardless of the mocks
    # above -- redirect it to a per-test temp dir instead of the repo.
    monkeypatch.setattr(get_settings(), "CV_UPLOAD_DIR", str(tmp_path))

    async def _fake_structure(_raw_text: str) -> dict:
        return _EXTRACTED

    monkeypatch.setattr(gateway, "structure_cv_text", _fake_structure)


def _dummy_pdf() -> tuple[str, bytes, str]:
    return ("cv.pdf", b"%PDF-1.4 fake content", "application/pdf")


async def test_upload_requires_auth(client: AsyncClient) -> None:
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()})
    assert r.status_code == 401


async def test_upload_parses_and_creates_draft_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "draft"
    assert body["first_name"] == "Thomas"
    assert body["experiences"][0]["company"] == "DataSolutions"
    assert body["skills"] == ["Product Management", "Agile"]
    assert body["headline"] == "Product Owner"
    assert body["professional_summary"] == _EXTRACTED["professional_summary"]
    assert body["identified_roles"] == ["Product Owner", "Product Manager"]
    assert body["domains"] == ["Data", "Retail"]
    assert body["skill_categories"][0]["category"] == "Product & Delivery"
    assert body["skill_categories"][0]["skills"] == ["Product Management"]


async def test_upload_sets_cv_analyzed_at(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["cv_analyzed_at"] is not None


async def test_synthesized_fields_are_not_user_editable(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # professional_summary/identified_roles/domains/skill_categories are
    # LLM-synthesized and refreshed on every re-import -- unlike headline,
    # there's no verification-step override for them.
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    r = await client.put(
        PROFILE,
        json={"first_name": "Thomasse"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["professional_summary"] == _EXTRACTED["professional_summary"]
    assert body["domains"] == ["Data", "Retail"]


async def test_headline_edit_lasts_only_until_the_next_reimport(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # headline behaves like the other flat CV fields (name, email,
    # location): editing it is a display tweak, not a permanent override --
    # the next CV import refreshes it to match the latest experience.
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(
        PROFILE, json={"headline": "Senior Product Owner"}, headers=auth_headers
    )
    r = await client.get(PROFILE, headers=auth_headers)
    assert r.json()["headline"] == "Senior Product Owner"

    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["headline"] == "Product Owner"


async def test_headline_refreshes_to_match_an_updated_cv(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    updated = {
        **_EXTRACTED,
        "experiences": [
            {**_EXTRACTED["experiences"][0], "title": "Lead Product Manager"}
        ],
    }

    async def _fake_structure_updated(_raw_text: str) -> dict:
        return updated

    monkeypatch.setattr(gateway, "structure_cv_text", _fake_structure_updated)
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["headline"] == "Lead Product Manager"


async def test_unsupported_file_type_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        UPLOAD,
        files={"file": ("cv.txt", b"plain text", "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_file_type"


async def test_profile_requires_prior_upload(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get(PROFILE, headers=auth_headers)
    assert r.status_code == 404


async def test_verification_update_overrides_extracted_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    r = await client.put(
        PROFILE,
        json={"first_name": "Thomasse", "skills": ["Product Management", "SQL"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["first_name"] == "Thomasse"
    assert body["skills"] == ["Product Management", "SQL"]
    # Untouched fields survive the partial update.
    assert body["last_name"] == "Martin"


async def test_verification_update_sets_verification_completed_at(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """This is the signal the frontend's onboarding guard reads to tell
    "just imported, not yet verified" apart from "verified, preferences not
    saved" -- both are status == "draft" (see models.py's docstring on the
    field)."""
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    before = await client.get(PROFILE, headers=auth_headers)
    assert before.json()["verification_completed_at"] is None

    r = await client.put(PROFILE, json={"first_name": "Thomasse"}, headers=auth_headers)
    assert r.json()["verification_completed_at"] is not None


async def test_verification_completed_at_survives_a_cv_reimport(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Deliberately NOT cleared on re-import (unlike most other extracted
    fields) -- see models.py's docstring: the profile page's "reupload CV"
    shortcut must keep working without bouncing an already-verified
    candidate back into the onboarding wizard."""
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(PROFILE, json={"first_name": "Thomasse"}, headers=auth_headers)

    # Re-import (the profile page's "reupload" shortcut) -- verification is
    # left alone. Onboarding itself ends at the "Zone & contrat" step, so a
    # brand-new account is still a draft here.
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.json()["status"] == "draft"
    assert r.json()["verification_completed_at"] is not None


async def test_onboarding_ends_at_the_zone_and_contract_step_and_matches_once(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """Steve, 2026-10-07: the last onboarding step is "Zone & contrat" (the
    preferences endpoint). Verifying the CV alone neither completes the
    profile nor starts the first matching run -- that run must see the chosen
    zone -- and it fires exactly once per account."""
    triggered: list = []

    async def _fake_run(profile_id):
        triggered.append(profile_id)

    monkeypatch.setattr(matching_jobs, "run_matching_for_new_profile", _fake_run)

    upload = await client.post(
        UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers
    )
    assert upload.json()["status"] == "draft"

    r = await client.put(PROFILE, json={"first_name": "Thomasse"}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "draft"
    assert triggered == []

    r = await client.put(
        PREFERENCES,
        json={"mobility_regions": ["Île-de-France"], "contract_types": ["CDI"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "complete"
    assert [str(p) for p in triggered] == [upload.json()["id"]]

    # Saving again (profile page edits, CV re-import) never re-triggers it,
    # and an onboarded account is complete again as soon as it re-verifies.
    await client.put(PROFILE, json={"first_name": "Thomas"}, headers=auth_headers)
    reimport = await client.post(
        UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers
    )
    assert reimport.json()["status"] == "complete"
    await client.put(PROFILE, json={"first_name": "Tom"}, headers=auth_headers)
    assert len(triggered) == 1


async def test_upload_strips_nul_characters_from_the_cv(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    """Some PDF exports leave NUL characters in the extracted text, which
    PostgreSQL refuses -- the upload used to fail with a 500 (jjkenfack,
    2026-10-02). SQLite accepts them, so check what actually gets stored."""
    monkeypatch.setattr(
        extraction, "_extract_pdf", lambda _data: "Thomas\nDe sept. 2023\x002024\x00"
    )

    async def _structure_with_nul(_raw_text: str) -> dict:
        return {**_EXTRACTED, "first_name": "Tho\x00mas", "skills": ["Ag\x00ile"]}

    monkeypatch.setattr(gateway, "structure_cv_text", _structure_with_nul)

    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["first_name"] == "Thomas"
    assert r.json()["skills"] == ["Agile"]

    async with AsyncSessionLocal() as session:
        stored = (await session.exec(select(CandidateProfile))).one()
    assert "\x00" not in stored.raw_text
    assert stored.raw_text.endswith("2023" + "2024")


async def test_cv_reimport_after_onboarding_completion_keeps_status_complete(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Steve, 2026-09-29: bouncing an already-onboarded candidate back to
    the preferences step on every CV update made no sense -- `status` only
    ever needs to protect the *first* trip through onboarding.
    `onboarding_matched_at` (set once, permanently, by apply_preferences)
    already guards on its own against repeat LLM-costed matching runs, so
    once it's set a re-import can safely refresh the CV-derived fields
    without touching `status`. Contrast with
    test_verification_completed_at_survives_a_cv_reimport above, where
    onboarding was never completed and status must still reset to draft."""
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(PROFILE, json={"first_name": "Thomasse"}, headers=auth_headers)
    await client.put(
        PREFERENCES,
        json={
            "contract_types": ["CDI"],
            "remote_preferences": ["Sur site"],
            "mobility": "France entière",
        },
        headers=auth_headers,
    )

    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    # The CV-derived fields are still refreshed from the new import --
    # only `status` is left alone.
    assert body["first_name"] == "Thomas"


async def test_preferences_update_completes_onboarding(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    r = await client.put(
        PREFERENCES,
        json={
            "contract_types": ["CDI", "Freelance"],
            "remote_preferences": ["Hybride", "Full remote"],
            "mobility": "France entière",
            "salary_target": 55000,
            "daily_rate": 500,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert body["contract_types"] == ["CDI", "Freelance"]
    assert body["remote_preferences"] == ["Hybride", "Full remote"]
    assert body["salary_target"] == 55000
    assert body["daily_rate"] == 500


async def test_completing_onboarding_for_the_first_time_triggers_immediate_matching(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    triggered: list = []

    async def _fake_run(profile_id):
        triggered.append(profile_id)

    monkeypatch.setattr(matching_jobs, "run_matching_for_new_profile", _fake_run)

    upload = await client.post(
        UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers
    )
    profile_id = upload.json()["id"]

    r = await client.put(
        PREFERENCES,
        json={
            "contract_types": ["CDI"],
            "remote_preferences": ["Sur site"],
            "mobility": "France entière",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert [str(p) for p in triggered] == [profile_id]


async def test_saving_preferences_again_does_not_retrigger_matching(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    # The abuse case Steve flagged: a candidate must not be able to force
    # extra (LLM-costed) matching runs by resaving preferences, or by
    # re-importing their CV, over and over.
    triggered: list = []

    async def _fake_run(profile_id):
        triggered.append(profile_id)

    monkeypatch.setattr(matching_jobs, "run_matching_for_new_profile", _fake_run)

    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    payload = {
        "contract_types": ["CDI"],
        "remote_preferences": ["Sur site"],
        "mobility": "France entière",
    }
    await client.put(PREFERENCES, json=payload, headers=auth_headers)
    assert len(triggered) == 1

    # Re-saving preferences a second time...
    r = await client.put(PREFERENCES, json=payload, headers=auth_headers)
    assert r.status_code == 200
    assert len(triggered) == 1

    # ...and re-importing the CV in between, then saving preferences once
    # more, still doesn't fire a second immediate run.
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(PREFERENCES, json=payload, headers=auth_headers)
    assert len(triggered) == 1


async def test_saving_preferences_does_not_touch_cv_analyzed_at(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # cv_analyzed_at means "the CV was last (re-)parsed" -- saving
    # preferences updates the same row (bumping the generic `updated_at`)
    # but must never look like a fresh CV analysis on the profile page.
    upload = await client.post(
        UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers
    )
    analyzed_at = upload.json()["cv_analyzed_at"]
    assert analyzed_at is not None

    r = await client.put(
        PREFERENCES,
        json={
            "contract_types": ["CDI"],
            "remote_preferences": ["Sur site"],
            "mobility": "France entière",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["cv_analyzed_at"] == analyzed_at


async def test_preferences_update_saves_regions_and_marks_them_saved(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    r = await client.put(
        PREFERENCES,
        json={
            "mobility_regions": ["Île-de-France", "Hauts-de-France", "Île-de-France"],
            "include_unknown_region": False,
            "contract_types": ["CDI", "Stage", "Alternance"],
            "remote_preferences": ["Full remote"],
            "salary_target": 45000,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mobility_regions"] == ["Île-de-France", "Hauts-de-France"]
    assert body["include_unknown_region"] is False
    assert body["contract_types"] == ["CDI", "Stage", "Alternance"]
    assert body["salary_target"] == 45000
    assert body["preferences_saved_at"] is not None
    # Legacy single-zone fields stay coherent for older readers.
    assert body["mobility"] == "Région uniquement"


async def test_preferences_default_to_no_restriction(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Every criterion is optional: an empty form means France entière, all
    contracts and all work modes."""
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    r = await client.put(PREFERENCES, json={}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["mobility_regions"] == []
    assert body["contract_types"] == []
    assert body["include_unknown_region"] is True
    assert body["mobility"] == "France entière"
    assert body["status"] == "complete"


async def test_preferences_rejects_unknown_region_and_contract(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)

    r = await client.put(
        PREFERENCES, json={"mobility_regions": ["Atlantide"]}, headers=auth_headers
    )
    assert r.status_code == 422
    r = await client.put(
        PREFERENCES, json={"contract_types": ["Mécénat"]}, headers=auth_headers
    )
    assert r.status_code == 422
