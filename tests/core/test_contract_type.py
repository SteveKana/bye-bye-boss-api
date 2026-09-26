from __future__ import annotations

from app.core.contract_type import guess_contract_type


def test_detects_common_contract_phrasings() -> None:
    assert guess_contract_type("Product Owner en CDI") == "CDI"
    assert guess_contract_type("Contrat à durée déterminée de 6 mois") == "CDD"
    assert guess_contract_type("Mission freelance, TJM à définir") == "Freelance"
    assert guess_contract_type("Stage de fin d'études") == "Stage"
    assert guess_contract_type("Alternance Business Analyst") == "Alternance"
    assert guess_contract_type("Poste en intérim de 3 mois") == "Intérim"


def test_stage_and_alternance_win_over_incidental_permanent_wording() -> None:
    # "Poste permanent au sein de l'entreprise d'accueil" is boilerplate that
    # shows up on real apprenticeship/internship listings -- must not be
    # misread as CDI.
    assert (
        guess_contract_type(
            "Alternance", "Poste permanent au sein de l'entreprise d'accueil"
        )
        == "Alternance"
    )
    assert (
        guess_contract_type("Stage", "Poste permanent au sein de l'équipe produit")
        == "Stage"
    )


def test_returns_none_when_nothing_recognized() -> None:
    assert guess_contract_type("Product Owner") is None
    assert guess_contract_type(None) is None
    assert guess_contract_type(None, None) is None
    assert guess_contract_type("") is None


def test_combines_title_and_description() -> None:
    assert guess_contract_type("Chef de projet", "Poste en CDI à pourvoir") == "CDI"
