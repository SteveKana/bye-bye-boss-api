"""One-off cost/quality comparison: gpt-5 vs. gpt-5-mini for matching.

Run this ON THE SERVER (it needs the real DATABASE_URL and OPENAI_API_KEY
from .env) -- it is READ-ONLY, it never writes to the database. For each of
the N most recently scored candidate/offer pairs, it re-runs the *exact
same* prompt (via matching/gateway.analyse_match, same JSON parsing and
schema validation as production) through gpt-5-mini, and prints it next to
the real gpt-5 result already on file for that pair -- so you can judge
whether the quality holds up before flipping MATCHING_OPENAI_MODEL in
app/core/config.py from "gpt-5" to "gpt-5-mini".

Cost: only N new API calls are made (gpt-5-mini only -- the gpt-5 side is
read from a match already computed in production, no new gpt-5 call). Check
the OpenAI Usage dashboard right after running this to see exactly what
those N calls cost.

Usage (from the repo root, inside the venv, with .env loaded):
    python -m scripts.compare_matching_models [--count 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlmodel import desc

from app.core.database import AsyncSessionLocal
from app.modules.cv import CandidateProfileRepository
from app.modules.matching.gateway import MatchingFailedError, analyse_match
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.service import _format_offer_text
from app.modules.offers import JobOfferRepository

_COMPARE_MODEL = "gpt-5-mini"

_SUMMARY_FIELDS = ("career_score", "ats_score", "ats_potential", "blocking_message")


def _summary(analysis: dict) -> dict:
    out = {field: analysis.get(field) for field in _SUMMARY_FIELDS}
    out["matches"] = len(analysis.get("matches") or [])
    out["blocking_requirements"] = len(analysis.get("blocking_requirements") or [])
    out["ats_gaps"] = len(analysis.get("ats_gaps") or [])
    return out


def _print_case(n: int, offer_title: str, company: str, gpt5: dict, mini: dict) -> None:
    print(f"\n{'=' * 70}\n[{n}] {offer_title} -- {company}\n{'=' * 70}")
    print(f"{'':<26}{'gpt-5 (en production)':<28}{'gpt-5-mini (ce test)'}")
    for field in (
        "career_score",
        "ats_score",
        "ats_potential",
        "matches",
        "blocking_requirements",
        "ats_gaps",
    ):
        print(f"{field:<26}{str(gpt5[field]):<28}{mini[field]}")
    print(f"\n  gpt-5 blocking_message  : {gpt5['blocking_message'] or '(vide)'}")
    print(f"  mini  blocking_message  : {mini['blocking_message'] or '(vide)'}")


async def _run(count: int) -> None:
    async with AsyncSessionLocal() as session:
        matches = await CandidateMatchRepository(session).list(
            order_by=desc(CandidateMatch.computed_at),
            limit=count * 3,  # over-fetch: some pairs get skipped below
        )
        profiles = CandidateProfileRepository(session)
        offers = JobOfferRepository(session)

        done = 0
        dumps: list[dict] = []
        for match in matches:
            if done >= count:
                break
            if not match.analysis:
                continue

            profile = await profiles.get(match.candidate_profile_id)
            offer = await offers.get(match.job_offer_id)
            if profile is None or offer is None or not profile.raw_text:
                continue

            try:
                mini_analysis = await analyse_match(
                    profile.raw_text,
                    _format_offer_text(offer),
                    model=_COMPARE_MODEL,
                )
            except MatchingFailedError as exc:
                print(f"\n[skipped] {offer.title} -- gpt-5-mini call failed: {exc}")
                continue

            done += 1
            mini_dict = mini_analysis.model_dump(mode="json")
            _print_case(
                done,
                offer.title,
                match.company_name or offer.company_name or "",
                _summary(match.analysis),
                _summary(mini_dict),
            )
            dumps.append(
                {
                    "offer_title": offer.title,
                    "gpt5_production": match.analysis,
                    "gpt5_mini_test": mini_dict,
                }
            )

        if done == 0:
            print(
                "Aucune paire candidat/offre déjà notée trouvée en base "
                "-- rien à comparer pour l'instant."
            )
            return

        out_path = "/tmp/compare_matching_models_result.json"
        with open(out_path, "w") as f:
            json.dump(dumps, f, ensure_ascii=False, indent=2)
        print(f"\n{'=' * 70}")
        print(f"{done} paire(s) comparée(s). Détail complet (JSON) : {out_path}")
        print(
            "Va voir le dashboard Usage OpenAI (platform.openai.com -> Usage) "
            "pour le coût réel de ces appels gpt-5-mini."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of already-scored pairs to compare (default: 3).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.count))


if __name__ == "__main__":
    main()
