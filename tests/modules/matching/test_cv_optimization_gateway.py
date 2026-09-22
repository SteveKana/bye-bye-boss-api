from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.modules.matching import cv_optimization_gateway as gateway

_VALID_RESPONSE = json.dumps(
    {
        "headline": "Product Owner orienté Data & Analytics",
        "summary": "Product Owner avec 5 ans d'expérience...",
        "summary_why": "Met en avant la dimension data attendue par l'offre.",
        "experiences": [
            {
                "title": "Product Owner orienté Data & Analytics",
                "company": "Doctolib",
                "period": "Janv. 2022 - Aujourd'hui",
                "bullets": [
                    {
                        "text": "Exploitation de la data (SQL)",
                        "status": "added",
                        "original_text": None,
                        "why": "Déductible de l'expérience réelle.",
                    },
                    {
                        "text": "Gestion du backlog produit",
                        "status": "unchanged",
                        "original_text": None,
                        "why": "",
                    },
                ],
            }
        ],
        "skills": [
            {"skill": "Agile", "added": False},
            {"skill": "SQL", "added": True},
        ],
        "advice": "Les ajouts en vert intègrent des mots-clés pertinents.",
    }
)


async def test_optimize_cv_without_api_key_raises_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", None)
    with pytest.raises(gateway.CVOptimizationUnavailableError):
        await gateway.optimize_cv({}, "offer text", {})


async def test_optimize_cv_parses_valid_response(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: _VALID_RESPONSE)

    result = await gateway.optimize_cv({}, "offer text", {})

    assert result.headline == "Product Owner orienté Data & Analytics"
    assert len(result.experiences) == 1
    assert result.experiences[0].bullets[0].status.value == "added"
    assert result.skills[1].skill == "SQL"
    assert result.skills[1].added is True


async def test_optimize_cv_accepts_plain_string_bullets_and_skills(monkeypatch) -> None:
    """The model may return an unremarkable bullet/skill as a bare string
    instead of the full object shape -- same tolerance as llm_schema's
    Action/JobSkill accept_plain_string."""
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    payload = json.dumps(
        {
            "headline": "",
            "summary": "",
            "summary_why": "",
            "experiences": [
                {
                    "title": "Chef de projet",
                    "company": "Appvicer",
                    "period": "2017-2019",
                    "bullets": ["Gestion de projet digital"],
                }
            ],
            "skills": ["JIRA"],
            "advice": "",
        }
    )
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: payload)

    result = await gateway.optimize_cv({}, "offer text", {})

    assert result.experiences[0].bullets[0].text == "Gestion de projet digital"
    assert result.experiences[0].bullets[0].status.value == "unchanged"
    assert result.skills[0].skill == "JIRA"
    assert result.skills[0].added is False


async def test_optimize_cv_wraps_response_in_json_fence(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    fenced = f"```json\n{_VALID_RESPONSE}\n```"
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: fenced)

    result = await gateway.optimize_cv({}, "offer text", {})
    assert result.headline == "Product Owner orienté Data & Analytics"


async def test_optimize_cv_bad_json_raises_failed(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: "not json")
    with pytest.raises(gateway.CVOptimizationFailedError):
        await gateway.optimize_cv({}, "offer text", {})


async def test_optimize_cv_defaults_to_configured_model(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(get_settings(), "CV_OPTIMIZATION_OPENAI_MODEL", "gpt-5")
    captured: dict = {}

    def _fake_call(**kwargs):
        captured.update(kwargs)
        return _VALID_RESPONSE

    monkeypatch.setattr(gateway, "_call_openai_sync", _fake_call)

    await gateway.optimize_cv({}, "offer text", {})

    assert captured["model"] == "gpt-5"
