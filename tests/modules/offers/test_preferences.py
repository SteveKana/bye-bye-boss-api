"""Search preferences as an offer filter (offers/preferences.py): the SQL
version and the Python version must agree, and the matching run must only
shortlist offers that fit what the candidate saved."""

from __future__ import annotations

import uuid

import pytest
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.service import MatchingService, preferences_of
from app.modules.offers import JobOffer, JobOfferRepository, OfferPreferences


def _offer(title: str, **fields) -> JobOffer:
    return JobOffer(
        source="test",
        external_id=title,
        title=title,
        url="https://example.com",
        description=fields.pop("description", "Un poste."),
        embedding=[1.0, 0.0, 0.0],
        **fields,
    )


def make_offers() -> list[JobOffer]:
    return [
        _offer("idf-cdi", region="Île-de-France", contract_type="CDI"),
        _offer(
            "idf-cdd",
            region="Île-de-France",
            contract_type="Contrat à durée déterminée - 12 Mois",
        ),
        _offer(
            "lyon-cdi",
            region="Auvergne-Rhône-Alpes",
            contract_type="permanent, full_time",
        ),
        _offer("no-region", region=None, contract_type="CDI"),
        _offer(
            "remote-bretagne",
            region="Bretagne",
            contract_type="CDI",
            is_full_remote=True,
        ),
        _offer("stage-idf", region="Île-de-France", contract_type="Stage"),
        _offer(
            "alt-lyon",
            region="Auvergne-Rhône-Alpes",
            contract_type="Contrat d'apprentissage",
        ),
        _offer("bare-full-time", region="Hauts-de-France", contract_type="full_time"),
        _offer(
            "contract-full-time",
            region="Hauts-de-France",
            contract_type="contract, full_time",
        ),
        _offer("no-contract", region="Hauts-de-France", contract_type=None),
        _offer("odd-contract", region="Hauts-de-France", contract_type="MIS"),
        _offer(
            "rich",
            region="Île-de-France",
            contract_type="CDI",
            salary_min=60000,
            salary_max=70000,
        ),
        _offer(
            "poor",
            region="Île-de-France",
            contract_type="CDI",
            salary_min=30000,
            salary_max=35000,
        ),
        _offer(
            "freelance-low",
            region="Île-de-France",
            contract_type="Freelance",
            salary_min=100,
            salary_max=120,
        ),
        _offer(
            "hybrid-idf",
            region="Île-de-France",
            contract_type="CDI",
            description="2 jours de télétravail par semaine, 3 jours sur site.",
        ),
    ]


CASES = {
    "regions": OfferPreferences(regions=("Île-de-France",)),
    "regions-strict": OfferPreferences(
        regions=("Île-de-France",), include_unknown_region=False
    ),
    "two-regions": OfferPreferences(regions=("Île-de-France", "Bretagne")),
    "cdi": OfferPreferences(contract_types=("CDI",)),
    "stage-alt": OfferPreferences(contract_types=("Stage", "Alternance")),
    "freelance": OfferPreferences(contract_types=("Freelance",)),
    "salary": OfferPreferences(min_salary=50000),
    "remote-only": OfferPreferences(remote_modes=("Full remote",)),
    "on-site-or-hybrid": OfferPreferences(remote_modes=("Sur site", "Hybride")),
    "hybrid-only": OfferPreferences(remote_modes=("Hybride",)),
    "on-site-only": OfferPreferences(remote_modes=("Sur site",)),
    "remote-or-hybrid": OfferPreferences(remote_modes=("Full remote", "Hybride")),
    "all-three": OfferPreferences(remote_modes=("Sur site", "Hybride", "Full remote")),
    "combo": OfferPreferences(
        regions=("Île-de-France",),
        contract_types=("CDI",),
        min_salary=50000,
        remote_modes=("Sur site",),
    ),
}


@pytest.fixture
async def stored_offers() -> None:
    async with AsyncSessionLocal() as session:
        for offer in make_offers():
            session.add(offer)
        await session.commit()


@pytest.mark.parametrize("name", list(CASES))
async def test_sql_and_python_versions_agree(name: str, stored_offers) -> None:
    prefs = CASES[name]
    async with AsyncSessionLocal() as session:
        stmt = prefs.apply(select(JobOffer))
        in_sql = {o.title for o in (await session.exec(stmt)).all()}
        all_offers = (await session.exec(select(JobOffer))).all()
    in_python = {o.title for o in all_offers if prefs.matches(o)}
    # SQL leaves Hybride-vs-Sur-site to Python (needs_python_remote).
    if prefs.needs_python_remote:
        in_sql = {
            o.title for o in all_offers if o.title in in_sql and prefs.matches_remote(o)
        }
    assert in_sql == in_python


async def test_expected_results(stored_offers) -> None:
    offers = make_offers()

    def titles(prefs: OfferPreferences) -> set[str]:
        return {o.title for o in offers if prefs.matches(o)}

    zone = titles(CASES["regions"])
    # No région kept, remote offer kept, other régions dropped.
    assert {"idf-cdi", "no-region", "remote-bretagne"} <= zone
    assert "lyon-cdi" not in zone and "alt-lyon" not in zone
    assert "no-region" not in titles(CASES["regions-strict"])

    cdi = titles(CASES["cdi"])
    assert "lyon-cdi" in cdi  # "permanent" is a CDI
    assert "idf-cdd" not in cdi and "stage-idf" not in cdi
    # Missing or unrecognised contract label: never hidden.
    assert {"no-contract", "odd-contract"} <= cdi

    # A bare "full_time" label is a CDI (Steve, 2026-10-07); "contract,
    # full_time" stays a Freelance mission.
    assert "bare-full-time" in cdi and "contract-full-time" not in cdi
    freelance = titles(CASES["freelance"])
    assert "bare-full-time" not in freelance and "contract-full-time" in freelance

    assert titles(CASES["stage-alt"]) >= {"stage-idf", "alt-lyon"}

    salary = titles(CASES["salary"])
    assert "rich" in salary and "poor" not in salary
    assert "idf-cdi" in salary  # states no salary
    assert "freelance-low" in salary  # a day rate is not an annual salary

    assert titles(CASES["remote-only"]) == {"remote-bretagne"}
    assert "hybrid-idf" in titles(CASES["hybrid-only"])
    assert "hybrid-idf" not in titles(CASES["on-site-only"])
    assert "idf-cdi" in titles(CASES["on-site-only"])


def test_empty_preferences_are_empty() -> None:
    assert OfferPreferences().is_empty
    assert OfferPreferences(
        remote_modes=("Sur site", "Hybride", "Full remote")
    ).is_empty
    assert not OfferPreferences(regions=("Bretagne",)).is_empty


# -- matching run --------------------------------------------------------------


async def _profile(**overrides) -> CandidateProfile:
    defaults = {
        "user_id": uuid.uuid4(),
        "status": ProfileStatus.complete.value,
        "raw_text": "Product Owner, SQL, Agile.",
        "headline": "Product Owner",
        "skills": ["SQL"],
        "embedding": [1.0, 0.0, 0.0],
    }
    defaults.update(overrides)
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(**defaults)
        )
        await session.commit()
        return profile


def test_preferences_only_apply_once_saved() -> None:
    profile = CandidateProfile(
        user_id=uuid.uuid4(),
        mobility_regions=["Bretagne"],
        contract_types=["CDI"],
    )
    # An older account may hold values from the retired preferences step.
    assert preferences_of(profile) is None
    profile.preferences_saved_at = utcnow()
    prefs = preferences_of(profile)
    assert prefs is not None and prefs.regions == ("Bretagne",)
    profile.mobility_regions = []
    profile.contract_types = []
    assert preferences_of(profile) is None  # nothing restricts anything


async def test_run_only_shortlists_offers_in_the_chosen_zone(stored_offers) -> None:
    profile = await _profile(
        mobility_regions=["Auvergne-Rhône-Alpes"],
        include_unknown_region=False,
        preferences_saved_at=utcnow(),
    )
    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)
        matches = await CandidateMatchRepository(session).list_all_for_profile(
            profile.id
        )
        offers = await JobOfferRepository(session).get_many(
            [m.job_offer_id for m in matches]
        )
    matched = [(o.title,) for o in offers]
    titles = {row[0] for row in matched}
    # Fully remote offers are never tied to a place, so they pass the zone.
    assert titles == {"lyon-cdi", "alt-lyon", "remote-bretagne"}


async def test_run_without_saved_preferences_sees_every_offer(stored_offers) -> None:
    profile = await _profile(mobility_regions=["Bretagne"])  # never saved
    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)
    assert report.pairs_shortlisted > 3
