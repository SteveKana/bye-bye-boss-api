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
    # The prompt (see prompt.py, ETAPE 5/8/9) defines soft_blocker as a real
    # classification level -- it's just supposed to never appear *alone* in
    # blocking_requirements (ETAPE 10: "jamais un soft seul"). Observed in
    # production (2026-09-21) doing exactly that anyway (one soft_blocker
    # entry, on its own, for a Product Owner Low-Code offer) -- validation
    # rejected the whole analysis over a value the prompt itself defines.
    # Accepting it costs nothing (nothing downstream branches on the level's
    # exact value) and matches this schema's own general policy of not
    # dropping an entire match over the model not perfectly following an
    # instruction about a value it's otherwise allowed to produce.
    soft_blocker = "soft_blocker"


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

    @field_validator("cv_skills", mode="before")
    @classmethod
    def accept_categorized_skills(cls, value):
        # The prompt asks for a flat list of concept strings (ETAPE 1), but
        # the model sometimes mirrors job_skills' richer shape instead and
        # returns {"concept": ..., "group": ...} objects here too -- observed
        # in production (2026-09-21), where it made every cv_skills entry
        # fail validation and silently dropped the whole analysis (see
        # matching.service, which just skips a pair on a schema error).
        # There's no `category`/`group` field on this side to keep the extra
        # detail in, so only the label is kept, same as job_skills' own
        # accept_plain_string does in the other direction.
        if not isinstance(value, list):
            return value
        return [
            (item.get("concept") or item.get("skill") or item.get("name") or "")
            if isinstance(item, dict)
            else item
            for item in value
        ]

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
