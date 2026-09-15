from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.cv import extraction, gateway
from app.modules.cv.models import AvailabilityStatus, CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.cv.schemas import PreferencesUpdate

# Fields the LLM is asked to fill on the flat (non-list) part of the profile.
# Availability is deliberately excluded: it's a live preference (immediate /
# a date / serving notice / unavailable), not something reliably extractable
# as free text, and is defaulted below instead.
_FLAT_FIELDS = ("first_name", "last_name", "email", "location", "total_experience")
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
            # availability_status defaults to "immediate" on the column
            # itself, so a fresh profile gets a sensible default without
            # guessing from unreliable free-text extraction. headline has
            # no column default -- seed it here, once, from the most recent
            # experience (CVs list jobs most-recent-first).
            headline = None
            experiences = extracted.get("experiences") or []
            if experiences:
                headline = experiences[0].get("title") or None
            profile = await self.profiles.create(
                CandidateProfile(user_id=user_id, headline=headline, **values)
            )
        else:
            # Re-importing a CV shouldn't silently reset a preference the
            # user set themselves (availability isn't really a CV fact).
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
        # Only one of availability_date / notice_period_months is ever
        # meaningful, matching whichever status was just set — clear the
        # other explicitly, since the generic filter above drops None values
        # and would otherwise let a stale one linger.
        status = updates.get("availability_status")
        if status == AvailabilityStatus.date.value:
            updates["notice_period_months"] = None
        elif status == AvailabilityStatus.notice.value:
            updates["availability_date"] = None
        elif status in (
            AvailabilityStatus.immediate.value,
            AvailabilityStatus.unavailable.value,
        ):
            updates["availability_date"] = None
            updates["notice_period_months"] = None
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
