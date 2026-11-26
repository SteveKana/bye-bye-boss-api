"""Free CV adaptation for visitors without an account."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.cv import extraction, gateway
from app.modules.cv.models import CandidateProfile
from app.modules.matching import cv_optimization_gateway, public_quota
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_schema import (
    CVOptimizationBullet,
    CVOptimizationExperience,
    CVOptimizationFormation,
    CVOptimizationLanguage,
    CVOptimizationResult,
    CVOptimizationSkill,
)
from app.modules.matching.cv_pdf import PDF_LABELS, pdf_labels

BASE = "/api/v1/public/cv-adapter"
OFFER = "Senior Product Owner. We are looking for a data-minded PO. " * 4

_EXTRACTED = {
    "first_name": "Camille",
    "last_name": "Martin",
    "email": "camille.martin@exemple.fr",
    "location": "Paris",
    "experiences": [
        {
            "title": "Product Owner",
            "company": "Galec",
            "period": "2022 – 2026",
            "description": "Gestion du backlog produit\nRédaction des user stories",
            "tools": ["Jira", "Figma"],
        }
    ],
    "skills": ["Gestion de projet", "Scrum"],
    "formations": [{"title": "Master MSI", "school_period": "Dauphine • 2016"}],
    "languages": [{"name": "Français", "level": "Langue maternelle"}],
    "certifications": [{"title": "PSPO I", "issuer_period": "Scrum.org • 2021"}],
}


@pytest.fixture(autouse=True)
async def _isolation(monkeypatch, tmp_path):
    await ratelimit.backend.reset()
    monkeypatch.setattr(extraction, "_extract_pdf", lambda _data: "texte brut du cv")
    monkeypatch.setattr(get_settings(), "CV_UPLOAD_DIR", str(tmp_path))

    async def structure(_raw: str) -> dict:
        return _EXTRACTED

    monkeypatch.setattr(gateway, "structure_cv_text", structure)
    yield
    await ratelimit.backend.reset()


def _model(monkeypatch, calls: list | None = None, *, fail: bool = False) -> None:
    async def fake(cv, offer_text, analysis, **kwargs):
        if calls is not None:
            calls.append({"cv": cv, "offer": offer_text, **kwargs})
        if fail:
            raise cv_optimization_gateway.CVOptimizationFailedError()
        return CVOptimizationResult(
            headline="Senior Product Owner",
            summary="Data-driven product owner.",
            summary_why="Pourquoi : l'offre insiste sur la donnée.",
            experiences=[
                CVOptimizationExperience(
                    title="Product Owner",
                    bullets=[
                        CVOptimizationBullet(
                            text="Backlog management",
                            original_text="Gestion du backlog produit",
                        ),
                        CVOptimizationBullet(
                            text="Writing user stories",
                            original_text="Rédaction des user stories",
                        ),
                    ],
                )
            ],
            skills=[
                CVOptimizationSkill(
                    skill="Project management",
                    original="Gestion de projet",
                    category="Delivery",
                ),
                CVOptimizationSkill(skill="Scrum", original="Scrum", category="Agile"),
            ],
            advice="Conseil.",
            language="en",
            formations=[CVOptimizationFormation(title="MSI Master's degree")],
            languages=[CVOptimizationLanguage(name="French", level="Native")],
        )

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", fake)


def _cv_file() -> dict:
    return {"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")}


async def _generate(client: AsyncClient, offer: str = OFFER, **extra):
    return await client.post(
        f"{BASE}/generate", files=_cv_file(), data={"offer_text": offer, **extra}
    )


async def test_needs_no_account_and_translates_into_the_offer_language(
    client: AsyncClient, monkeypatch
) -> None:
    calls: list = []
    _model(monkeypatch, calls)

    resp = await _generate(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "en"
    assert body["headline"] == "Senior Product Owner"
    # The identity comes from the visitor's own CV.
    assert (body["first_name"], body["email"]) == (
        "Camille",
        "camille.martin@exemple.fr",
    )
    exp = body["experiences"][0]
    assert exp["company"] == "Galec" and exp["period"] == "2022 – 2026"
    assert exp["tools"] == ["Jira", "Figma"]
    assert [b["text"] for b in exp["bullets"]] == [
        "Backlog management",
        "Writing user stories",
    ]
    # Skills are the visitor's own, shown in the offer's language.
    assert [s["skill"] for s in body["skills"]] == ["Project management", "Scrum"]
    assert not any(s["added"] for s in body["skills"])
    assert body["formations"][0]["title"] == "MSI Master's degree"
    assert body["formations"][0]["school_period"] == "Dauphine • 2016"
    assert body["languages"] == [{"name": "French", "level": "Native"}]
    assert body["certifications"][0]["title"] == "PSPO I"
    # The model was asked to write in the offer's language.
    assert calls[0]["offer_language"] is True
    assert "formations" in calls[0]["cv"]


async def test_stores_nothing(client: AsyncClient, monkeypatch, tmp_path) -> None:
    _model(monkeypatch)
    assert (await _generate(client)).status_code == 200
    async with AsyncSessionLocal() as session:
        assert (await session.exec(select(CandidateProfile))).all() == []
        assert (await session.exec(select(CVOptimization))).all() == []
    assert list(tmp_path.iterdir()) == []  # the CV file is not kept either


async def test_refuses_a_short_offer_without_using_an_adaptation(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    resp = await _generate(client, "trop court")
    assert resp.status_code == 400
    status = (await client.get(f"{BASE}/status")).json()
    assert status["remaining"] == status["daily_limit"]


async def test_three_a_day_then_a_clear_refusal(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    for _ in range(3):
        assert (await _generate(client)).status_code == 200
    assert (await client.get(f"{BASE}/status")).json()["remaining"] == 0

    resp = await _generate(client)

    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "public_cv_daily_limit"
    assert "compte gratuit" in resp.json()["error"]["message"]


async def test_each_visitor_has_their_own_allowance(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    for _ in range(3):
        await client.post(
            f"{BASE}/generate",
            files=_cv_file(),
            data={"offer_text": OFFER},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
    other = await client.post(
        f"{BASE}/generate",
        files=_cv_file(),
        data={"offer_text": OFFER},
        headers={"X-Forwarded-For": "2.2.2.2"},
    )
    assert other.status_code == 200


async def test_a_forged_forwarded_header_does_not_buy_a_new_allowance(
    client: AsyncClient, monkeypatch
) -> None:
    """The visitor's address is the one our proxy appended (the end of the
    header): whatever they write at the start changes nothing."""
    _model(monkeypatch)
    for i in range(3):
        resp = await client.post(
            f"{BASE}/generate",
            files=_cv_file(),
            data={"offer_text": OFFER},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 9.9.9.9"},
        )
        assert resp.status_code == 200
    forged = await client.post(
        f"{BASE}/generate",
        files=_cv_file(),
        data={"offer_text": OFFER},
        headers={"X-Forwarded-For": "10.0.0.99, 9.9.9.9"},
    )
    assert forged.status_code == 429


async def test_the_whole_site_has_a_daily_cap(client: AsyncClient, monkeypatch) -> None:
    _model(monkeypatch)
    monkeypatch.setattr(get_settings(), "PUBLIC_CV_DAILY_GLOBAL_CAP", 2)
    for ip in ("1.1.1.1", "2.2.2.2"):
        resp = await client.post(
            f"{BASE}/generate",
            files=_cv_file(),
            data={"offer_text": OFFER},
            headers={"X-Forwarded-For": ip},
        )
        assert resp.status_code == 200

    full = await client.post(
        f"{BASE}/generate",
        files=_cv_file(),
        data={"offer_text": OFFER},
        headers={"X-Forwarded-For": "3.3.3.3"},
    )

    assert full.status_code == 429
    assert full.json()["error"]["code"] == "public_cv_capacity"
    # That visitor was not charged for a call that never ran.
    assert await public_quota.remaining_for("3.3.3.3") == 3


async def test_a_failure_on_our_side_gives_the_adaptation_back(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch, fail=True)
    resp = await _generate(client)
    assert resp.status_code >= 500
    status = (await client.get(f"{BASE}/status")).json()
    assert status["remaining"] == status["daily_limit"]


async def test_a_cv_without_experience_is_refused_and_not_charged(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)

    async def empty(_raw: str) -> dict:
        return {**_EXTRACTED, "experiences": []}

    monkeypatch.setattr(gateway, "structure_cv_text", empty)
    resp = await _generate(client)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "cv_without_experience"
    status = (await client.get(f"{BASE}/status")).json()
    assert status["remaining"] == status["daily_limit"]


async def test_the_switch_turns_everything_off(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    monkeypatch.setattr(get_settings(), "PUBLIC_CV_ADAPTER_ENABLED", False)
    resp = await _generate(client)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "public_cv_disabled"
    assert (await client.get(f"{BASE}/status")).json()["enabled"] is False


async def test_turnstile_is_required_only_when_configured(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    assert (await client.get(f"{BASE}/status")).json()["captcha_required"] is False

    monkeypatch.setattr(get_settings(), "TURNSTILE_SECRET_KEY", "secret")
    assert (await client.get(f"{BASE}/status")).json()["captcha_required"] is True
    missing = await _generate(client)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "captcha_failed"

    async def accepted(token, *, remote_ip=None):
        assert token == "good-token"

    from app.modules.matching.routes.v1 import public_cv_routes

    monkeypatch.setattr(public_cv_routes, "verify_turnstile", accepted)
    ok = await _generate(client, captcha_token="good-token")
    assert ok.status_code == 200


async def test_my_ip_shows_the_address_read_for_the_visitor(
    client: AsyncClient,
) -> None:
    resp = await client.get(
        f"{BASE}/my-ip", headers={"X-Forwarded-For": "5.5.5.5, 6.6.6.6"}
    )
    assert resp.json() == {"ip": "6.6.6.6", "forwarded_for": "5.5.5.5, 6.6.6.6"}


async def test_the_pdf_is_rendered_from_the_edited_cv_in_its_language(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    cv = (await _generate(client)).json()
    cv["summary"] = "Edited by the visitor."

    async def boom(*args, **kwargs):
        raise AssertionError("the PDF must not call the model")

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", boom)
    for template in ("sobre", "visuelle"):
        resp = await client.post(f"{BASE}/pdf", params={"template": template}, json=cv)
        assert resp.status_code == 200
        assert resp.content.startswith(b"%PDF")
        assert (
            'filename="CV_Camille_Martin_Senior_Product_Owner.pdf"'
            in resp.headers["content-disposition"]
        )


async def test_advice_is_rewritten_from_the_edited_cv(
    client: AsyncClient, monkeypatch
) -> None:
    _model(monkeypatch)
    cv = (await _generate(client)).json()
    seen: dict = {}

    async def fake_advice(cv_arg, offer_text, **kwargs):
        seen["cv"], seen["offer"] = cv_arg, offer_text
        return "Nouveau conseil."

    monkeypatch.setattr(cv_optimization_gateway, "refresh_advice", fake_advice)
    resp = await client.post(f"{BASE}/advice", json={"offer_text": OFFER, "cv": cv})

    assert resp.json() == {"advice": "Nouveau conseil."}
    assert seen["cv"]["headline"] == "Senior Product Owner"
    assert seen["cv"]["skills"] == ["Project management", "Scrum"]


async def test_an_oversized_cv_payload_is_refused(client: AsyncClient) -> None:
    resp = await client.post(f"{BASE}/pdf", json={"summary": "x" * 5000})
    assert resp.status_code == 422


async def test_the_link_reader_is_open_to_visitors(
    client: AsyncClient, monkeypatch
) -> None:
    from app.modules.matching.offer_page_reader import OfferPage
    from app.modules.matching.routes.v1 import public_cv_routes

    async def fake_read(url):
        return OfferPage(
            text="Texte de l'offre",
            title="PO",
            host="exemple.fr",
            used_structured_data=True,
        )

    monkeypatch.setattr(public_cv_routes, "read_offer_from_url", fake_read)
    resp = await client.post(
        f"{BASE}/offer-from-url", json={"url": "https://exemple.fr/offre"}
    )
    assert resp.status_code == 200
    assert resp.json()["host"] == "exemple.fr"


def test_every_language_has_every_pdf_label() -> None:
    keys = set(PDF_LABELS["fr"])
    assert all(set(labels) == keys for labels in PDF_LABELS.values())
    assert pdf_labels("EN-us")["skills"] == "Skills"
    assert pdf_labels(None)["skills"] == "Compétences"
    assert pdf_labels("ja")["skills"] == "Skills"  # no table: English
