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
    assert offer.salary_min == 40000
    assert offer.salary_max == 48000
    assert offer.url == "https://example.fr/offre/123"
    assert offer.published_at is not None
    assert offer.raw["id"] == "123ABC"


async def test_france_travail_guesses_contract_type_when_fields_are_empty(
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
                        "intitule": "Product Owner IT - CDI",
                        "description": "Type : CDI / Mission longue durée.",
                        # No typeContratLibelle/typeContrat at all -- happens
                        # even on France Travail, though less often than on
                        # Adzuna.
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].contract_type == "CDI"


async def test_france_travail_never_overrides_typecontrat_with_a_guess(
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
                        "intitule": "Stage Business Analyst",
                        "description": "Stage de fin d'études.",
                        # Wording would guess "Stage", but France Travail did
                        # provide a value -- that value must win.
                        "typeContrat": "CDD",
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].contract_type == "CDD"


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


async def test_france_travail_extracts_daily_rate_from_salaire_libelle(
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
                        "intitule": "Développeur freelance",
                        "typeContratLibelle": "Mission freelance",
                        "salaire": {"libelle": "TJM : 500€"},
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="developpeur", limit=50)

    assert results[0].daily_rate_min == 500
    assert results[0].daily_rate_max == 500


async def test_france_travail_has_no_daily_rate_for_a_salaried_offer(
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
                        "salaire": {
                            "libelle": "Annuel de 45000.0 Euros à 55000.0 Euros"
                        },
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].daily_rate_min is None
    assert results[0].daily_rate_max is None


async def test_france_travail_extracts_annual_salary_from_salaire_libelle(
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
                        # Real-world label: "Euros" after the first figure,
                        # plus trailing avantages text after the range.
                        "salaire": {
                            "libelle": (
                                "Annuel de 45000.0 Euros à 55000.0 Euros - TR, CSE"
                            )
                        },
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].salary_min == 45000
    assert results[0].salary_max == 55000


async def test_france_travail_annualizes_mensuel_salaire_libelle(monkeypatch) -> None:
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
                        "salaire": {
                            "libelle": "Mensuel de 2500.0 Euros à 3000.0 Euros"
                        },
                        "origineOffre": {"urlOrigine": "https://example.fr/offre/123"},
                    }
                ]
            },
        )

    provider = FranceTravailProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].salary_min == 30000
    assert results[0].salary_max == 36000


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


async def test_adzuna_guesses_contract_type_when_fields_are_empty(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Product Owner freelance",
                        "description": "Mission en freelance, TJM à définir.",
                        "redirect_url": "https://adzuna.fr/details/999",
                        # No contract_type/contract_time at all -- the
                        # common case for a lot of Adzuna listings.
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].contract_type == "Freelance"


async def test_adzuna_never_overrides_its_own_contract_fields_with_a_guess(
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
                        "title": "Stage Business Analyst",
                        "description": "Stage de fin d'études.",
                        "redirect_url": "https://adzuna.fr/details/999",
                        # Adzuna did provide something -- even though the
                        # wording would otherwise guess "Stage", the actual
                        # provided value must win.
                        "contract_time": "part_time",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].contract_type == "part_time"


async def test_adzuna_has_no_contract_type_when_nothing_recognized(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Product Owner",
                        "description": "Rejoignez notre équipe produit.",
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="product owner", limit=50)

    assert results[0].contract_type is None


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


async def test_adzuna_extracts_daily_rate_from_description(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_ID", "app-id")
    monkeypatch.setattr(get_settings(), "ADZUNA_APP_KEY", "app-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "999",
                        "title": "Développeur freelance",
                        "description": "Mission longue durée. TJM : 450-550€.",
                        "location": {"display_name": "Lyon"},
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="developpeur", limit=50)

    assert results[0].daily_rate_min == 450
    assert results[0].daily_rate_max == 550


async def test_adzuna_has_no_daily_rate_for_a_salaried_offer(monkeypatch) -> None:
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
                        "description": "Poste en CDI, salaire selon profil.",
                        "location": {"display_name": "Lyon"},
                        "salary_min": 38000,
                        "salary_max": 45000,
                        "redirect_url": "https://adzuna.fr/details/999",
                    }
                ]
            },
        )

    provider = AdzunaProvider(client=_client(handler))
    results = await provider.search(keywords="business analyst", limit=50)

    assert results[0].daily_rate_min is None
    assert results[0].daily_rate_max is None


async def test_france_travail_census_reads_totals_from_content_range(
    monkeypatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.modules.offers.providers import france_travail

    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(get_settings(), "FRANCE_TRAVAIL_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(france_travail, "_CALL_SPACING_SECONDS", 0)
    monkeypatch.setattr(france_travail, "DEPARTEMENTS", ("75", "13", "99"))

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t"})
        assert request.url.params["range"] == "0-0"
        assert "minCreationDate" in request.url.params
        dept = request.url.params.get("departement")
        if dept == "75":
            return httpx.Response(
                206,
                headers={"Content-Range": "offres 0-0/1234"},
                json={"resultats": [{}]},
            )
        if dept == "13":
            return httpx.Response(204)
        if dept == "99":
            return httpx.Response(500)
        return httpx.Response(
            206, headers={"Content-Range": "offres 0-0/98765"}, json={"resultats": [{}]}
        )

    provider = FranceTravailProvider(client=_client(handler))
    counts = await provider.census(since=datetime.now(UTC) - timedelta(days=15))

    assert counts == {"ALL": 98765, "75": 1234, "13": 0, "99": None}
