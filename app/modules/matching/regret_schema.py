"""Typed shape of the regret-scoring LLM's JSON output -- same validate-then-
trust discipline as llm_schema.LLMAnalysis (see that file's docstring)."""

from __future__ import annotations

import enum

from pydantic import BaseModel, field_validator


class RegretAvailability(enum.StrEnum):
    available = "available"
    insufficient = "insufficient"


class RegretAnalysis(BaseModel):
    availability: RegretAvailability
    score: int | None = None
    reasons: list[str] = []

    @field_validator("score")
    @classmethod
    def _clamp_score(cls, v: int | None) -> int | None:
        if v is None:
            return None
        return max(0, min(100, v))

    @field_validator("reasons")
    @classmethod
    def _cap_reasons(cls, v: list[str]) -> list[str]:
        return v[:4]
