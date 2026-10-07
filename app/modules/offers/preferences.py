"""A candidate's search preferences, as a filter over the offer pool.

Applied *before* the nearest-offers search (see repository.nearest) so that
the few offers a candidate is analysed on each day are picked among the ones
that fit what they asked for, instead of being picked nationwide and filtered
afterwards (Steve, 2026-10-07).

Every criterion is lenient about missing data, because the sources fill the
fields unevenly -- hiding an offer for lacking a field would silently drop a
large part of the pool:

- zone: offers with no région are kept when `include_unknown_region` is on,
  and a fully-remote offer is always kept (the offer isn't tied to a place);
- contract: an offer whose contract label is missing or not one of the six
  the app knows is kept;
- salary: an offer that states no salary is kept (and a freelance mission,
  whose pay is a day rate, is never judged on an annual salary);
- remote mode: Full remote comes from a stored flag, Hybride from a keyword
  heuristic over the text (core/remote_work.looks_hybrid), and Sur site is
  everything else -- the same three-way split as the Opportunités page.

Each rule exists twice, in SQL (`apply`, for the Postgres/pgvector path) and
in Python (`matches`, for the in-memory path and for the hybrid check SQL
cannot do). tests/modules/offers/test_preferences.py runs both over the same
offers and requires the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from app.core.contract_type import CONTRACT_LABEL_SUBSTRINGS
from app.core.remote_work import looks_hybrid
from app.modules.offers.models import JobOffer

REMOTE_MODES = ("Sur site", "Hybride", "Full remote")

_ALL_SUBSTRINGS = tuple(
    sub for subs in CONTRACT_LABEL_SUBSTRINGS.values() for sub in subs
)


def _contract_text(offer: JobOffer) -> str:
    return (offer.contract_type or "").lower()


def _like_any(column, substrings):  # type: ignore[no-untyped-def]
    return sa.or_(*[column.like(f"%{sub}%") for sub in substrings])


@dataclass(frozen=True)
class OfferPreferences:
    regions: tuple[str, ...] = ()
    include_unknown_region: bool = True
    contract_types: tuple[str, ...] = ()
    remote_modes: tuple[str, ...] = ()
    min_salary: int | None = None

    # -- helpers -------------------------------------------------------------

    @property
    def _remote_active(self) -> bool:
        modes = set(self.remote_modes)
        return bool(modes) and modes != set(REMOTE_MODES)

    @property
    def needs_python_remote(self) -> bool:
        """True when SQL alone cannot tell Hybride from Sur site, so the
        caller must also run `matches_remote` on what SQL returned (and ask
        for more rows, since some will be dropped)."""
        if not self._remote_active:
            return False
        modes = set(self.remote_modes)
        return modes not in ({"Full remote"}, {"Hybride", "Sur site"})

    @property
    def is_empty(self) -> bool:
        return not (
            self.regions
            or self.contract_types
            or self._remote_active
            or self.min_salary
        )

    # -- SQL -----------------------------------------------------------------

    def apply(self, stmt):  # type: ignore[no-untyped-def]
        """Adds the WHERE clauses that SQL can express to a select over
        JobOffer."""
        if self.regions:
            region_ok = [
                JobOffer.region.in_(list(self.regions)),  # type: ignore[attr-defined]
                JobOffer.is_full_remote.is_(True),  # type: ignore[attr-defined]
            ]
            if self.include_unknown_region:
                region_ok.append(JobOffer.region.is_(None))  # type: ignore[union-attr]
            stmt = stmt.where(sa.or_(*region_ok))

        if self.contract_types:
            label = sa.func.lower(sa.func.coalesce(JobOffer.contract_type, ""))
            wanted = [
                sub
                for contract in self.contract_types
                for sub in CONTRACT_LABEL_SUBSTRINGS.get(contract, ())
            ]
            stmt = stmt.where(
                sa.or_(
                    _like_any(label, wanted),
                    sa.not_(_like_any(label, _ALL_SUBSTRINGS)),
                )
            )

        if self.min_salary:
            label = sa.func.lower(sa.func.coalesce(JobOffer.contract_type, ""))
            stmt = stmt.where(
                sa.or_(
                    sa.and_(
                        JobOffer.salary_min.is_(None),  # type: ignore[union-attr]
                        JobOffer.salary_max.is_(None),  # type: ignore[union-attr]
                    ),
                    sa.func.coalesce(JobOffer.salary_max, JobOffer.salary_min)
                    >= self.min_salary,
                    _like_any(label, CONTRACT_LABEL_SUBSTRINGS["Freelance"]),
                )
            )

        if self._remote_active:
            modes = set(self.remote_modes)
            if "Full remote" not in modes:
                stmt = stmt.where(JobOffer.is_full_remote.is_(False))  # type: ignore[attr-defined]
            elif modes == {"Full remote"}:
                stmt = stmt.where(JobOffer.is_full_remote.is_(True))  # type: ignore[attr-defined]
        return stmt

    # -- Python --------------------------------------------------------------

    @staticmethod
    def remote_mode_of(offer: JobOffer) -> str:
        if offer.is_full_remote:
            return "Full remote"
        if looks_hybrid(offer.title, offer.description):
            return "Hybride"
        return "Sur site"

    def matches_remote(self, offer: JobOffer) -> bool:
        if not self._remote_active:
            return True
        return self.remote_mode_of(offer) in self.remote_modes

    def matches(self, offer: JobOffer) -> bool:
        if self.regions and not (
            offer.region in self.regions
            or offer.is_full_remote
            or (self.include_unknown_region and offer.region is None)
        ):
            return False

        if self.contract_types:
            text = _contract_text(offer)
            wanted = [
                sub
                for contract in self.contract_types
                for sub in CONTRACT_LABEL_SUBSTRINGS.get(contract, ())
            ]
            recognized = any(sub in text for sub in _ALL_SUBSTRINGS)
            if recognized and not any(sub in text for sub in wanted):
                return False

        if self.min_salary:
            states_salary = offer.salary_min is not None or offer.salary_max is not None
            top = offer.salary_max if offer.salary_max is not None else offer.salary_min
            is_freelance = any(
                sub in _contract_text(offer)
                for sub in CONTRACT_LABEL_SUBSTRINGS["Freelance"]
            )
            if states_salary and not is_freelance and (top or 0) < self.min_salary:
                return False

        return self.matches_remote(offer)
