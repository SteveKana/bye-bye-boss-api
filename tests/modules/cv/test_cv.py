from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.modules.cv import extraction, gateway

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
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert body["contract_types"] == ["CDI", "Freelance"]
    assert body["remote_preferences"] == ["Hybride", "Full remote"]
    assert body["salary_target"] == 55000


async def test_preferences_requires_at_least_one_contract_type(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    r = await client.put(
        PREFERENCES,
        json={
            "contract_types": [],
            "remote_preferences": ["Hybride", "Full remote"],
            "mobility": "France entière",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


async def test_new_profile_defaults_to_available_immediately(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    body = r.json()
    assert body["availability_status"] == "immediate"
    assert body["availability_date"] is None
    assert body["notice_period_months"] is None


async def test_setting_a_future_date_clears_notice_period(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    r = await client.put(
        PROFILE,
        json={"availability_status": "date", "availability_date": "2026-11-01"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["availability_status"] == "date"
    assert body["availability_date"] == "2026-11-01"
    assert body["notice_period_months"] is None


async def test_setting_notice_period_clears_the_date(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(
        PROFILE,
        json={"availability_status": "date", "availability_date": "2026-11-01"},
        headers=auth_headers,
    )
    r = await client.put(
        PROFILE,
        json={"availability_status": "notice", "notice_period_months": 2},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["availability_status"] == "notice"
    assert body["notice_period_months"] == 2
    assert body["availability_date"] is None


async def test_switching_back_to_immediate_clears_both(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(
        PROFILE,
        json={"availability_status": "notice", "notice_period_months": 3},
        headers=auth_headers,
    )
    r = await client.put(
        PROFILE,
        json={"availability_status": "immediate"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["availability_status"] == "immediate"
    assert body["availability_date"] is None
    assert body["notice_period_months"] is None


async def test_reimporting_cv_preserves_manually_set_availability(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.put(
        PROFILE,
        json={"availability_status": "date", "availability_date": "2026-11-01"},
        headers=auth_headers,
    )
    r = await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["availability_status"] == "date"
    assert body["availability_date"] == "2026-11-01"


DOWNLOAD = "/api/v1/cv/download"


async def test_download_returns_the_uploaded_file(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    r = await client.get(DOWNLOAD, headers=auth_headers)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake content"
    assert r.headers["content-type"] == "application/pdf"
    assert "cv.pdf" in r.headers["content-disposition"]


async def test_download_requires_auth(client: AsyncClient) -> None:
    r = await client.get(DOWNLOAD)
    assert r.status_code == 401


async def test_download_without_a_cv_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get(DOWNLOAD, headers=auth_headers)
    assert r.status_code == 404


async def test_reimporting_replaces_the_downloadable_file(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await client.post(UPLOAD, files={"file": _dummy_pdf()}, headers=auth_headers)
    await client.post(
        UPLOAD,
        files={"file": ("cv_v2.pdf", b"%PDF-1.4 second version", "application/pdf")},
        headers=auth_headers,
    )
    r = await client.get(DOWNLOAD, headers=auth_headers)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 second version"
    assert "cv_v2.pdf" in r.headers["content-disposition"]
