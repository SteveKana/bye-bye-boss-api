from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.cv import extraction, gateway
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.cv.schemas import PreferencesUpdate

# Fields the LLM is asked to fill on the flat (non-list) part of the profile.
_FLAT_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "location",
    "availability",
    "total_experience",
)
_LIST_FIELDS = ("experiences", "skills", "formations", "languages", "certifications")


class CvService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.profiles = CandidateProfileRepository(session)

    async def import_cv(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> CandidateProfile:
        """Extract text from the file, structure it via the LLM, and persist
        it as a draft profile (creating or replacing the previous draft)."""
        raw_text = extraction.extract_text(
            filename=filename, content_type=content_type, data=data
        )
        extracted = await gateway.structure_cv_text(raw_text)

        profile = await self.profiles.get_by_user(user_id)
        values = {field: extracted.get(field) or None for field in _FLAT_FIELDS}
        for field in _LIST_FIELDS:
            values[field] = extracted.get(field) or []
        values["raw_text"] = raw_text
        values["status"] = ProfileStatus.draft.value

        if profile is None:
            profile = await self.profiles.create(
                CandidateProfile(user_id=user_id, **values)
            )
        else:
            profile = await self.profiles.update(profile, values)

        await self.session.commit()
        return profile

    async def get_for_user(self, user_id: uuid.UUID) -> CandidateProfile:
        profile = await self.profiles.get_by_user(user_id)
        if profile is None:
            raise NotFoundError("Aucun profil trouvé. Importez d'abord un CV.")
        return profile

    async def apply_verification(
        self, *, user_id: uuid.UUID, data: dict
    ) -> CandidateProfile:
        profile = await self.get_for_user(user_id)
        updates = {k: v for k, v in data.items() if v is not None}
        profile = await self.profiles.update(profile, updates)
        await self.session.commit()
        return profile

    async def apply_preferences(
        self, *, user_id: uuid.UUID, data: PreferencesUpdate
    ) -> CandidateProfile:
        profile = await self.get_for_user(user_id)
        profile = await self.profiles.update(
            profile,
            {
                "contract_types": data.contract_types,
                "remote_preferences": data.remote_preferences,
                "mobility": data.mobility,
                "salary_target": data.salary_target,
                "status": ProfileStatus.complete.value,
            },
        )
        await self.session.commit()
        return profile
