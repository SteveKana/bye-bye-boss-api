"""Gives back to the profiles the experience bullets that were lost at CV
import (the import used to summarize each experience into one short
paragraph; it now copies every bullet -- see cv/gateway.py).

How it works, per profile: the CV text already saved (raw_text) is read again
by the model, and ONLY the `description` of each experience is replaced -- and
only when the new one is clearly fuller (at least 1.5 x longer) and the
profile still has the same experiences in the same order (same company).
Anything else (name, skills, preferences, status...) is never touched.

DRY RUN BY DEFAULT: nothing is written unless you add --apply.
CALLS THE AI once per profile (an import-sized call, a few cents in total for
a few dozen profiles -- estimate, not measured).

Usage (from the repo root, inside the venv, with .env loaded):
    python -m scripts.cv_restore_bullets                      # report only
    python -m scripts.cv_restore_bullets --email a@b.fr       # one profile
    python -m scripts.cv_restore_bullets --apply              # write
"""

from __future__ import annotations

import argparse
import asyncio

import sqlalchemy as sa

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User  # noqa: I001  (script only)
from app.modules.cv import CandidateProfile, gateway

MIN_GROWTH = 1.5


def _same_company(old: dict, new: dict) -> bool:
    def norm(value: str) -> str:
        return " ".join((value or "").casefold().split())

    a, b = norm(old.get("company", "")), norm(new.get("company", ""))
    return bool(a) and bool(b) and (a in b or b in a)


def merge_descriptions(old_experiences: list, new_experiences: list) -> list | None:
    """The old experiences with fuller descriptions, or None when nothing
    should change (different structure, or no gain)."""
    if len(old_experiences) != len(new_experiences):
        return None
    if not all(
        _same_company(o, n)
        for o, n in zip(old_experiences, new_experiences, strict=True)
    ):
        return None
    merged = []
    changed = False
    for old, new in zip(old_experiences, new_experiences, strict=True):
        old_text = old.get("description") or ""
        new_text = new.get("description") or ""
        if len(new_text) >= MIN_GROWTH * max(len(old_text), 1):
            merged.append({**old, "description": new_text})
            changed = True
        else:
            merged.append(old)
    return merged if changed else None


async def main(apply: bool, email: str | None) -> None:
    if not get_settings().OPENAI_API_KEY:
        print("OPENAI_API_KEY est vide : rien à faire.")
        return
    async with AsyncSessionLocal() as session:
        query = sa.select(CandidateProfile, User.email).join(
            User,
            User.id == CandidateProfile.user_id,  # type: ignore[arg-type]
        )
        query = query.where(CandidateProfile.raw_text.is_not(None))  # type: ignore[union-attr]
        if email:
            query = query.where(User.email == email)
        rows = (await session.execute(query)).all()
        done = skipped = failed = 0
        for profile, user_email in rows:
            try:
                extracted = await gateway.structure_cv_text(profile.raw_text)
            except Exception as exc:  # noqa: BLE001 - report and go on
                failed += 1
                print(f"ÉCHEC  {user_email} : {exc}")
                continue
            merged = merge_descriptions(
                profile.experiences or [], extracted.get("experiences") or []
            )
            if merged is None:
                skipped += 1
                print(f"inchangé  {user_email}")
                continue
            before = sum(len(e.get("description") or "") for e in profile.experiences)
            after = sum(len(e.get("description") or "") for e in merged)
            print(f"à corriger  {user_email} : {before} -> {after} caractères")
            done += 1
            if apply:
                profile.experiences = merged  # reassigned: change is detected
                session.add(profile)
                await session.commit()
        mode = "écrits" if apply else "à écrire (relance avec --apply)"
        print(
            f"\nTerminé : {done} profils {mode}, "
            f"{skipped} inchangés, {failed} en échec."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--email")
    args = parser.parse_args()
    asyncio.run(main(args.apply, args.email))
