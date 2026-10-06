"""Everything the dashboard shows, computed from the live tables."""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core import scheduler as scheduler_core
from app.core.config import get_settings
from app.modules.monitoring import queries as q
from app.modules.monitoring.common import (
    as_utc,
    last_days,
    now_utc,
    paris_day,
    start_of_today_paris,
)
from app.modules.monitoring.incidents import OPEN, to_item
from app.modules.monitoring.models import AiUsage, Incident, PageView
from app.modules.monitoring.schemas import (
    AccountToFollow,
    AlertChannel,
    BehaviorResponse,
    BehaviorTiles,
    CostDay,
    CvOptimizationStats,
    CvOptimizedOffer,
    DayCount,
    FunnelStep,
    HealthItem,
    JobItem,
    OffersBySource,
    OverviewResponse,
    OverviewTiles,
    SignupMode,
    TopOffer,
    TopPage,
    UserRow,
)

JOB_LABELS = {
    "matching_sync": "Matching quotidien (8h)",
    "matching_submit_batches": "Envoi des lots à OpenAI",
    "matching_poll_batches": "Réception des résultats",
    "matching_skill_labels": "Traduction des compétences",
    "mail_queue": "File d'e-mails",
    "offers_ingestion": "Import des offres",
}
PAGE_LABELS = {
    "/opportunites": "Opportunités",
    "/dashboard": "Tableau de bord",
    "/opportunites/:id": "Détail d'une offre",
    "/settings": "Réglages",
    "/candidatures": "Candidatures",
    "/profile": "Profil",
    "/profil": "Profil",
}
APPLIED_SQL = q.matches.c.application_status != "not_applied"


async def _all(session: AsyncSession, stmt: sa.Select) -> list:
    return list((await session.execute(stmt)).all())


def _real_users():
    return sa.and_(q.users.c.is_active.is_(True))


# ---------------------------------------------------------------- AI cost --
def cost_eur(model: str, input_tokens: int, output_tokens: int) -> float:
    settings = get_settings()
    prices = settings.MONITORING_AI_PRICES_USD_PER_M.get(model)
    if not prices or len(prices) != 2:
        return 0.0
    usd = (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
    return usd * settings.MONITORING_BATCH_DISCOUNT * settings.MONITORING_USD_TO_EUR


async def _usage_rows(session: AsyncSession, since: datetime) -> list[AiUsage]:
    rows = (
        await session.exec(select(AiUsage).where(AiUsage.created_at >= since))
    ).all()
    return list(rows)


# ---------------------------------------------------------------- overview --
async def overview(session: AsyncSession, days: int) -> OverviewResponse:
    settings = get_settings()
    now = now_utc()
    today = start_of_today_paris()
    since = now - timedelta(days=days)

    users = await _all(
        session, sa.select(q.users.c.id, q.users.c.created_at).where(_real_users())
    )
    signup_days = dict.fromkeys(last_days(30), 0)
    for _, created in users:
        day = paris_day(created)
        if day in signup_days:
            signup_days[day] += 1
    profiles_complete = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(q.profiles)
            .where(q.profiles.c.status == "complete")
        )
    ).scalar_one()

    offer_rows = await _all(
        session,
        sa.select(q.offers.c.source, q.offers.c.created_at).where(
            q.offers.c.created_at >= now - timedelta(days=30)
        ),
    )
    offers_today = sum(1 for _, created in offer_rows if as_utc(created) >= today)
    by_source: dict[str, Counter] = defaultdict(Counter)
    sources: set[str] = set()
    window = set(last_days(14))
    for source, created in offer_rows:
        day = paris_day(created)
        if day in window:
            by_source[day.isoformat()][source] += 1
            sources.add(source)

    # Matching pipeline over the last 24 hours.
    day_ago = now - timedelta(hours=24)
    pairs = await _all(
        session,
        sa.select(
            q.matches.c.status,
            q.matches.c.prefilter_score,
            q.matches.c.computed_at,
            q.matches.c.updated_at,
            q.matches.c.created_at,
        ).where(q.matches.c.updated_at >= day_ago),
    )
    examined = [p for p in pairs if as_utc(p.created_at) >= day_ago]
    excluded = sum(
        1 for p in examined if p.status == "filtered_out" and p.prefilter_score is None
    )
    prefiltered = sum(1 for p in pairs if p.prefilter_score is not None)
    passed = sum(
        1
        for p in pairs
        if p.prefilter_score is not None and p.status in ("pending", "scored")
    )
    analysed = sum(
        1 for p in pairs if p.status == "scored" and as_utc(p.computed_at) >= day_ago
    )
    analyses_today = sum(
        1 for p in pairs if p.status == "scored" and as_utc(p.computed_at) >= today
    )
    prefilter_today = sum(
        1
        for p in pairs
        if p.prefilter_score is not None and as_utc(p.updated_at) >= today
    )

    def pct(part: int, whole: int) -> str | None:
        return f"{round(100 * part / whole)} %" if whole else None

    funnel = [
        FunnelStep(
            key="examined",
            label="Paires examinées",
            value=len(examined),
            note=f"{excluded} écartées d'office (séniorité)" if excluded else None,
        ),
        FunnelStep(
            key="prefiltered",
            label="Pré-analysées",
            value=prefiltered,
            note=f"{settings.MATCHING_PREFILTER_POOL_SIZE} max / candidat",
        ),
        FunnelStep(
            key="passed",
            label=f"ATS ≥ {settings.MATCHING_PREFILTER_MIN_ATS}",
            value=passed,
            note=pct(passed, prefiltered),
        ),
        FunnelStep(key="analysed", label="Analysées", value=analysed, note=None),
    ]

    # Alerts of the day, by channel.
    entries = await _all(
        session,
        sa.select(q.brief_entries.c.channels_sent).where(
            q.brief_entries.c.sent_at >= today
        ),
    )
    sent_by_channel: Counter = Counter()
    for (channels,) in entries:
        for channel in channels or []:
            sent_by_channel[channel] += 1
    incidents = list((await session.exec(select(Incident))).all())
    failed_by_channel: Counter = Counter()
    for incident in incidents:
        if incident.kind == "alert" and incident.fingerprint.startswith("alert:"):
            channel = incident.fingerprint.split(":", 1)[1]
            failed_by_channel[channel] += sum(
                1
                for stamp in incident.occurrences or []
                if as_utc(datetime.fromisoformat(stamp)) >= today
            )
    channel_names = ("email", "whatsapp", "discord")
    alerts_today = [
        AlertChannel(
            channel=c,
            sent=sent_by_channel.get(c, 0),
            failed=failed_by_channel.get(c, 0),
        )
        for c in channel_names
    ]
    sent_total = sum(a.sent for a in alerts_today)
    failed_total = sum(a.failed for a in alerts_today)

    # Jobs.
    jobs: list[JobItem] = []
    for job_id in scheduler_core.registered_jobs():
        run = scheduler_core.job_runs.get(job_id)
        jobs.append(
            JobItem(
                id=job_id,
                label=JOB_LABELS.get(job_id, job_id),
                last_run=run.last_run if run else None,
                status=run.status if run else "never",  # type: ignore[arg-type]
                duration_ms=run.duration_ms if run else None,
                next_run=scheduler_core.next_run_time(job_id),
            )
        )

    open_incidents = sorted(
        (i for i in incidents if i.status in OPEN),
        key=lambda i: as_utc(i.last_seen),
        reverse=True,
    )[:3]

    return OverviewResponse(
        generated_at=now,
        health=await _health(session, jobs, failed_by_channel, now),
        tiles=OverviewTiles(
            users_total=len(users),
            users_new=sum(1 for _, c in users if as_utc(c) >= since),
            profiles_complete=profiles_complete,
            offers_30d=len(offer_rows),
            offers_today=offers_today,
            analyses_today=analyses_today,
            prefilter_today=prefilter_today,
            alerts_sent_today=sent_total,
            alerts_by_channel_today={
                c: sent_by_channel.get(c, 0) for c in channel_names
            },
            alerts_failed_today=failed_total,
        ),
        signups=[DayCount(date=d.isoformat(), count=n) for d, n in signup_days.items()],
        matching_funnel=funnel,
        offers_by_source=OffersBySource(
            sources=sorted(sources),
            days=[
                {
                    "date": d.isoformat(),
                    "counts": dict(by_source.get(d.isoformat(), {})),
                }
                for d in last_days(14)
            ],
        ),
        alerts_today=alerts_today,
        jobs=jobs,
        recent_incidents=[to_item(i) for i in open_incidents],
    )


async def _health(
    session: AsyncSession,
    jobs: list[JobItem],
    failed_by_channel: Counter,
    now: datetime,
) -> list[HealthItem]:
    settings = get_settings()
    health = [
        HealthItem(key="api", label="API", state="ok", detail="répond normalement")
    ]

    try:
        await session.execute(sa.text("SELECT 1"))
        health.append(
            HealthItem(
                key="database", label="Base de données", state="ok", detail="connectée"
            )
        )
    except Exception:
        health.append(
            HealthItem(
                key="database",
                label="Base de données",
                state="bad",
                detail="injoignable",
            )
        )

    if not settings.SCHEDULER_ENABLED or not scheduler_core.scheduler.running:
        health.append(
            HealthItem(
                key="scheduler",
                label="Tâches planifiées",
                state="bad",
                detail="le planificateur est arrêté",
            )
        )
    else:
        in_error = [j for j in jobs if j.status == "error"]
        ok_count = len(jobs) - len(in_error)
        health.append(
            HealthItem(
                key="scheduler",
                label="Tâches planifiées",
                state="warn" if in_error else "ok",
                detail=f"{ok_count} sur {len(jobs)} sans erreur",
            )
        )

    stuck = await _all(
        session,
        sa.select(q.batches.c.created_at).where(q.batches.c.status == "submitted"),
    )
    oldest_hours = max(
        ((now - as_utc(c)).total_seconds() / 3600 for (c,) in stuck), default=0
    )
    health.append(
        HealthItem(
            key="openai",
            label="OpenAI (Batch)",
            state="bad" if oldest_hours > 24 else "warn" if oldest_hours > 3 else "ok",
            detail=(
                f"{len(stuck)} lot(s) en attente" if stuck else "aucun lot en attente"
            ),
        )
    )

    wa_failed = failed_by_channel.get("whatsapp", 0)
    if not settings.WHATSAPP_ACCESS_TOKEN:
        wa = HealthItem(
            key="whatsapp", label="WhatsApp", state="warn", detail="non configuré"
        )
    elif wa_failed:
        wa = HealthItem(
            key="whatsapp",
            label="WhatsApp",
            state="warn",
            detail=f"{wa_failed} échec(s) aujourd'hui",
        )
    else:
        wa = HealthItem(
            key="whatsapp",
            label="WhatsApp",
            state="ok",
            detail="aucun échec aujourd'hui",
        )
    health.append(wa)

    mail_rows = await _all(
        session,
        sa.select(q.emails.c.status).where(
            q.emails.c.status.in_(("pending", "failed"))
        ),
    )
    pending = sum(1 for (s,) in mail_rows if s == "pending")
    failed = sum(1 for (s,) in mail_rows if s == "failed")
    health.append(
        HealthItem(
            key="email",
            label="E-mails",
            state="warn" if failed or pending > 50 else "ok",
            detail=(
                f"{failed} en échec définitif"
                if failed
                else f"{pending} en attente"
                if pending
                else "file vide"
            ),
        )
    )
    return health


# ---------------------------------------------------------------- behaviour --
async def behavior(session: AsyncSession, days: int) -> BehaviorResponse:
    now = now_utc()
    since = now - timedelta(days=days)
    today = start_of_today_paris()

    users = await _all(
        session,
        sa.select(
            q.users.c.id,
            q.users.c.email,
            q.users.c.created_at,
            q.users.c.is_verified,
            q.users.c.google_id,
        ).where(q.users.c.is_active.is_(True), q.users.c.isadmin.is_(False)),
    )
    user_ids = {u.id for u in users}
    profiles = await _all(
        session,
        sa.select(
            q.profiles.c.id,
            q.profiles.c.user_id,
            q.profiles.c.status,
            q.profiles.c.verification_completed_at,
        ),
    )
    profile_by_user = {p.user_id: p for p in profiles if p.user_id in user_ids}
    applied_rows = await _all(
        session,
        sa.select(
            q.matches.c.candidate_profile_id,
            q.matches.c.job_offer_id,
            q.matches.c.career_score,
            q.matches.c.application_status_updated_at,
        ).where(APPLIED_SQL),
    )
    profile_user = {p.id: p.user_id for p in profiles}
    applicant_users = {
        profile_user[r.candidate_profile_id]
        for r in applied_rows
        if r.candidate_profile_id in profile_user
    }
    alerted_users = {
        row[0]
        for row in await _all(session, sa.select(q.brief_entries.c.user_id).distinct())
    }

    def lost(prev: int, cur: int) -> str | None:
        diff = prev - cur
        return f"{diff} perdu(s)" if diff > 0 else None

    n_signed = len(users)
    n_verified = sum(1 for u in users if u.is_verified)
    n_cv = len(profile_by_user)
    n_profile = sum(
        1 for p in profile_by_user.values() if p.verification_completed_at is not None
    )
    n_alert = len(alerted_users & user_ids)
    n_app = len(applicant_users & user_ids)
    optimizations = await _all(
        session,
        sa.select(
            q.cv_optimizations.c.created_at,
            q.cv_optimizations.c.confirmed_at,
            q.matches.c.candidate_profile_id,
            q.matches.c.job_offer_id,
        ).select_from(
            q.cv_optimizations.join(
                q.matches, q.matches.c.id == q.cv_optimizations.c.candidate_match_id
            )
        ),
    )
    optimizing_users = {
        profile_user[r.candidate_profile_id]
        for r in optimizations
        if r.candidate_profile_id in profile_user
    } & user_ids
    funnel = [
        FunnelStep(key="signed_up", label="Inscrits", value=n_signed),
        FunnelStep(
            key="verified",
            label="E-mail confirmé",
            value=n_verified,
            note=lost(n_signed, n_verified),
        ),
        FunnelStep(
            key="cv_imported",
            label="CV importé",
            value=n_cv,
            note=lost(n_verified, n_cv),
        ),
        FunnelStep(
            key="profile_verified",
            label="Profil vérifié",
            value=n_profile,
            note=lost(n_cv, n_profile),
        ),
        FunnelStep(
            key="first_alert",
            label="1re alerte reçue",
            value=n_alert,
            note=lost(n_profile, n_alert),
        ),
        FunnelStep(
            key="first_application",
            label="1re candidature",
            value=n_app,
            note=f"{n_alert - n_app} jamais candidaté" if n_alert > n_app else None,
        ),
        FunnelStep(
            key="cv_optimized",
            label="CV adapté à une offre",
            value=len(optimizing_users),
            note=None,
        ),
    ]

    views = list(
        (await session.exec(select(PageView).where(PageView.created_at >= since))).all()
    )
    first_view = (
        await session.execute(sa.select(sa.func.min(PageView.created_at)))
    ).scalar_one()
    page_counter = Counter(v.path for v in views)
    top_pages = [
        TopPage(path=path, label=PAGE_LABELS.get(path, path), count=count)
        for path, count in page_counter.most_common(8)
    ]

    applied_period = [
        r
        for r in applied_rows
        if r.application_status_updated_at is not None
        and as_utc(r.application_status_updated_at) >= since
    ]
    offer_ids = {r.job_offer_id for r in applied_period}
    offer_info: dict = {}
    if offer_ids:
        for row in await _all(
            session,
            sa.select(q.offers.c.id, q.offers.c.title, q.offers.c.company_name).where(
                q.offers.c.id.in_(offer_ids)
            ),
        ):
            offer_info[row.id] = row
    per_offer: dict = defaultdict(list)
    for r in applied_period:
        per_offer[r.job_offer_id].append(r.career_score)
    top_offers = sorted(per_offer.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    top_applied = [
        TopOffer(
            title=offer_info[oid].title if oid in offer_info else "Offre supprimée",
            company=(offer_info[oid].company_name or "") if oid in offer_info else "",
            count=len(scores),
            avg_score=(
                round(sum(s for s in scores if s) / len([s for s in scores if s]))
                if any(scores)
                else None
            ),
        )
        for oid, scores in top_offers
    ]

    cv_stats = await _cv_optimization_stats(
        session, optimizations, profile_user, user_ids, since, today
    )

    # Cost.
    usage = await _usage_rows(session, now - timedelta(days=max(days, 7)))
    any_usage = (
        await session.execute(sa.select(sa.func.count()).select_from(AiUsage))
    ).scalar_one() > 0
    detail_stage = "analysis"
    cost_days: dict = {d: [0.0, 0.0] for d in last_days(7)}
    cost_today = cost_period = 0.0
    for row in usage:
        eur = cost_eur(row.model, row.input_tokens, row.output_tokens)
        created = as_utc(row.created_at)
        day = paris_day(created)
        if day in cost_days:
            cost_days[day][0 if row.stage == detail_stage else 1] += eur
        if created >= today:
            cost_today += eur
        if created >= since:
            cost_period += eur

    unverified_users = [u for u in users if not u.is_verified]
    return BehaviorResponse(
        tracking_since=as_utc(first_view) if first_view else None,
        tiles=BehaviorTiles(
            active_users=len({v.user_id for v in views}),
            users_total=n_signed,
            page_views=len(views),
            applications=len(applied_period),
            unverified=len(unverified_users),
            cost_today_eur=round(cost_today, 2) if any_usage else None,
            cost_period_eur=round(cost_period, 2) if any_usage else None,
        ),
        funnel=funnel,
        signup_modes=[
            SignupMode(
                key="password",
                label="E-mail + mot de passe",
                count=sum(1 for u in users if not u.google_id),
            ),
            SignupMode(
                key="google", label="Google", count=sum(1 for u in users if u.google_id)
            ),
        ],
        top_pages=top_pages,
        top_applied_offers=top_applied,
        cv_optimization=cv_stats,
        cost_daily=[
            CostDay(
                date=d.isoformat(),
                detailed_eur=round(v[0], 3),
                prefilter_eur=round(v[1], 3),
            )
            for d, v in cost_days.items()
        ],
        cost_note=_cost_note(any_usage),
        accounts_to_follow=_accounts_to_follow(
            users, profile_by_user, applicant_users, now
        ),
    )


async def _cv_optimization_stats(
    session: AsyncSession,
    rows: list,
    profile_user: dict,
    user_ids: set,
    since: datetime,
    today: datetime,
) -> CvOptimizationStats:
    mine = [r for r in rows if profile_user.get(r.candidate_profile_id) in user_ids]
    period = [r for r in mine if as_utc(r.created_at) >= since]
    kept = [r for r in period if r.confirmed_at is not None]
    users = {profile_user[r.candidate_profile_id] for r in period}
    per_offer: dict = defaultdict(lambda: [0, 0])
    for r in period:
        per_offer[r.job_offer_id][0] += 1
        if r.confirmed_at is not None:
            per_offer[r.job_offer_id][1] += 1
    top = sorted(per_offer.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
    info: dict = {}
    if top:
        for row in await _all(
            session,
            sa.select(q.offers.c.id, q.offers.c.title, q.offers.c.company_name).where(
                q.offers.c.id.in_([oid for oid, _ in top])
            ),
        ):
            info[row.id] = row
    return CvOptimizationStats(
        generated_today=sum(1 for r in mine if as_utc(r.created_at) >= today),
        generated=len(period),
        kept=len(kept),
        users=len(users),
        per_user=round(len(period) / len(users), 1) if users else None,
        top_offers=[
            CvOptimizedOffer(
                title=info[oid].title if oid in info else "Offre supprimée",
                company=(info[oid].company_name or "") if oid in info else "",
                generated=counts[0],
                kept=counts[1],
            )
            for oid, counts in top
        ],
    )


def _cost_note(any_usage: bool) -> str:
    if not any_usage:
        return (
            "Le suivi des coûts démarre avec cette version : les chiffres "
            "arrivent après le prochain matching."
        )
    return (
        "Estimation à partir des jetons réellement consommés et des tarifs "
        "OpenAI configurés (remise Batch de 50 % incluse). Les embeddings ne "
        "sont pas comptés."
    )


def _accounts_to_follow(
    users, profile_by_user, applicant_users, now
) -> list[AccountToFollow]:
    follow: list[AccountToFollow] = []
    for u in sorted(users, key=lambda u: as_utc(u.created_at), reverse=True):
        mode = "google" if u.google_id else "password"
        profile = profile_by_user.get(u.id)
        if not u.is_verified:
            stage, label, action = (
                "unverified",
                "E-mail jamais confirmé",
                "resend_verification",
            )
        elif profile is None:
            stage, label, action = "no_cv", "CV pas encore importé", "reminder"
        elif u.id not in applicant_users and (now - as_utc(u.created_at)) > timedelta(
            days=3
        ):
            stage, label, action = "no_application", "Jamais candidaté", "reminder"
        else:
            continue
        follow.append(
            AccountToFollow(
                user_id=u.id,
                email=u.email,
                created_at=as_utc(u.created_at),
                mode=mode,  # type: ignore[arg-type]
                stage=stage,  # type: ignore[arg-type]
                stage_label=label,
                action=action,  # type: ignore[arg-type]
            )
        )
    return follow[:20]


# ---------------------------------------------------------------- users ----
async def users_list(session: AsyncSession) -> list[UserRow]:
    users = await _all(
        session,
        sa.select(
            q.users.c.id,
            q.users.c.email,
            q.users.c.first_name,
            q.users.c.created_at,
            q.users.c.is_verified,
            q.users.c.google_id,
            q.users.c.isadmin,
        )
        .where(q.users.c.is_active.is_(True))
        .order_by(q.users.c.created_at.desc()),
    )
    profiles = {
        p.user_id: p
        for p in await _all(
            session,
            sa.select(q.profiles.c.id, q.profiles.c.user_id, q.profiles.c.status),
        )
    }
    profile_user = {p.id: uid for uid, p in profiles.items()}
    applications: Counter = Counter()
    for row in await _all(
        session, sa.select(q.matches.c.candidate_profile_id).where(APPLIED_SQL)
    ):
        uid = profile_user.get(row[0])
        if uid:
            applications[uid] += 1
    last_seen: dict[uuid.UUID, datetime] = {}
    for user_id, last in await _all(
        session,
        sa.select(
            sa.column("user_id", sa.Uuid),
            sa.func.max(sa.column("created_at", sa.DateTime(timezone=True))),
        )
        .select_from(PageView)
        .group_by(sa.column("user_id")),
    ):
        last_seen[user_id] = as_utc(last)
    prefs = {
        p.user_id: p
        for p in await _all(
            session,
            sa.select(
                q.preferences.c.user_id,
                q.preferences.c.email_enabled,
                q.preferences.c.discord_enabled,
                q.preferences.c.whatsapp_enabled,
            ),
        )
    }
    rows = []
    for u in users:
        profile = profiles.get(u.id)
        pref = prefs.get(u.id)
        rows.append(
            UserRow(
                user_id=u.id,
                email=u.email,
                first_name=u.first_name,
                created_at=as_utc(u.created_at),
                mode="google" if u.google_id else "password",
                verified=bool(u.is_verified),
                profile_status=(
                    "none"
                    if profile is None
                    else "complete"
                    if profile.status == "complete"
                    else "draft"
                ),
                last_seen=last_seen.get(u.id),
                alerts_enabled=bool(
                    pref
                    and (
                        pref.email_enabled
                        or pref.discord_enabled
                        or pref.whatsapp_enabled
                    )
                ),
                applications=applications.get(u.id, 0),
                is_admin=bool(u.isadmin),
            )
        )
    return rows
