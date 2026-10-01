"""Best-effort extraction of an annual salary from France Travail's
`salaire.libelle` free-text field.

Unlike Adzuna, France Travail never exposes salary_min/salary_max as
structured numbers -- only this label, e.g. "Annuel de 40000.0 à 48000.0
Euros", "Mensuel de 2500.0 Euros à 3000.0 Euros", "Horaire de 15.0 Euros à
18.0 Euros - TR, CSE". The leading period keyword (Annuel/Mensuel/Horaire)
says what the figures mean; everything after the numbers (TR, CSE, "sur 12
mois", ...) is noise we don't try to parse.

Same spirit and caveats as core/daily_rate.py:
  - A label phrased some other way is a false negative -- the offer simply
    keeps salary_min/max unset, same as one with no label at all. Never
    guessed.
  - Only the label is parsed, not the free-text description: unlike a TJM
    (which only ever shows up as prose), a permanent-role salary stated in
    the description is far more variable in wording and far more prone to
    false positives (years of experience, headcount, a budget figure for
    something else entirely) than this one structured field.

Only "Annuel" is used as-is; "Mensuel" and "Horaire" are annualized using
the same French-legal 151.67-paid-hours/month convention the frontend uses
the other way around to show a monthly/hourly estimate under the salary
slider (pages/opportunites.vue's MONTHLY_HOURS_35).
"""

from __future__ import annotations

import re

# A real French annual salary is virtually always within this range; a
# number outside it is more likely a mis-parse (a typo, a figure meant as
# something else) than a genuine salary.
_MIN_PLAUSIBLE_ANNUAL = 10_000
_MAX_PLAUSIBLE_ANNUAL = 500_000

_MONTHLY_HOURS_35 = 151.67

_PERIOD_TO_ANNUAL_MULTIPLIER = {
    "annuel": 1,
    "mensuel": 12,
    "horaire": _MONTHLY_HOURS_35 * 12,
}
_PERIOD = r"(Annuel|Mensuel|Horaire)"
# France Travail generates these figures from floats (e.g. "40000.0"), so a
# decimal part is always present, with either separator seen in practice.
_NUM = r"(\d+(?:[.,]\d+)?)"
_RANGE_SEP = r"(?:à|-|–)"

# "Euros" after the first figure is sometimes dropped (e.g. "Annuel de
# 40000.0 à 48000.0 Euros") -- only required once, at the end.
_RANGE = re.compile(
    rf"{_PERIOD}\s+de\s+{_NUM}\s*(?:Euros?)?\s*{_RANGE_SEP}\s*{_NUM}\s*Euros?",
    re.IGNORECASE,
)
_SINGLE = re.compile(
    rf"{_PERIOD}\s+de\s+{_NUM}\s*Euros?",
    re.IGNORECASE,
)


def _plausible(value: float) -> bool:
    return _MIN_PLAUSIBLE_ANNUAL <= value <= _MAX_PLAUSIBLE_ANNUAL


def extract_annual_salary(label: str | None) -> tuple[int | None, int | None]:
    """(salary_min, salary_max), annualized, parsed from a France Travail
    `salaire.libelle` string, or (None, None) if nothing plausible was
    found. A single value (no range stated) is returned as (value, value),
    same convention as core/daily_rate.py."""
    if not label:
        return None, None

    match = _RANGE.search(label)
    if match:
        period, low_raw, high_raw = match.groups()
        multiplier = _PERIOD_TO_ANNUAL_MULTIPLIER[period.lower()]
        low, high = sorted(
            float(raw.replace(",", ".")) * multiplier for raw in (low_raw, high_raw)
        )
        if _plausible(low) and _plausible(high):
            return round(low), round(high)
        return None, None

    match = _SINGLE.search(label)
    if match:
        period, value_raw = match.groups()
        multiplier = _PERIOD_TO_ANNUAL_MULTIPLIER[period.lower()]
        value = float(value_raw.replace(",", ".")) * multiplier
        if _plausible(value):
            return round(value), round(value)
        return None, None

    return None, None
