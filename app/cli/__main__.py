"""Entry point: `python -m app.cli <command>`.

Commands:
    new-module <name> [--force]   scaffold a new feature module
    list-modules                  list discovered modules
    routes                        print the registered route table
    sync-offers                   fetch job offers from configured providers now
    run-matching                  score complete profiles against offers now
    backfill-contract-type        guess contract_type for already-stored offers
                                   that have none (one-off, safe to re-run)
    backfill-regret-index         compute the Regret Index for already-stored
                                   matches that predate it (one-off, safe to
                                   re-run)
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


def _cmd_backfill_regret_index(_: argparse.Namespace) -> int:
    """One-off: RegretService.get_or_compute only runs at match-(re)scoring
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
    CompanyRegretProfile's own cache means a company shared by many matches
    is only actually looked up on SimplyHired once."""
    import asyncio

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
            for match in candidates:
                availability, score = await regret.get_or_compute(match.company_name)
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
    import asyncio

    from app.core.database import AsyncSessionLocal
    from app.modules.matching.service import MatchingService

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            report = await MatchingService(session).sync_all()
        print(
            f"profiles={report.profiles_processed} scored={report.pairs_scored} "
            f"skipped_fresh={report.pairs_skipped_fresh} failed={report.pairs_failed} "
            f"skipped_no_cv_text={report.profiles_skipped_no_cv_text}"
        )

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
        "run-matching", help="score complete profiles against offers now"
    ).set_defaults(func=_cmd_run_matching)
    sub.add_parser(
        "backfill-contract-type",
        help="guess contract_type for already-stored offers that have none",
    ).set_defaults(func=_cmd_backfill_contract_type)
    sub.add_parser(
        "backfill-regret-index",
        help="compute the Regret Index for already-stored matches that predate it",
    ).set_defaults(func=_cmd_backfill_regret_index)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
