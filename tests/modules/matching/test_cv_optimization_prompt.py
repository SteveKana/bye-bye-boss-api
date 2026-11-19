"""The CV-optimization prompts accept obvious deductions (API REST -> JSON) and
make the advice agree with the skills the CV ends up with."""

from app.modules.matching.cv_optimization_prompt import (
    build_advice_prompt,
    build_prompt,
)


def test_main_prompt_accepts_obvious_deductions_but_not_neighbours():
    prompt = build_prompt({"a": 1}, "offre", {"x": 1})
    assert "Déductions ÉVIDENTES" in prompt
    assert '"API REST" -> "JSON"' in prompt
    assert "mapping de données" in prompt
    # A thematic link is still not enough (Kafka, API Management stay gaps).
    assert "Kafka" in prompt and "un lien thématique ne" in prompt
    # The old blanket "when in doubt, don't add" is gone.
    assert "En cas de doute sur une déduction, ne pas l'ajouter" not in prompt


def test_main_prompt_ties_the_advice_to_the_final_cv():
    prompt = build_prompt({"a": 1}, "offre", {"x": 1})
    assert "se\nrédige EN DERNIER" in prompt
    assert "déduction évidente" in prompt
    assert "doit quand même être ajouté" in prompt  # analysis is more cautious


def test_advice_refresh_prompt_does_not_flag_obvious_deductions():
    prompt = build_advice_prompt({"skills": ["API REST"]}, "offre")
    assert "déduction évidente" in prompt
    assert "JSON" in prompt
