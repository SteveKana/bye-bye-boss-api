"""CV-owned event listeners."""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.events import event_bus, on
from app.core.logging import get_logger
from app.modules.auth import UserDeletionRequested
from app.modules.cv.events import CandidateProfileDeleting
from app.modules.cv.extraction import SUPPORTED_CONTENT_TYPES, _storage_path
from app.modules.cv.repository import CandidateProfileRepository

logger = get_logger("cv.listeners")


@on(UserDeletionRequested)
async def purge_candidate_profile(event: UserDeletionRequested) -> None:
    """Account deletion: first let the modules holding data keyed by this
    profile purge it (CandidateProfileDeleting, awaited, failures raised),
    then remove the uploaded CV file and the profile row itself."""
    async with AsyncSessionLocal() as session:
        repo = CandidateProfileRepository(session)
        profile = await repo.get_by_user(event.user_id)
        if profile is not None:
            await event_bus.emit(
                CandidateProfileDeleting(profile_id=profile.id, user_id=event.user_id),
                raise_on_error=True,
            )
            # Hard delete -- see AuthService.delete_account.
            await session.delete(profile)
            await session.commit()

    for kind in set(SUPPORTED_CONTENT_TYPES.values()):
        _storage_path(event.user_id, kind).unlink(missing_ok=True)
    logger.info("cv_data_purged", user_id=str(event.user_id))
