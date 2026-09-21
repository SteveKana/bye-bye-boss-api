from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.modules.offers.providers.adzuna import AdzunaProvider
from app.modules.offers.providers.france_travail import FranceTravailProvider


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---- France Travail --------------------------------------------------------


async def test_france_travail_not_configured_by_default() -> None:
    provider = FranceTravailProvider()
    assert provider.is_configured() is False
    # search() must short-circuit without ever touching the network.
    assert await provider.search(keywords="product owner", limit=10) == []


async def test_france_travail_search_normalizes_results(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        assert request.headers["Authorization"] == "Bearer fake-token"
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "description": "Pilotage du backlog.",
                        "lieuTravail": {"libelle": "Paris"},
                        "typeContratLibelle": "CDI",
                        "entreprise": {"nom": "Astek"},
                        "salaire": {"libelle": "Annuel de 40000.0 à 48000.0 Euros"},
                        "dateCreation": "2026-09-10T12:00:00.000Z",
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert len(results) == 1
    offer = results[0]
    assert offer.external_id == "123ABC"
    assert offer.title == "Product Owner"
    assert offer.company_name == "Astek"
    assert offer.location == "Paris"
    assert offer.contract_type == "CDI"
    assert offer.salary_label == "Annuel de 40000.0 à 48000.0 Euros"
    assert offer.url == "https://example.fr/offre/123"
    assert offer.published_at is not None
    assert offer.raw["id"] == "123ABC"


async def test_france_travail_prefers_actualisation_date_over_creation(
    monkeypatch,
) -> None:
    """France Travail's own site shows "Actualisé le ..." (dateActualisation),
    not the original creation date -- using dateCreation made long-running
    offers look far staler than France Travail itself reports them as."""
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "dateCreation": "2026-06-01T08:00:00.000Z",
                        "dateActualisation": "2026-09-18T08:00:00.000Z",
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].published_at.isoformat() == "2026-09-18T08:00:00+00:00"


async def test_france_travail_falls_back_to_creation_date_when_no_actualisation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "dateCreation": "2026-06-01T08:00:00.000Z",
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].published_at.isoformat() == "2026-06-01T08:00:00+00:00"


async def test_france_travail_search_failure_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = FranceTravailProvider(client=_client(handler))
    assert await provider.search(keywords="product owner", limit=50) == []


async def test_france_travail_derives_region_from_postal_code(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "lieuTravail": {"libelle": "Paris", "codePostal": "75011"},
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].region == "Île-de-France"


async def test_france_travail_falls_back_to_commune_insee_code_for_region(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        # No codePostal this time, only the INSEE commune code.
                        "lieuTravail": {"libelle": "Rennes", "commune": "35238"},
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].region == "Bretagne"


async def test_france_travail_has_no_region_when_location_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].region is None
    assert results[0].is_full_remote is False


async def test_france_travail_detects_full_remote_from_description(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "fake-token"})
        return httpx.Response(
            200,
            json={
                "resultats": [
                    {
                        "id": "123ABC",
                        "intitule": "Product Owner",
                        "description": "Poste en télétravail 100%, toute la France.",
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].is_full_remote is True


# ---- Adzuna -----------------------------------------------------------------


async def test_adzuna_not_configured_by_default() -> None:
    provider = AdzunaProvider()
    assert provider.is_configured() is False
    assert await provider.search(keywords="product owner", limit=10) == []


async def test_adzuna_search_normalizes_results(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["app_id"] == "app-id"
        assert request.url.params["app_key"] == "app-key"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Business Analyst",
                        "company": {"display_name": "Doctolib"},
                        "location": {"display_name": "Lyon, Rhône"},
                        "description": "Analyse des besoins métier.",
                        "salary_min": 38000,
                        "salary_max": 45000,
                        "redirect_url": "https://adzuna.fr/details/999",
                        "created": "2026-09-08T09:30:00Z",
                        "contract_type": "permanent",
                        "contract_time": "full_time",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert len(results) == 1
    offer = results[0]
    assert offer.external_id == "999"
    assert offer.title == "Business Analyst"
    assert offer.company_name == "Doctolib"
    assert offer.location == "Lyon, Rhône"
    assert offer.salary_min == 38000
    assert offer.salary_max == 45000
    assert offer.contract_type == "permanent, full_time"
    assert offer.url == "https://adzuna.fr/details/999"
    assert offer.published_at is not None


async def test_adzuna_search_failure_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    provider = AdzunaProvider(client=_client(handler))
    assert await provider.search(keywords="business analyst", limit=50) == []


async def test_adzuna_derives_region_from_location_area_breadcrumb(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Business Analyst",
                        "location": {
                            "display_name": "Lyon, Rhône",
                            # Breadcrumb order per Adzuna's docs (country ->
                            # ... -> city) -- no confirmed French example, so
                            # the provider tries every element rather than
                            # assuming a fixed index (see its docstring).
                            "area": ["France", "Auvergne-Rhône-Alpes", "Rhône", "Lyon"],
                        },
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].region == "Auvergne-Rhône-Alpes"


async def test_adzuna_has_no_region_when_area_has_no_recognizable_region(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Business Analyst",
                        "location": {"display_name": "Lyon"},
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].region is None
    assert results[0].is_full_remote is False


async def test_adzuna_detects_full_remote_from_title(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Business Analyst - Full Remote",
                        "location": {"display_name": "Lyon"},
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].is_full_remote is True
