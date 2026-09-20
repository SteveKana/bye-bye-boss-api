from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.modules.matching import gateway

_FIXTURES = Path(__file__).parent / "fixtures"

# A real GPT-5 response captured from the standalone matchcareer_engine
# prototype (Steve's own CV vs. a real "Product Owner Data" offer at Astek),
# not a hand-written fixture -- if our ported LLMAnalysis schema can't parse
# this, it can't parse what the model actually returns.
_REAL_RESPONSE = (_FIXTURES / "astek_po_data_real_response.json").read_text()


async def test_analyse_match_without_api_key_raises_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", None)
    with pytest.raises(gateway.MatchingUnavailableError):
        await gateway.analyse_match("cv text", "offer text")


async def test_analyse_match_parses_real_captured_response(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: _REAL_RESPONSE)

    analysis = await gateway.analyse_match("cv text", "offer text")

    assert analysis.company_name == "Astek"
    assert analysis.career_score == 86
    assert analysis.ats_score == 72
    assert analysis.ats_potential == 90
    assert analysis.ats_potential >= analysis.ats_score
    assert analysis.blocking_requirements == []
    assert len(analysis.matches) == 24
    assert len(analysis.ats_gaps) == 9
    assert len(analysis.actions) == 9
    soapui = next(m for m in analysis.matches if m.skill == "soapui")
    assert soapui.result.value == "missing"


async def test_analyse_match_wraps_response_in_json_fence(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    fenced = f"```json\n{_REAL_RESPONSE}\n```"
    monkeypatch.setattr(gateway, "_call_openai_sync", lambda **kwargs: fenced)

    analysis = await gateway.analyse_match("cv text", "offer text")
    assert analysis.career_score == 86


async def test_analyse_match_bad_json_raises_failed(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        gateway, "_call_openai_sync", lambda **kwargs: "not json at all"
    )
    with pytest.raises(gateway.MatchingFailedError):
        await gateway.analyse_match("cv text", "offer text")


async def test_analyse_match_schema_violation_raises_failed(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "sk-test")
    # career_score is required -- missing it should fail Pydantic validation.
    monkeypatch.setattr(
        gateway, "_call_openai_sync", lambda **kwargs: json.dumps({"ats_score": 50})
    )
    with pytest.raises(gateway.MatchingFailedError):
        await gateway.analyse_match("cv text", "offer text")
