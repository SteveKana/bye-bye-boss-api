"""Typed shape of the matching LLM's JSON output.

Ported from the standalone `matchcareer_engine` prototype (validated against
real model output during prototyping -- the acceptance validators below
exist because the model doesn't always return the exact shape asked for).
Nothing downstream ever sees unvalidated LLM output: `gateway.py` parses the
model's JSON straight into `LLMAnalysis`.
"""

from __future__ import annotations

import enum

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


# enum.StrEnum, not `str, Enum` -- same convention as app/modules/cv/models.py.
class Importance(enum.StrEnum):
    required = "required"
    preferred = "preferred"
    nice_to_have = "nice_to_have"


class BlockerLevel(enum.StrEnum):
    hard_blocker = "hard_blocker"
    medium_blocker = "medium_blocker"


class MatchResultStatus(enum.StrEnum):
    matched = "matched"
    partially_matched = "partially_matched"
    missing = "missing"


class JobSkill(BaseModel):
    skill: str
    category: str = ""
    importance: Importance = Importance.required

    @model_validator(mode="before")
    @classmethod
    def accept_plain_string(cls, data):
        # The model sometimes returns job_skills / cv_skills as bare strings
        # (e.g. "user_story") instead of {skill, category, importance} objects.
        if isinstance(data, str):
            return {"skill": data}
        return data


class BlockingRequirement(BaseModel):
    skill: str
    level: BlockerLevel = BlockerLevel.medium_blocker
    reason: str = ""
    # The model doesn't consistently return a bare number here -- observed in
    # production returning e.g. "5y" (a magnitude with a unit) instead of 5.
    # Accepted as either shape rather than coerced/parsed: nothing downstream
    # does arithmetic on these yet, and guessing at unit conversion (years?
    # percent? points?) would be worse than just keeping what the model said.
    gap_value: float | str | None = None
    gap_percent: float | str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_plain_string(cls, data):
        if isinstance(data, str):
            return {"skill": data}
        return data


class SkillMatch(BaseModel):
    model_config = {"populate_by_name": True}

    skill: str
    importance: Importance = Importance.required
    result: MatchResultStatus = Field(
        default=MatchResultStatus.missing,
        validation_alias=AliasChoices("result", "match", "status", "match_result"),
    )
    confidence: int = Field(default=0, ge=0, le=100)


class AtsGap(BaseModel):
    model_config = {"populate_by_name": True}

    skill: str
    missing_in_cv: bool = Field(
        default=True,
        validation_alias=AliasChoices("missing_in_cv", "missing", "is_missing"),
    )
    why_it_matters: str = Field(
        default="",
        validation_alias=AliasChoices("why_it_matters", "why", "reason", "gap_type"),
    )
    cv_fix_example: str = Field(
        default="",
        validation_alias=AliasChoices(
            "cv_fix_example", "fix", "example", "cv_fix", "suggestion"
        ),
    )


class Action(BaseModel):
    model_config = {"populate_by_name": True}

    action: str
    details: str = ""

    @model_validator(mode="before")
    @classmethod
    def accept_plain_string(cls, data):
        # The model sometimes returns actions as plain strings instead of
        # {action, details} objects. Wrap a bare string into the object shape.
        if isinstance(data, str):
            return {"action": data, "details": ""}
        return data


class LLMAnalysis(BaseModel):
    company_name: str = ""

    career_score: int = Field(ge=0, le=100)
    ats_score: int = Field(ge=0, le=100)
    ats_potential: int = Field(ge=0, le=100)

    cv_skills: list[str] = Field(default_factory=list)
    job_skills: list[JobSkill] = Field(default_factory=list)

    mandatory_requirements: list[JobSkill] = Field(default_factory=list)
    preferred_requirements: list[JobSkill] = Field(default_factory=list)

    blocking_requirements: list[BlockingRequirement] = Field(default_factory=list)
    blocking_message: str = ""

    matches: list[SkillMatch] = Field(default_factory=list)
    ats_gaps: list[AtsGap] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)

    career_explanation: list[str] = Field(default_factory=list)
    ats_explanation: list[str] = Field(default_factory=list)

    @field_validator("ats_potential")
    @classmethod
    def potential_not_below_score(cls, v: int, info) -> int:
        # ATS Potential is "best achievable by rewording" -- it can never be
        # lower than the current ATS score. If the model violates this, clamp.
        ats = info.data.get("ats_score")
        if ats is not None and v < ats:
            return ats
        return v
