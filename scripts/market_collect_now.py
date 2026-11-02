"""Runs the market-watch collection right now, until every offer is filed,
then refreshes the weekly counts. Same work as the 30-minute job, without
waiting for it: handy right after the feature ships, to catch up on every
offer already in the database in one go.

CALLS THE AI (gpt-6-luna): roughly 0.4 $ for ~5 000 offers (estimate, not
measured). Stops by itself; run it again if it was interrupted -- offers
already filed are skipped.

Usage (from the repo root, inside the venv, with .env loaded):
    python -m scripts.market_collect_now
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.market.service import MarketCollectionService

MAX_ROUNDS = 50  # safety net: 50 x 600 offers


async def main() -> None:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY est vide : rien à faire.")
        return
    total = failed = 0
    for round_number in range(1, MAX_ROUNDS + 1):
        async with AsyncSessionLocal() as session:
            report = await MarketCollectionService(session).classify_pending()
        total += report.classified
        failed += report.failed
        print(
            f"Lot {round_number} : {report.classified} offres classées, "
            f"{report.failed} en échec (tokens : {report.input_tokens} "
            f"entrée / {report.output_tokens} sortie)"
        )
        if report.classified == 0:
            break
    async with AsyncSessionLocal() as session:
        groups = await MarketCollectionService(session).aggregate_weeks()
    print(f"\nTerminé : {total} offres classées, {failed} en échec.")
    print(f"Comptages hebdomadaires recalculés ({groups} groupes).")
    if failed:
        print("Des offres ont échoué : relance la commande pour réessayer.")


if __name__ == "__main__":
    asyncio.run(main())
