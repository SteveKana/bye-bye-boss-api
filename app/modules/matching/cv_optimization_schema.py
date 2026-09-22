"""Typed shape of the CV-optimization LLM's JSON output.

Same tolerant-parsing philosophy as llm_schema.py (the model doesn't always
return the exact shape asked for) -- this schema is smaller and newer, so it
hasn't accumulated the same list of observed-in-production quirks yet, but
the `accept_plain_string` pattern is copied preemptively for the same
reasons it was needed there.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field, model_validator


class BulletStatus(enum.StrEnum):
    unchanged = "unchanged"
    modified = "modified"
    added = "added"


class CVOptimizationBullet(BaseModel):
    text: str
    status: BulletStatus = BulletStatus.unchanged
    # Only meaningful when status == "modified" -- the original wording, so
    # the frontend's hover card can show what changed. None for
    # "unchanged"/"added" bullets, which have nothing to diff against.
    original_text: str | None = None
    # Required (by the prompt) for "modified"/"added" bullets: why this
    # specific change helps for this specific offer -- never a generic
    # "improves your CV" filler. Left blank for "unchanged".
    why: str = ""

    @model_validator(mode="before")
    @classmethod
    def accept_plain_string(cls, data):
        # Mirrors llm_schema.Action's accept_plain_string -- the model may
        # return a bare string instead of the full object for an
        # unremarkable "unchanged" bullet.
        if isinstance(data, str):
            return {"text": data}
        return data


class CVOptimizationExperience(BaseModel):
    # Deliberately no validation tying these back to the candidate's real
    # experiences -- see cv_optimization_service.py, which pairs output
    # experiences with input experiences by position (and falls back to the
    # original title/company/period if the model drifts) rather than
    # trusting the model to echo them back verbatim.
    title: str = ""
    company: str = ""
    period: str = ""
    bullets: list[CVOptimizationBullet] = Field(default_factory=list)


class CVOptimizationSkill(BaseModel):
    skill: str
    # True if this skill wasn't in the candidate's own `skills` list -- see
    # the prompt's "raisonnablement déductible" rule: only ever set when the
    # skill is clearly implied by something the CV already says, never
    # because the offer merely asks for it.
    added: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_plain_string(cls, data):
        if isinstance(data, str):
            return {"skill": data}
        return data


class CVOptimizationResult(BaseModel):
    headline: str = ""
    summary: str = ""
    summary_why: str = ""
    experiences: list[CVOptimizationExperience] = Field(default_factory=list)
    skills: list[CVOptimizationSkill] = Field(default_factory=list)
    # Backs the mockup's "Conseil" card -- one short, concrete piece of
    # advice about the changes as a whole, not a repeat of summary_why.
    advice: str = ""
