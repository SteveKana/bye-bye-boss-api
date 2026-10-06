"""One-off manual trigger for the Regret Index refresh (SimplyHired + Indeed
reviews), added 2026-10-02 for Steve's one-time Bright Data pull.

The monthly job (app/modules/matching/regret_jobs.py) only runs on its own
schedule (03:00 on the 1st of the month, Europe/Paris) -- this script calls
the exact same function immediately, so Steve doesn't have to wait for the
next 1st to do his one-time Indeed/Bright Data pull. Same pacing, same
per-company error isolation, same logging -- this is NOT a separate code
path, just an on-demand call to refresh_all_company_regret_profiles().

Run this ON THE SERVER, from the repo root, with the venv active (so
DATABASE_URL and the two BRIGHTDATA_* variables are read from .env):

    cd /var/www/bye-bye-boss-api/<site>/repo
    source ../venv/bin/activate
    python -m scripts.run_regret_refresh_once

Before running, set these three in that .env (see .env.example):
    BRIGHTDATA_API_KEY=...
    BRIGHTDATA_WEB_UNLOCKER_ZONE=web_unlocker1
    INDEED_REVIEWS_MAX_PAGES_PER_COMPANY=5   # or however many pages wanted

Then restart the service once so it picks up the new .env values before
running this script (systemctl restart <site>) -- the script itself opens
its own DB sessions but reads settings the same way the running app does.

Once this finishes and the reviews are confirmed in the database, remove
BRIGHTDATA_API_KEY (and the other two lines) from .env and restart the
service again. From then on indeed_gateway.is_configured() is False, so
no further Bright Data request is ever made and nothing more can be
billed -- the Indeed reviews already stored stay exactly as they are (see
regret_service.py's _refresh_reviews docstring for why that's now safe).
"""

from __future__ import annotations

import asyncio

from app.modules.matching.regret_jobs import refresh_all_company_regret_profiles


async def _main() -> None:
    await refresh_all_company_regret_profiles()


def main() -> None:
    # refresh_all_company_regret_profiles is typed as scheduler.Job (a bare
    # Callable[[], Awaitable[None]]), not Coroutine -- asyncio.run wants a
    # real coroutine, hence this thin async wrapper rather than passing it
    # directly.
    asyncio.run(_main())


if __name__ == "__main__":
    main()
