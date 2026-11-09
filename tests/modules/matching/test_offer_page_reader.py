from __future__ import annotations

import json

import httpx
import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.matching import offer_page_reader as reader
from app.modules.matching.offer_page_reader import (
    OfferPageError,
    extract_offer_text,
    read_offer_from_url,
)

LONG = "Tu piloteras le backlog produit et animeras les cérémonies agiles. " * 6


def _jsonld_page() -> str:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Product Owner Data",
        "description": f"<p>{LONG}</p><ul><li>SQL</li><li>Jira</li></ul>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": {
            "@type": "Place",
            "address": {"addressLocality": "Paris", "addressRegion": "Île-de-France"},
        },
    }
    return (
        "<html><head><script type='application/ld+json'>"
        + json.dumps(posting)
        + "</script></head><body>Menu</body></html>"
    )


def _plain_page() -> str:
    return (
        "<html><head><title>Offre</title></head><body><nav>Accueil Connexion</nav>"
        f"<main><h1>Chef de projet SI</h1><p>{LONG}</p><ul><li>Recette</li></ul>"
        "</main><footer>Mentions légales</footer><script>var x=1</script></body></html>"
    )


def test_reads_the_structured_job_posting() -> None:
    text, title, structured = extract_offer_text(_jsonld_page())
    assert structured and title == "Product Owner Data"
    assert text.startswith("Product Owner Data\nAcme — Paris, Île-de-France")
    assert "backlog produit" in text and "- SQL" in text and "Menu" not in text


def test_falls_back_to_the_main_text_without_menus_or_scripts() -> None:
    text, title, structured = extract_offer_text(_plain_page())
    assert not structured and title == "Chef de projet SI"
    assert "backlog produit" in text and "- Recette" in text
    assert "Accueil" not in text and "Mentions légales" not in text
    assert "var x" not in text


def test_a_page_that_is_empty_without_javascript_is_reported() -> None:
    page = "<html><body><div id='app'></div><script>load()</script></body></html>"
    with pytest.raises(OfferPageError, match="colle le texte"):
        extract_offer_text(page)


def _resolver(monkeypatch, mapping: dict[str, list[str]]) -> None:
    async def fake(host: str, port: int) -> list[str]:
        return mapping.get(host, ["93.184.216.34"])

    monkeypatch.setattr(reader, "_resolve", fake)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/offre",
        "http://127.0.0.1/offre",
        "http://10.0.0.5/offre",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/offre",
        "ftp://example.com/offre",
        "https://example.com:8443/offre",
    ],
)
async def test_refuses_non_public_or_odd_addresses(monkeypatch, url) -> None:
    _resolver(
        monkeypatch,
        {
            "localhost": ["127.0.0.1"],
            "127.0.0.1": ["127.0.0.1"],
            "10.0.0.5": ["10.0.0.5"],
            "169.254.169.254": ["169.254.169.254"],
            "::1": ["::1"],
        },
    )
    with pytest.raises(OfferPageError):
        await read_offer_from_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/123",
        "https://fr.indeed.com/viewjob?jk=abc",
        "https://www.welcometothejungle.com/fr/companies/x/jobs/y",
    ],
)
async def test_does_not_even_try_sites_that_forbid_automation(monkeypatch, url) -> None:
    _resolver(monkeypatch, {})
    with pytest.raises(OfferPageError, match="n'autorise pas la lecture automatique"):
        await read_offer_from_url(url)


def _serve(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(reader.httpx, "AsyncClient", factory)


async def test_reads_a_page_and_sends_an_honest_user_agent(monkeypatch) -> None:
    _resolver(monkeypatch, {})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            text=_jsonld_page(),
            headers={"content-type": "text/html; charset=utf-8"},
        )

    _serve(monkeypatch, handler)
    page = await read_offer_from_url("example.com/offre/1")  # scheme added
    assert page.host == "example.com" and page.title == "Product Owner Data"
    assert str(seen[0].url) == "https://example.com/offre/1"
    assert seen[0].headers["user-agent"].startswith("ByeByeBoss/")


async def test_a_redirect_to_a_private_address_is_refused(monkeypatch) -> None:
    _resolver(monkeypatch, {"intranet.example": ["10.1.2.3"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://intranet.example/x"})

    _serve(monkeypatch, handler)
    with pytest.raises(OfferPageError, match="pas autorisé"):
        await read_offer_from_url("https://example.com/offre")


async def test_redirects_are_followed_and_capped(monkeypatch) -> None:
    _resolver(monkeypatch, {})

    def loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/again"})

    _serve(monkeypatch, loop)
    with pytest.raises(OfferPageError, match="trop de redirections"):
        await read_offer_from_url("https://example.com/offre")


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "bloque la lecture automatique"),
        (429, "bloque la lecture automatique"),
        (404, "introuvable"),
        (410, "introuvable"),
        (500, "erreur"),
    ],
)
async def test_http_errors_become_clear_messages(monkeypatch, status, message) -> None:
    _resolver(monkeypatch, {})
    _serve(monkeypatch, lambda request: httpx.Response(status, text="no"))
    with pytest.raises(OfferPageError, match=message):
        await read_offer_from_url("https://example.com/offre")


async def test_a_non_html_answer_is_refused(monkeypatch) -> None:
    _resolver(monkeypatch, {})
    _serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        ),
    )
    with pytest.raises(OfferPageError, match="pas à une page web"):
        await read_offer_from_url("https://example.com/offre.pdf")


# -- the admin route ----------------------------------------------------------

URL = "/api/v1/matching/admin/offer-from-url"


async def _login(client: AsyncClient, email: str, *, admin: bool) -> dict:
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
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "supersecret"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_route_is_admin_only(client: AsyncClient) -> None:
    body = {"url": "https://example.com/offre"}
    assert (await client.post(URL, json=body)).status_code == 401
    headers = await _login(client, "user@example.com", admin=False)
    assert (await client.post(URL, json=body, headers=headers)).status_code == 403


async def test_route_returns_the_text_or_a_clear_400(
    client: AsyncClient, monkeypatch
) -> None:
    headers = await _login(client, "admin@example.com", admin=True)
    _resolver(monkeypatch, {})
    _serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, text=_jsonld_page(), headers={"content-type": "text/html"}
        ),
    )
    ok = await client.post(URL, json={"url": "https://example.com/o"}, headers=headers)
    assert ok.status_code == 200
    data = ok.json()
    assert data["host"] == "example.com" and data["structured"] is True
    assert "backlog produit" in data["text"]

    blocked = await client.post(
        URL, json={"url": "https://www.linkedin.com/jobs/view/1"}, headers=headers
    )
    assert blocked.status_code == 400
    assert "colle le texte" in json.dumps(blocked.json(), ensure_ascii=False)
