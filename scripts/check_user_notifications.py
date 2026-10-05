"""Read-only check: why does this user not get notification emails?

Run ON THE SERVER, from the repo root, with the venv active:

    cd /var/www/bye-bye-boss-api/<site>/repo
    source ../venv/bin/activate
    python -m scripts.check_user_notifications someone@gmail.com

It only reads the database (nothing is changed or sent) and prints, in
plain words, the account state, the notification settings, the offers
waiting to be sent, and the last emails queued for that address.
"""

from __future__ import annotations

import asyncio
import sys

from sqlmodel import col, desc, select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile
from app.modules.mailer.models import EmailMessage
from app.modules.matching.models import CandidateMatch, MatchStatus
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)


def say(ok: bool | None, text: str) -> None:
    mark = {True: "OK ", False: "!! ", None: "-- "}[ok]
    print(f"{mark} {text}")


async def main(email: str) -> None:
    async with AsyncSessionLocal() as s:
        user = (
            await s.exec(select(User).where(col(User.email) == email.strip().lower()))
        ).first()
        if user is None:
            user = (await s.exec(select(User).where(col(User.email) == email))).first()
        if user is None:
            say(False, f"Aucun compte avec l'adresse {email}")
            return
        print(f"\nCompte : {user.email} (créé le {user.created_at:%d/%m/%Y %H:%M})")
        say(user.is_active, f"Compte actif : {user.is_active}")
        say(user.is_verified, f"Email vérifié : {user.is_verified}")
        say(None, f"Inscrit via Google : {bool(user.google_id)}")

        pref = (
            await s.exec(
                select(NotificationPreference).where(
                    col(NotificationPreference.user_id) == user.id
                )
            )
        ).first()
        if pref is None:
            say(
                False,
                "AUCUNE ligne de préférences de notification : le compte n'est "
                "dans aucun envoi (jamais ouvert /settings, créé avant le 4 oct.)",
            )
        else:
            say(pref.email_enabled, f"Alertes email activées : {pref.email_enabled}")
            say(
                None,
                f"Discord : {pref.discord_enabled} / "
                f"WhatsApp : {pref.whatsapp_enabled}",
            )

        profile = (
            await s.exec(
                select(CandidateProfile).where(col(CandidateProfile.user_id) == user.id)
            )
        ).first()
        if profile is None:
            say(False, "Aucun profil (CV jamais importé)")
            return
        say(profile.status == "complete", f"Statut du profil : {profile.status}")

        matches = (
            await s.exec(
                select(CandidateMatch).where(
                    col(CandidateMatch.candidate_profile_id) == profile.id
                )
            )
        ).all()
        scored = [m for m in matches if m.status == MatchStatus.scored.value]
        sent_ids = {
            e.candidate_match_id
            for e in (
                await s.exec(
                    select(NotificationBriefEntry).where(
                        col(NotificationBriefEntry.user_id) == user.id
                    )
                )
            ).all()
        }
        waiting = [m for m in scored if m.id not in sent_ids]
        say(None, f"Offres au total : {len(matches)} dont analysées : {len(scored)}")
        say(
            len(waiting) > 0,
            f"Offres analysées jamais envoyées par alerte : {len(waiting)}",
        )
        say(None, f"Offres déjà incluses dans une alerte : {len(sent_ids)}")

        mails = (
            await s.exec(
                select(EmailMessage)
                .where(col(EmailMessage.to_email) == user.email)
                .order_by(desc(col(EmailMessage.created_at)))
                .limit(8)
            )
        ).all()
        print("\nDerniers emails mis en file pour cette adresse :")
        if not mails:
            say(False, "Aucun email jamais mis en file")
        for m in mails:
            line = (
                f"{m.created_at:%d/%m %H:%M} | {m.status} | "
                f"essais {m.attempts} | {m.subject[:60]}"
            )
            say(m.status == "sent", line)
            if m.last_error:
                print(f"      erreur : {m.last_error[:200]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.check_user_notifications <email>")
    asyncio.run(main(sys.argv[1]))
