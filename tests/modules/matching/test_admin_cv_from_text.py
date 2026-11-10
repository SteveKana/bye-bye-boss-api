from __future__ import annotations

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.matching import cv_optimization_gateway
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_prompt import NO_ANALYSIS_TEXT, build_prompt
from app.modules.matching.cv_optimization_schema import CVOptimizationResult

URL = "/api/v1/matching/admin/cv-from-offer-text"
OFFER = "Product Owner Data. " * 15  # long enough (>= 100 characters)


async def _login(client: AsyncClient, email: str, *, admin: bool):
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "supersecret", "first_name": "Steve"},
    )
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).one()
        user.isadmin = admin
        user.is_verified = True
        session.add(user)
        await session.commit()
        user_id = user.id
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret"}
    )
    return user_id, {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _profile(user_id, status: str = ProfileStatus.complete.value) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            CandidateProfile(
                user_id=user_id,
                status=status,
                first_name="Steve",
                last_name="Kana",
                headline="Product Owner",
                skills=["Jira"],
                experiences=[
                    {
                        "title": "Product Owner",
                        "company": "Galec",
                        "period": "2022-2026",
                        "description": "Gestion du backlog produit",
                    }
                ],
            )
        )
        await session.commit()


def _mock(monkeypatch, calls: list | None = None) -> None:
    async def fake(cv, offer_text, analysis, **kwargs):
        if calls is not None:
            calls.append((cv, offer_text, analysis))
        return CVOptimizationResult(
            headline="Product Owner Data",
            summary="Résumé adapté",
            summary_why="Pertinent pour cette offre.",
            advice="Conseil.",
        )

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", fake)


def test_prompt_says_so_when_there_is_no_analysis() -> None:
    assert NO_ANALYSIS_TEXT in build_prompt({"a": 1}, "offre", None)
    assert NO_ANALYSIS_TEXT not in build_prompt({"a": 1}, "offre", {"x": 1})


async def test_only_admins_can_use_it(client: AsyncClient) -> None:
    assert (await client.post(URL, json={"offer_text": OFFER})).status_code == 401
    _, headers = await _login(client, "user@example.com", admin=False)
    resp = await client.post(URL, json={"offer_text": OFFER}, headers=headers)
    assert resp.status_code == 403


async def test_generates_from_pasted_text_and_stores_nothing(
    client: AsyncClient, monkeypatch
) -> None:
    calls: list = []
    _mock(monkeypatch, calls)
    user_id, headers = await _login(client, "admin@example.com", admin=True)
    await _profile(user_id)

    resp = await client.post(URL, json={"offer_text": OFFER}, headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["headline"] == "Product Owner Data"
    # The real experience is kept (title/company/period come from the profile).
    assert body["experiences"][0]["company"] == "Galec"
    assert body["skills"] == [{"skill": "Jira", "added": False}]
    # The model got the pasted text and no analysis.
    assert calls[0][1] == OFFER.strip() and calls[0][2] is None
    async with AsyncSessionLocal() as session:
        assert (await session.exec(select(CVOptimization))).all() == []


async def test_refuses_a_too_short_text_and_an_incomplete_profile(
    client: AsyncClient, monkeypatch
) -> None:
    _mock(monkeypatch)
    user_id, headers = await _login(client, "admin@example.com", admin=True)
    short = await client.post(URL, json={"offer_text": "trop court"}, headers=headers)
    assert short.status_code == 400
    no_profile = await client.post(URL, json={"offer_text": OFFER}, headers=headers)
    assert no_profile.status_code == 404
    await _profile(user_id, status=ProfileStatus.draft.value)
    draft = await client.post(URL, json={"offer_text": OFFER}, headers=headers)
    assert draft.status_code == 400


async def test_pdf_is_rendered_from_the_result_without_calling_the_model(
    client: AsyncClient, monkeypatch
) -> None:
    _mock(monkeypatch)
    user_id, headers = await _login(client, "admin@example.com", admin=True)
    await _profile(user_id)
    result = (
        await client.post(URL, json={"offer_text": OFFER}, headers=headers)
    ).json()
    assert result["filename"] == "CV_Steve_Kana_Product_Owner_Data.pdf"

    async def boom(*args, **kwargs):
        raise AssertionError("the PDF must not call the model")

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", boom)
    for template in ("sobre", "visuelle"):
        resp = await client.post(
            f"{URL}/pdf", params={"template": template}, json=result, headers=headers
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        assert (
            'filename="CV_Steve_Kana_Product_Owner_Data.pdf"'
            in resp.headers["content-disposition"]
        )
    forbidden = await client.post(f"{URL}/pdf", json=result)
    assert forbidden.status_code == 401
