from __future__ import annotations

import uuid

from app.core.events import Event


class ProfileOnboardingCompleted(Event):
    """Fired the first time a candidate's profile reaches
    ProfileStatus.complete -- i.e. the end of the onboarding wizard, right
    after CvService.apply_preferences saves step 3 for a brand-new account
    (see its `is_first_completion` return value).

    Never fired again for that account afterwards: saving preferences a
    second time (from the Préférences settings page) always finds the
    profile already complete, and re-importing a CV only ever sets it back
    to draft (see CvService.import_cv) -- it never itself reaches
    "complete". So this is a true once-per-account signal, deliberately,
    to avoid a way for a candidate to force extra (LLM-costed) matching
    runs by re-uploading their CV.
    """

    profile_id: uuid.UUID


class CandidateProfileDeleting(Event):
    """Emitted by the cv module, awaited (failures re-raised), just BEFORE a
    candidate's profile row is removed because their account is being
    deleted -- the cue for modules holding data keyed by `profile_id`
    (matching's candidate_matches / CV optimisations) to purge it. The
    profile row is only deleted once every listener succeeded."""

    profile_id: uuid.UUID
    user_id: uuid.UUID
