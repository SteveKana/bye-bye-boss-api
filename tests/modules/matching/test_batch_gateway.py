from __future__ import annotations

import json

import pytest

from app.modules.matching import batch_gateway, gateway
from app.modules.matching.gateway import MatchingFailedError
from app.modules.matching.prompt import build_prefilter_prompt, build_prompt


def _result_line(custom_id: str, text: str | None, *, status_code: int = 200) -> str:
    body = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}] if text else [],
            },
        ]
    }
    return json.dumps(
        {
            "id": f"batch_req_{custom_id}",
            "custom_id": custom_id,
            "response": {"status_code": status_code, "body": body},
            "error": None,
        }
    )


def test_build_request_has_the_documented_batch_line_shape() -> None:
    request = batch_gateway.build_request(
        custom_id="abc",
        model="gpt-5-mini",
        prompt="hello",
        effort="low",
        verbosity="low",
    )
    assert request == {
        "custom_id": "abc",
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": "gpt-5-mini",
            "input": "hello",
            "reasoning": {"effort": "low"},
            "text": {"verbosity": "low"},
        },
    }


def test_parse_results_extracts_message_text_and_flags_errors() -> None:
    jsonl = "\n".join(
        [
            _result_line("ok", '{"ats_score": 80}'),
            _result_line("empty", None),
            _result_line("http-error", "x", status_code=500),
            json.dumps({"custom_id": "errored", "response": None, "error": {"x": 1}}),
            "",
            "not json",
        ]
    )

    results = batch_gateway.parse_results(jsonl)

    assert results == {
        "ok": '{"ats_score": 80}',
        "empty": None,
        "http-error": None,
        "errored": None,
    }


def test_parse_prefilter_score_accepts_plain_and_fenced_json_and_clamps() -> None:
    assert gateway.parse_prefilter_score('{"ats_score": 82}') == 82
    assert gateway.parse_prefilter_score('```json\n{"ats_score": "67"}\n```') == 67
    assert gateway.parse_prefilter_score('{"ats_score": 140}') == 100
    assert gateway.parse_prefilter_score('{"ats_score": -3}') == 0


@pytest.mark.parametrize("raw", ["n/a", '{"score": 80}', '{"ats_score": "high"}'])
def test_parse_prefilter_score_rejects_unusable_output(raw: str) -> None:
    with pytest.raises(MatchingFailedError):
        gateway.parse_prefilter_score(raw)


def test_prefilter_prompt_reuses_the_ats_rules_but_asks_for_the_score_only() -> None:
    full = build_prompt("MON CV", "MON OFFRE")
    short = build_prefilter_prompt("MON CV", "MON OFFRE")

    # Same ATS grid: the ETAPE 8 rules text is in both.
    assert "ETAPE 8" in short and "ETAPE 8" in full
    rules_end = full.index("FORMAT DE SORTIE")
    assert short.startswith(full[:rules_end])
    # ...but only the score is requested, and the full schema is not.
    assert '{"ats_score": 0}' in short
    assert "career_explanation" not in short
    assert "MON CV" in short and "MON OFFRE" in short
    assert len(short) < len(full)
