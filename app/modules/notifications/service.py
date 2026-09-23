"""Notification preferences (read/update) and the daily brief itself.

Cross-module reads here go exclusively through each module's public gateway/
repository export (AuthGateway, CandidateProfileRepository, ProfileStatus,
CandidateMatchRepository, JobOfferRepository) -- never a deep import -- per
the architecture test and this module's declared `depends_on`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.core.models import utcnow
from app.modules.auth import AuthGateway, PublicUser
from app.modules.cv import CandidateProfileRepository, ProfileStatus
from app.modules.matching import CandidateMatchRepository
from app.modules.notifications.brief_item import BriefItem
from app.modules.notifications.channels.discord_channel import send_brief_discord
from app.modules.notifications.channels.email_channel import send_brief_email
from app.modules.notifications.channels.whatsapp_channel import send_brief_whatsapp
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)
from app.modules.notifications.repository import (
    NotificationBriefEntryRepository,
    NotificationPreferenceRepository,
)
from app.modules.offers import JobOfferRepository

logger = get_logger("notifications.worker")

_DISCORD_WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"


class NotificationPreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.preferences = NotificationPreferenceRepository(session)

    async def get_or_create(self, user_id: uuid.UUID) -> NotificationPreference:
        preference = await self.preferences.get_by_user(user_id)
        if preference is not None:
            return preference
        return await self.preferences.create(NotificationPreference(user_id=user_id))

    async def update(self, user_id: uuid.UUID, **fields: Any) -> NotificationPreference:
        preference = await self.get_or_create(user_id)
        data = {k: v for k, v in fields.items() if v is not None}

        discord_enabled = data.get("discord_enabled", preference.discord_enabled)
        discord_webhook_url = data.get(
            "discord_webhook_url", preference.discord_webhook_url
        )
        if discord_enabled and not discord_webhook_url:
            raise BadRequestError(
                "Une URL de webhook Discord est requise pour activer ce canal."
            )
        if discord_webhook_url and not discord_webhook_url.startswith(
            _DISCORD_WEBHOOK_PREFIX
        ):
            raise BadRequestError(
                f"L'URL du webhook Discord doit commencer par {_DISCORD_WEBHOOK_PREFIX}"
            )

        whatsapp_enabled = data.get("whatsapp_enabled", preference.whatsapp_enabled)
        whatsapp_phone_number = data.get(
            "whatsapp_phone_number", preference.whatsapp_phone_number
        )
        if whatsapp_enabled and not whatsapp_phone_number:
            raise BadRequestError(
                "Un numéro de téléphone est requis pour activer WhatsApp."
            )

        return await self.preferences.update(preference, data)


@dataclass
class BriefRunReport:
    users_considered: int = 0
    users_sent: int = 0
    users_skipped_no_channel: int = 0
    users_skipped_no_new_offers: int = 0
    items_sent: int = 0
    channel_failures: dict = field(default_factory=dict)


class DailyBriefService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.profiles = CandidateProfileRepository(session)
        self.preferences = NotificationPreferenceRepository(session)
        self.entries = NotificationBriefEntryRepository(session)
        self.matches = CandidateMatchRepository(session)
        self.offers = JobOfferRepository(session)
        self.auth = AuthGateway(session)

    async def send_daily_briefs(self) -> BriefRunReport:
        report = BriefRunReport()
        profiles = await self.profiles.list(
            filters={"status": ProfileStatus.complete.value}
        )
        for profile in profiles:
            report.users_considered += 1
            await self._send_for_profile(profile.id, profile.user_id, report)
        logger.info(
            "notifications_daily_brief_complete",
            considered=report.users_considered,
            sent=report.users_sent,
            skipped_no_channel=report.users_skipped_no_channel,
            skipped_no_new_offers=report.users_skipped_no_new_offers,
            items=report.items_sent,
        )
        return report

    async def _send_for_profile(
        self,
        candidate_profile_id: uuid.UUID,
        user_id: uuid.UUID,
        report: BriefRunReport,
    ) -> None:
        settings = get_settings()
        preference = await self.preferences.get_by_user(user_id)
        if preference is None or not (
            preference.email_enabled
            or preference.discord_enabled
            or preference.whatsapp_enabled
        ):
            report.users_skipped_no_channel += 1
            return

        already_sent = await self.entries.sent_match_ids(user_id)
        # Fetch a wider pool than NOTIFICATIONS_BRIEF_MAX_ITEMS so there's
        # still something left to fill the brief with once already-sent
        # matches are filtered out -- a plain top-N query would otherwise
        # shrink toward empty as more of a candidate's best offers get used
        # up day over day.
        candidates = await self.matches.list_top_for_profile(
            candidate_profile_id, limit=settings.NOTIFICATIONS_BRIEF_MAX_ITEMS * 4
        )
        new_matches = [m for m in candidates if m.id not in already_sent][
            : settings.NOTIFICATIONS_BRIEF_MAX_ITEMS
        ]
        if not new_matches:
            report.users_skipped_no_new_offers += 1
            return

        items: list[BriefItem] = []
        for match in new_matches:
            offer = await self.offers.get(match.job_offer_id)
            if offer is None:
                continue
            items.append(
                BriefItem(
                    match_id=str(match.id),
                    title=offer.title,
                    company_name=offer.company_name or match.company_name,
                    # Our own opportunity page, not the raw external
                    # job-board URL -- that's where the candidate sees the
                    # match analysis and can use "Adapter mon CV pour
                    # cette offre", not just a bare job posting.
                    url=f"{settings.APP_URL.rstrip('/')}/opportunity/{match.id}",
                    career_score=match.career_score,
                )
            )
        if not items:
            report.users_skipped_no_new_offers += 1
            return

        user = await self.auth.get_user(user_id)
        if user is None:
            return

        channels_sent = await self._dispatch(user, preference, items, settings)
        for failure in {"email", "discord", "whatsapp"} - set(channels_sent):
            enabled = getattr(preference, f"{failure}_enabled")
            if enabled:
                report.channel_failures[failure] = (
                    report.channel_failures.get(failure, 0) + 1
                )

        now = utcnow()
        for item in items:
            await self.entries.create(
                NotificationBriefEntry(
                    user_id=user_id,
                    candidate_match_id=uuid.UUID(item.match_id),
                    sent_at=now,
                    channels_sent=channels_sent,
                )
            )
        await self.session.commit()
        report.users_sent += 1
        report.items_sent += len(items)

    async def send_test_brief(self, user_id: uuid.UUID) -> list[str]:
        """Manual "send me one now" -- backs a "Tester l'envoi" action on the
        notification-settings screen so a candidate can see a real email/
        Discord/WhatsApp message land before trusting the scheduled 18:30
        job to ever run.

        Deliberately independent of the real daily job's state in both
        directions: an offer already recorded as sent by send_daily_briefs
        is still eligible here (dedup would otherwise make testing
        impossible once the job has run once), and nothing sent here is
        recorded in NotificationBriefEntry (a test-send must never suppress
        that same offer from the real brief later).
        """
        settings = get_settings()
        profile = await self.profiles.get_by_user(user_id)
        if profile is None or profile.status != ProfileStatus.complete.value:
            raise BadRequestError(
                "Complétez votre profil candidat avant de tester l'envoi des "
                "notifications."
            )

        preference = await self.preferences.get_by_user(user_id)
        if preference is None or not (
            preference.email_enabled
            or preference.discord_enabled
            or preference.whatsapp_enabled
        ):
            raise BadRequestError(
                "Activez au moins un canal de notification avant de tester l'envoi."
            )

        candidates = await self.matches.list_top_for_profile(
            profile.id, limit=settings.NOTIFICATIONS_BRIEF_MAX_ITEMS
        )
        items: list[BriefItem] = []
        for match in candidates:
            offer = await self.offers.get(match.job_offer_id)
            if offer is None:
                continue
            items.append(
                BriefItem(
                    match_id=str(match.id),
                    title=offer.title,
                    company_name=offer.company_name or match.company_name,
                    url=f"{settings.APP_URL.rstrip('/')}/opportunity/{match.id}",
                    career_score=match.career_score,
                )
            )
        if not items:
            raise BadRequestError(
                "Aucune offre correspondante pour l'instant -- réessayez une "
                "fois que des correspondances auront été calculées."
            )

        user = await self.auth.get_user(user_id)
        if user is None:
            raise NotFoundError("Utilisateur introuvable.")

        channels_sent = await self._dispatch(user, preference, items, settings)
        # send_brief_email only enqueues an EmailMessage row in this
        # session's transaction (see channels/email_channel.py) -- without
        # this commit a test-send would silently never actually reach the
        # mailer's outbox.
        await self.session.commit()
        return channels_sent

    async def _dispatch(
        self,
        user: PublicUser,
        preference: NotificationPreference,
        items: list[BriefItem],
        settings: Settings,
    ) -> list[str]:
        channels_sent: list[str] = []

        if preference.email_enabled:
            await send_brief_email(
                self.session,
                to_email=user.email,
                first_name=user.first_name,
                items=items,
            )
            channels_sent.append("email")

        if (
            preference.discord_enabled
            and preference.discord_webhook_url
            and await send_brief_discord(
                webhook_url=preference.discord_webhook_url, items=items
            )
        ):
            channels_sent.append("discord")

        if (
            preference.whatsapp_enabled
            and preference.whatsapp_phone_number
            and await send_brief_whatsapp(
                phone_number=preference.whatsapp_phone_number,
                first_name=user.first_name,
                items=items,
                dashboard_url=f"{settings.APP_URL.rstrip('/')}/dashboard",
            )
        ):
            channels_sent.append("whatsapp")

        return channels_sent
