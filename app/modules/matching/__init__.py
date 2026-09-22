"""Matching module — public surface.

Scores each candidate's CV against relevant job offers using an LLM (career
fit, ATS compatibility, blocking requirements, concrete CV improvement
actions) -- ported from the standalone `matchcareer_engine` prototype, fixed
to run safely inside this async backend (see gateway.py's docstring).

Runs entirely in the background (see jobs.py): a cheap keyword-overlap
pre-filter (shortlist.py) narrows the offer pool per candidate before any
LLM call happens, and results are stored in `candidate_matches` so the
dashboard only ever reads, never waits on the LLM. Depends on `cv` (the
candidate's extracted CV text) and `offers` (the pool to match against).

The Regret Index (employee-sentiment risk score) is deliberately NOT
computed here. MatchCareer's own review-data sourcing options were reviewed
and none were legitimate enough to ship yet (no scraping, and the API
options considered either require negotiated commercial terms or lack
structured ratings) -- every CandidateMatch instead reports
regret_availability="unavailable" until a real source is wired in.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register the models (Alembic) and the scheduled job.
from app.modules.matching import (
    cv_optimization_models as cv_optimization_models,  # noqa: F401
)
from app.modules.matching import jobs as jobs  # noqa: F401
from app.modules.matching import models as models  # noqa: F401
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.routes.v1 import cv_optimization_routes, matching_routes
from app.modules.matching.schemas import CandidateMatchRead, CVOptimizationRead
from app.modules.matching.service import MatchingService

_router = APIRouter()
_router.include_router(matching_routes.router)
_router.include_router(cv_optimization_routes.router)

module = Module(
    name="matching",
    router=_router,
    order=50,
    depends_on=["auth", "cv", "offers"],
    tags=["matching"],
)

__all__ = [
    "module",
    "CandidateMatchRepository",
    "CandidateMatchRead",
    "CVOptimizationRead",
    "MatchingService",
]
