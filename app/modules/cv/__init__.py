"""CV module — public surface.

Parses an uploaded CV (PDF/DOCX) into a structured candidate profile via an
LLM, and stores the user's verification edits and onboarding preferences.
Depends on `auth` for the current user.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register the model so Alembic sees it.
from app.modules.cv import models as models  # noqa: F401
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.cv.routes.v1 import cv_routes
from app.modules.cv.schemas import CandidateProfileRead
from app.modules.cv.service import CvService

_router = APIRouter()
_router.include_router(cv_routes.router)

module = Module(
    name="cv",
    router=_router,
    order=40,
    depends_on=["auth"],
    tags=["cv"],
)

__all__ = [
    "module",
    "CandidateProfileRead",
    "CandidateProfile",
    "ProfileStatus",
    "CandidateProfileRepository",
    "CvService",
]
