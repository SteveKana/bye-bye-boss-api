"""Where the market-watch collection stands, and which specialties pass the
threshold. READ-ONLY (no AI call, nothing written) -- run it on the server
whenever you want the real volumes.

Usage (from the repo root, inside the venv, with .env loaded):
    python -m scripts.market_report
"""

from __future__ import annotations

import asyncio

import sqlalchemy as sa

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.market.models import MarketGroupWeekly, OfferMarketTag
from app.modules.offers import JobOffer


async def main() -> None:
    settings = get_settings()
    threshold = settings.MARKET_MIN_OFFERS
    async with AsyncSessionLocal() as session:
        offers = (
            await session.execute(sa.select(sa.func.count()).select_from(JobOffer))
        ).scalar_one()
        tagged = (
            await session.execute(
                sa.select(sa.func.count()).select_from(OfferMarketTag)
            )
        ).scalar_one()
        print(f"Offres en base : {offers} · déjà classées par l'IA : {tagged}")
        if tagged < offers:
            print(f"  -> il reste {offers - tagged} offres à classer (la collecte "
                  "tourne toutes les 30 minutes).")  # fmt: skip

        rows = (
            await session.execute(
                sa.select(
                    OfferMarketTag.family,
                    OfferMarketTag.specialty,
                    sa.func.count(),
                )
                .group_by(OfferMarketTag.family, OfferMarketTag.specialty)
                .order_by(sa.func.count().desc())
            )
        ).all()
        print(f"\nOffres classées par spécialité (seuil d'envoi : {threshold}) :")
        print("(sur 4 semaines complètes, le seuil se lit sur l'ensemble des semaines)")
        for family, specialty, count in rows:
            mark = "OK " if count >= threshold else "   "
            print(f"  {mark}{count:5d}  {family} › {specialty or '(famille seule)'}")

        weeks = (
            await session.execute(
                sa.select(
                    MarketGroupWeekly.week_start,
                    sa.func.sum(MarketGroupWeekly.offers_total),
                    sa.func.bool_and(MarketGroupWeekly.is_complete),
                )
                .where(MarketGroupWeekly.specialty == "")
                .group_by(MarketGroupWeekly.week_start)
                .order_by(MarketGroupWeekly.week_start)
            )
        ).all()
        print("\nSemaines enregistrées (offres classées, toutes familles) :")
        for week_start, total, complete in weeks:
            state = "complète" if complete else "en cours"
            print(f"  semaine du {week_start} : {total} offres ({state})")


if __name__ == "__main__":
    asyncio.run(main())
