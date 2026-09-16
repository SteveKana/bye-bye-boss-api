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


def _derive_headline(extracted: dict) -> str | None:
    """Most recent experience's title -- CVs list jobs most-recent-first.
    There's no dedicated headline field in a CV, so this is a best-effort
    seed rather than something re-extracted on every import."""
    experiences = extracted.get("experiences") or []
    return (experiences[0].get("title") or None) if experiences else None


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
        raw_text, kind = extraction.extract_text(
            filename=filename, content_type=content_type, data=data
        )
        extracted = await gateway.structure_cv_text(raw_text)

        profile = await self.profiles.get_by_user(user_id)
        values = {field: extracted.get(field) or None for field in _FLAT_FIELDS}
        for field in _LIST_FIELDS:
            values[field] = extracted.get(field) or []
        values["raw_text"] = raw_text
        values["status"] = ProfileStatus.draft.value
        values["cv_filename"] = filename
        values["cv_content_type"] = content_type

        if profile is None:
            # availability_status defaults to "immediate" on the column
            # itself, so a fresh profile gets a sensible default without
            # guessing from unreliable free-text extraction.
            profile = await self.profiles.create(
                CandidateProfile(
                    user_id=user_id, headline=_derive_headline(extracted), **values
                )
            )
        else:
            # Re-importing a CV shouldn't silently reset a preference the
            # user set themselves (availability isn't really a CV fact).
            # headline is the one exception with a middle ground: it's
            # worth backfilling if it was never set (e.g. a profile created
            # before this field existed), but never overwritten once the
            # user has their own value in there.
            if not profile.headline:
                values["headline"] = _derive_headline(extracted)
            profile = await self.profiles.update(profile, values)

        # Saved after the LLM call succeeds, so a failed import never
        # leaves an orphaned file with no matching profile data.
        extraction.save_original_file(user_id=user_id, kind=kind, data=data)

        await self.session.commit()
        return profile

    async def get_for_user(self, user_id: uuid.UUID) -> CandidateProfile:
        profile = await self.profiles.get_by_user(user_id)
        if profile is None:
            raise NotFoundError("Aucun profil trouvé. Importez d'abord un CV.")
        return profile

    def get_cv_file(self, profile: CandidateProfile):
        """Returns (path, filename, content_type) for downloading the
        original uploaded file back."""
        kind = (
            "docx" if (profile.cv_filename or "").lower().endswith(".docx") else "pdf"
        )
        path = extraction.stored_file_path(profile.user_id, kind)
        filename = profile.cv_filename or path.name
        content_type = profile.cv_content_type or (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if kind == "docx"
            else "application/pdf"
        )
        return path, filename, content_type

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
