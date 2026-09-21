"""The 18 French régions, and ways to derive one from what a job-offer
source actually gives us.

Kept in `core` rather than `offers` or `cv` because both need it: `offers`
derives each ingested offer's région from its raw postal-code/breadcrumb
data (see providers/adzuna.py and providers/france_travail.py), `cv` already
has its own hand-picked `MobilityRegion` Literal (app/modules/cv/schemas.py)
for the candidate's own preference dropdown, and `matching` compares the
two (see matching/geo_filter.py). Putting it in `core` avoids `offers`
having to depend on `cv` (or vice versa) just for a list of 18 names --
see the module dependency graph in app/modules/*/__init__.py.

FRENCH_REGIONS below must stay in sync with cv/schemas.py's MobilityRegion
Literal -- both list the same 18 post-2016 régions (13 metropolitan + 5
overseas), just in two different shapes (a runtime list here vs. a static
Literal there for request-body validation). France last redrew région
boundaries in 2016; this isn't expected to change again soon.
"""

from __future__ import annotations

import re

from app.core.text import fold

FRENCH_REGIONS: tuple[str, ...] = (
    "Auvergne-Rhône-Alpes",
    "Bourgogne-Franche-Comté",
    "Bretagne",
    "Centre-Val de Loire",
    "Corse",
    "Grand Est",
    "Hauts-de-France",
    "Île-de-France",
    "Normandie",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Pays de la Loire",
    "Provence-Alpes-Côte d'Azur",
    "Guadeloupe",
    "Martinique",
    "Guyane",
    "La Réunion",
    "Mayotte",
)

# French administrative département code -> région, post-2016 boundaries.
# Corse's two départements (2A Corse-du-Sud, 2B Haute-Corse) both map to the
# same région, so postal codes -- which use "20" for both, unlike INSEE
# codes' "2A"/"2B" -- resolve unambiguously too. Overseas codes are the
# 3-digit "97x" prefix shared by postal and INSEE codes alike. Guyane's own
# code is "973", Mayotte's is "976" -- 975 (Saint-Pierre-et-Miquelon) and
# 977/978 (Saint-Barthélemy/Saint-Martin) are COMs, not régions, and are
# deliberately absent: an offer located there has no région to report.
_DEPARTMENT_TO_REGION: dict[str, str] = {
    # Auvergne-Rhône-Alpes
    "01": "Auvergne-Rhône-Alpes",
    "03": "Auvergne-Rhône-Alpes",
    "07": "Auvergne-Rhône-Alpes",
    "15": "Auvergne-Rhône-Alpes",
    "26": "Auvergne-Rhône-Alpes",
    "38": "Auvergne-Rhône-Alpes",
    "42": "Auvergne-Rhône-Alpes",
    "43": "Auvergne-Rhône-Alpes",
    "63": "Auvergne-Rhône-Alpes",
    "69": "Auvergne-Rhône-Alpes",
    "73": "Auvergne-Rhône-Alpes",
    "74": "Auvergne-Rhône-Alpes",
    # Bourgogne-Franche-Comté
    "21": "Bourgogne-Franche-Comté",
    "25": "Bourgogne-Franche-Comté",
    "39": "Bourgogne-Franche-Comté",
    "58": "Bourgogne-Franche-Comté",
    "70": "Bourgogne-Franche-Comté",
    "71": "Bourgogne-Franche-Comté",
    "89": "Bourgogne-Franche-Comté",
    "90": "Bourgogne-Franche-Comté",
    # Bretagne
    "22": "Bretagne",
    "29": "Bretagne",
    "35": "Bretagne",
    "56": "Bretagne",
    # Centre-Val de Loire
    "18": "Centre-Val de Loire",
    "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire",
    "37": "Centre-Val de Loire",
    "41": "Centre-Val de Loire",
    "45": "Centre-Val de Loire",
    # Corse
    "20": "Corse",
    "2A": "Corse",
    "2B": "Corse",
    # Grand Est
    "08": "Grand Est",
    "10": "Grand Est",
    "51": "Grand Est",
    "52": "Grand Est",
    "54": "Grand Est",
    "55": "Grand Est",
    "57": "Grand Est",
    "67": "Grand Est",
    "68": "Grand Est",
    "88": "Grand Est",
    # Hauts-de-France
    "02": "Hauts-de-France",
    "59": "Hauts-de-France",
    "60": "Hauts-de-France",
    "62": "Hauts-de-France",
    "80": "Hauts-de-France",
    # Île-de-France
    "75": "Île-de-France",
    "77": "Île-de-France",
    "78": "Île-de-France",
    "91": "Île-de-France",
    "92": "Île-de-France",
    "93": "Île-de-France",
    "94": "Île-de-France",
    "95": "Île-de-France",
    # Normandie
    "14": "Normandie",
    "27": "Normandie",
    "50": "Normandie",
    "61": "Normandie",
    "76": "Normandie",
    # Nouvelle-Aquitaine
    "16": "Nouvelle-Aquitaine",
    "17": "Nouvelle-Aquitaine",
    "19": "Nouvelle-Aquitaine",
    "23": "Nouvelle-Aquitaine",
    "24": "Nouvelle-Aquitaine",
    "33": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine",
    "47": "Nouvelle-Aquitaine",
    "64": "Nouvelle-Aquitaine",
    "79": "Nouvelle-Aquitaine",
    "86": "Nouvelle-Aquitaine",
    "87": "Nouvelle-Aquitaine",
    # Occitanie
    "09": "Occitanie",
    "11": "Occitanie",
    "12": "Occitanie",
    "30": "Occitanie",
    "31": "Occitanie",
    "32": "Occitanie",
    "34": "Occitanie",
    "46": "Occitanie",
    "48": "Occitanie",
    "65": "Occitanie",
    "66": "Occitanie",
    "81": "Occitanie",
    "82": "Occitanie",
    # Pays de la Loire
    "44": "Pays de la Loire",
    "49": "Pays de la Loire",
    "53": "Pays de la Loire",
    "72": "Pays de la Loire",
    "85": "Pays de la Loire",
    # Provence-Alpes-Côte d'Azur
    "04": "Provence-Alpes-Côte d'Azur",
    "05": "Provence-Alpes-Côte d'Azur",
    "06": "Provence-Alpes-Côte d'Azur",
    "13": "Provence-Alpes-Côte d'Azur",
    "83": "Provence-Alpes-Côte d'Azur",
    "84": "Provence-Alpes-Côte d'Azur",
    # Overseas
    "971": "Guadeloupe",
    "972": "Martinique",
    "973": "Guyane",
    "974": "La Réunion",
    "976": "Mayotte",
}

# A handful of common alternate spellings/abbreviations seen in free text
# (accents already stripped by `fold` before lookup, so only the
# non-accent variation needs listing here).
_ALIASES: dict[str, str] = {
    "paca": "Provence-Alpes-Côte d'Azur",
    "ile de france": "Île-de-France",
    "idf": "Île-de-France",
    "outre mer": "",  # not a région on its own -- deliberately unresolved
}

_FOLDED_REGIONS: dict[str, str] = {fold(name): name for name in FRENCH_REGIONS}


def region_from_postal_code(postal_code: str | None) -> str | None:
    """France Travail's `lieuTravail.codePostal` -> région, or None if the
    code is missing/unrecognized. Corse's two départements share the "20"
    postal prefix, which maps unambiguously to "Corse" regardless."""
    digits = re.sub(r"\D", "", postal_code or "")
    if len(digits) < 2:
        return None
    dept = digits[:3] if digits.startswith("97") else digits[:2]
    return _DEPARTMENT_TO_REGION.get(dept)


def region_from_insee_code(insee_code: str | None) -> str | None:
    """A commune's INSEE code (France Travail's `lieuTravail.commune`) ->
    région. Unlike postal codes, INSEE codes spell Corse's départements out
    as "2A"/"2B" rather than "20"."""
    code = (insee_code or "").strip().upper()
    if len(code) < 2:
        return None
    if code[:2] in ("2A", "2B"):
        dept = code[:2]
    elif code.startswith("97"):
        dept = code[:3]
    else:
        dept = code[:2]
    return _DEPARTMENT_TO_REGION.get(dept)


def normalize_region_name(raw: str | None) -> str | None:
    """A free-text région name (e.g. one element of Adzuna's
    `location.area` breadcrumb) -> the matching canonical name in
    FRENCH_REGIONS, or None if it isn't one (most breadcrumb elements are a
    country, a région, a département or a city -- only one of them is
    ever a région)."""
    folded = fold(raw)
    if not folded:
        return None
    if folded in _ALIASES:
        return _ALIASES[folded] or None
    return _FOLDED_REGIONS.get(folded)
