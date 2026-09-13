from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.config import get_settings
from app.core.dependencies import DBSession
from app.core.exceptions import BadRequestError
from app.core.ratelimit import RateLimiter
from app.modules.auth import CurrentUser
from app.modules.cv.schemas import (
    CandidateProfileRead,
    CandidateProfileUpdate,
    PreferencesUpdate,
)
from app.modules.cv.service import CvService

router = APIRouter(prefix="/cv", tags=["cv"])

# CV parsing calls an LLM — throttle harder than a plain CRUD endpoint.
upload_limit = RateLimiter(times=10, seconds=3600, scope="cv:upload")


@router.post(
    "/upload",
    response_model=CandidateProfileRead,
    dependencies=[Depends(upload_limit)],
)
async def upload_cv(
    session: DBSession, user: CurrentUser, file: UploadFile = File(...)
) -> CandidateProfileRead:
    settings = get_settings()
    data = await file.read()
    max_bytes = settings.CV_MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise BadRequestError(
            f"Le fichier dépasse la taille maximale de {settings.CV_MAX_UPLOAD_MB} Mo.",
            code="file_too_large",
        )
    profile = await CvService(session).import_cv(
        user_id=user.id,
        filename=file.filename or "cv",
        content_type=file.content_type,
        data=data,
    )
    return CandidateProfileRead.model_validate(profile)


@router.get("/profile", response_model=CandidateProfileRead)
async def get_profile(session: DBSession, user: CurrentUser) -> CandidateProfileRead:
    profile = await CvService(session).get_for_user(user.id)
    return CandidateProfileRead.model_validate(profile)


@router.put("/profile", response_model=CandidateProfileRead)
async def update_profile(
    data: CandidateProfileUpdate, session: DBSession, user: CurrentUser
) -> CandidateProfileRead:
    payload = data.model_dump(exclude_unset=True)
    # Nested list items come through as pydantic models via validation;
    # normalize to plain dicts before they hit the JSON column.
    for key in ("experiences", "formations", "languages", "certifications"):
        if payload.get(key) is not None:
            payload[key] = [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in payload[key]
            ]
    profile = await CvService(session).apply_verification(user_id=user.id, data=payload)
    return CandidateProfileRead.model_validate(profile)


@router.put("/profile/preferences", response_model=CandidateProfileRead)
async def update_preferences(
    data: PreferencesUpdate, session: DBSession, user: CurrentUser
) -> CandidateProfileRead:
    profile = await CvService(session).apply_preferences(user_id=user.id, data=data)
    return CandidateProfileRead.model_validate(profile)
