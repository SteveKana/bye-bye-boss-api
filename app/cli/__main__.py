"""Entry point: `python -m app.cli <command>`.

Commands:
    new-module <name> [--force]   scaffold a new feature module
    list-modules                  list discovered modules
    routes                        print the registered route table
    sync-offers                   fetch job offers from configured providers now
    run-matching                  shortlist offers + submit pending OpenAI batches
    poll-matching-batches         apply finished OpenAI matching batches now
    offers-census [--days N]      count France Travail offers by département
                                   (read-only, stores nothing)
    import-leads [--apply] [--limit N]
                                  waitlist leads -> accounts + one invitation
                                   mail each (dry run unless --apply)
    backfill-contract-type        guess contract_type for already-stored offers
                                   that have none (one-off, safe to re-run)

Regret Index feature disabled 2026-10-03 (Steve) -- backfill-regret-index
is no longer registered below, so it can't be invoked from this CLI anymore.
"""

from __future__ import annotations

import argparse
import sys


def _count_endpoints(router) -> int:
    """Count real endpoints under a router, descending through FastAPI's lazy
    `_IncludedRouter` wrappers (0.100+ nested includes are not pre-flattened)."""
    total = 0
    for route in getattr(router, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            total += _count_endpoints(inner)
        elif getattr(route, "methods", None):
            total += 1
    return total


def _cmd_new_module(args: argparse.Namespace) -> int:
    from app.cli.generator import generate_module

    try:
        target = generate_module(args.name, force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"✓ created module at {target}")
    print("\nNext steps:")
    print(f"  1. alembic revision --autogenerate -m 'add {target.name}'")
    print("  2. alembic upgrade head")
    print("  3. start the app — the module is already auto-registered.")
    return 0


def _cmd_list_modules(_: argparse.Namespace) -> int:
    from app.main import MODULES

    for module in MODULES:
        count = _count_endpoints(module.router) if module.router else 0
        print(f"  {module.name:<16} order={module.order:<4} endpoints={count}")
    return 0


def _cmd_routes(_: argparse.Namespace) -> int:
    from app.main import app

    paths: dict = app.openapi().get("paths", {})
    rows = [
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
    ]
    for method, path in sorted(rows, key=lambda r: (r[1], r[0])):
        print(f"  {method:<8} {path}")
    return 0


def _cmd_sync_offers(_: argparse.Namespace) -> int:
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.modules.offers.service import OffersIngestionService

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            report = await OffersIngestionService(session).sync()
        print(
            f"fetched={report.fetched} created={report.created} "
            f"updated={report.updated}"
        )
        if report.skipped_unconfigured:
            print(
                "skipped (no credentials configured): "
                + ", ".join(report.skipped_unconfigured)
            )

    asyncio.run(_run())
    return 0


def _cmd_backfill_contract_type(_: argparse.Namespace) -> int:
    """One-off: core/contract_type.py's keyword guess only runs at ingestion
    time (see adzuna.py/france_travail.py's `_normalize`), so it never
    touches an offer already sitting in the DB from before that fallback
    existed -- that offer only gets fixed if/when it's re-fetched by a
    provider, which isn't guaranteed for an older listing that may have
    already fallen out of the source's own search results. This applies the
    same guess directly to every already-stored offer with no contract_type,
    from the title/description already on file. Safe to re-run: it only
    ever touches offers where contract_type is still empty."""
    import asyncio

    from app.core.contract_type import guess_contract_type
    from app.core.database import AsyncSessionLocal
    from app.modules.offers.repository import JobOfferRepository

    async def _run() -> None:
        updated = 0
        async with AsyncSessionLocal() as session:
            offers = JobOfferRepository(session)
            candidates = await offers.list_missing_contract_type()
            for offer in candidates:
                guess = guess_contract_type(offer.title, offer.description)
                if guess:
                    await offers.update(offer, {"contract_type": guess})
                    updated += 1
            await session.commit()
        print(f"checked={len(candidates)} updated={updated}")

    asyncio.run(_run())
    return 0


def _cmd_backfill_regret_index(_: argparse.Namespace) -> int:  # pragma: no cover
    """DISABLED 2026-10-03 (Steve: masquer/désactiver tout l'indice de
    regret) -- no longer registered as a CLI subcommand below, so this
    can't be invoked anymore; left in place, unregistered, so the feature
    can be turned back on later without rewriting it. Docstring below is
    the original, pre-disable one, kept for that same reason.

    One-off: RegretService.get_or_compute only runs at match-(re)scoring
    time (see matching/service.py's `_upsert`), which itself only fires when
    a pair is no longer "fresh" (profile/offer unchanged since last scored --
    see `_run_for_profile`'s to_score logic). A match scored before the
    Regret Index existed (Reddit era or the later SimplyHired pivot) stays
    "fresh" forever from the matching engine's point of view, since neither
    the candidate's profile nor the offer itself changed -- so it silently
    keeps its default regret_availability="unavailable" until something
    else invalidates it, which may never happen for an older listing. This
    computes it directly from each such match's already-stored company_name,
    without re-running the (expensive) LLM match analysis at all. Safe to
    re-run: only touches matches still at the default "unavailable", and
    within one run, CompanyRegretProfile's own cache means a company shared
    by many matches is only actually looked up on SimplyHired once. Paced
    the same way as regret_jobs.py's monthly refresh (a random 1.5-3.5s
    pause between matches): the first production run of this command fired
    ~120 requests inside 2 seconds with no pacing at all and SimplyHired
    403'd every single one.

    Passes force=True for the same reason regret_jobs.py's monthly job
    does: without it, get_or_compute's 30-day TTL means a SECOND run of
    this exact command -- e.g. right after fixing whatever made SimplyHired
    reject the first attempt -- just replays the cached "unavailable" from
    the failed run and never retries at all (this is exactly what happened
    testing the pacing fix above: a re-run came back instantly with zero
    fetch attempts, purely from cache). A one-off retry tool defeats its
    own purpose if it trusts a cache written by the failure it's retrying."""
    import asyncio
    import random

    from app.core.database import AsyncSessionLocal
    from app.modules.matching.regret_service import RegretService
    from app.modules.matching.repository import CandidateMatchRepository

    async def _run() -> None:
        available = 0
        async with AsyncSessionLocal() as session:
            matches = CandidateMatchRepository(session)
            candidates = await matches.list(
                filters={"regret_availability": "unavailable"}
            )
            regret = RegretService(session)
            for index, match in enumerate(candidates):
                if index > 0:
                    await asyncio.sleep(random.uniform(1.5, 3.5))
                availability, score = await regret.get_or_compute(
                    match.company_name, force=True
                )
                if (
                    availability != match.regret_availability
                    or score != match.regret_score
                ):
                    await matches.update(
                        match,
                        {"regret_availability": availability, "regret_score": score},
                    )
                if availability == "available":
                    available += 1
            await session.commit()
        print(f"checked={len(candidates)} now_available={available}")

    asyncio.run(_run())
    return 0


def _cmd_run_matching(_: argparse.Namespace) -> int:
    """Runs the daily shortlisting for every complete profile, then submits
    whatever is waiting for a pipeline stage to OpenAI's Batch API. Results
    are applied by the scheduled poll job, or `poll-matching-batches`."""
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.modules.matching.batch_service import MatchingBatchService
    from app.modules.matching.service import MatchingService

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            report = await MatchingService(session).sync_all()
        async with AsyncSessionLocal() as session:
            submitted = await MatchingBatchService(session).submit_pending()
        print(
            f"profiles={report.profiles_processed} "
            f"shortlisted={report.pairs_shortlisted} "
            f"skipped_no_cv_text={report.profiles_skipped_no_cv_text} "
            f"batches_submitted={submitted.batches_submitted} "
            f"requests_submitted={submitted.requests_submitted}"
        )

    asyncio.run(_run())
    return 0


def _cmd_poll_matching_batches(_: argparse.Namespace) -> int:
    """One manual pass of the scheduled batch-polling job."""
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.modules.matching.batch_service import MatchingBatchService

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            report = await MatchingBatchService(session).poll_batches()
        print(
            f"batches_completed={report.batches_completed} "
            f"batches_failed={report.batches_failed} promoted={report.promoted} "
            f"filtered_out={report.filtered_out} scored={report.scored} "
            f"failed_pairs={report.failed}"
        )

    asyncio.run(_run())
    return 0


def _cmd_offers_census(args: argparse.Namespace) -> int:
    """Read-only: how many France Travail offers were created in the last N
    days, nationally and per département. Sizes a bulk import."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from app.modules.offers.providers.france_travail import FranceTravailProvider

    provider = FranceTravailProvider()
    if not provider.is_configured():
        print("France Travail n'est pas configuré (identifiants manquants).")
        return 1

    async def _run() -> None:
        since = datetime.now(UTC) - timedelta(days=args.days)
        counts = await provider.census(since=since)
        print(f"== Offres France Travail créées depuis {args.days} jours ==")
        print(f"France entière: {counts.get('ALL')}")
        known = [(d, c) for d, c in counts.items() if d != "ALL" and c is not None]
        failed = [d for d, c in counts.items() if d != "ALL" and c is None]
        print(f"somme des départements: {sum(c for _, c in known)}")
        print(f"départements en échec: {failed or 'aucun'}")
        print("10 plus gros départements:")
        for dept, count in sorted(known, key=lambda x: x[1], reverse=True)[:10]:
            print(f"   {dept}: {count}")
        over = [d for d, c in known if c > 1150]
        print(f"départements au-dessus de 1150 (à découper par date): {len(over)}")

    asyncio.run(_run())
    return 0


def _cmd_import_leads(args: argparse.Namespace) -> int:
    """Waitlist leads -> accounts (e-mail only) + ONE invitation mail each.
    Dry run unless --apply; leads that already have an account, or were
    already invited, are skipped, so re-running is harmless."""
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.modules.leads.importer import import_leads

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            r = await import_leads(session, apply=args.apply, limit=args.limit)
        mode = "APPLIQUÉ" if args.apply else "SIMULATION (rien n'est créé ni envoyé)"
        print(f"== {mode} ==")
        print(f"leads au total: {r.leads_total}")
        print(f"déjà un compte (ignorés): {len(r.already_accounts)}")
        for e in r.already_accounts:
            print(f"   - {e}")
        print(f"déjà invités (ignorés): {len(r.already_invited)}")
        print(f"à importer: {len(r.to_import)}")
        for e in r.to_import:
            print(f"   - {e}")
        if args.apply:
            print(f"comptes créés: {len(r.accounts_created)}")
            print(f"invitations en file d'envoi: {len(r.invitations_queued)}")

    asyncio.run(_run())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new-module", help="scaffold a new feature module")
    p_new.add_argument("name", help="singular resource name, e.g. 'product'")
    p_new.add_argument("--force", action="store_true", help="overwrite if exists")
    p_new.set_defaults(func=_cmd_new_module)

    sub.add_parser("list-modules", help="list discovered modules").set_defaults(
        func=_cmd_list_modules
    )
    sub.add_parser("routes", help="print the route table").set_defaults(
        func=_cmd_routes
    )
    sub.add_parser(
        "sync-offers", help="fetch job offers from configured providers now"
    ).set_defaults(func=_cmd_sync_offers)
    sub.add_parser(
        "run-matching",
        help="shortlist offers for complete profiles and submit pending batches now",
    ).set_defaults(func=_cmd_run_matching)
    sub.add_parser(
        "poll-matching-batches", help="apply finished OpenAI matching batches now"
    ).set_defaults(func=_cmd_poll_matching_batches)
    sub.add_parser(
        "backfill-contract-type",
        help="guess contract_type for already-stored offers that have none",
    ).set_defaults(func=_cmd_backfill_contract_type)
    p_census = sub.add_parser(
        "offers-census",
        help="count France Travail offers by département (read-only)",
    )
    p_census.add_argument(
        "--days", type=int, default=15, help="created in the last N days"
    )
    p_census.set_defaults(func=_cmd_offers_census)
    p_leads = sub.add_parser(
        "import-leads",
        help="waitlist leads -> accounts + one invitation mail (dry run by default)",
    )
    p_leads.add_argument(
        "--apply", action="store_true", help="really create accounts and queue mails"
    )
    p_leads.add_argument("--limit", type=int, default=None, help="at most N leads")
    p_leads.set_defaults(func=_cmd_import_leads)
    # backfill-regret-index subcommand disabled 2026-10-03, see
    # _cmd_backfill_regret_index's own docstring above.
    # sub.add_parser(
    #     "backfill-regret-index",
    #     help="compute the Regret Index for already-stored matches that predate it",
    # ).set_defaults(func=_cmd_backfill_regret_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
