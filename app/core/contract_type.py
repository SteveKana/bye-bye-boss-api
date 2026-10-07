"""Best-effort guess of a job posting's contract type from free text.

Adzuna's `contract_type`/`contract_time` fields (see
offers/providers/adzuna.py) are only as complete as whatever the original
job board it aggregated from provided -- for a lot of listings, Adzuna
simply has nothing in either field, so the frontend's contract-type tag
(useOfferDisplay.js's `contractTag`) has nothing to map and shows no tag at
all, unlike France Travail's `typeContratLibelle`, which is populated
almost every time.

This is a keyword heuristic, not a guarantee, same caveat as
core/remote_work.py's `looks_full_remote`: it can fail both ways. Wording
not covered here is a false negative (still just "no tag", no worse than
before this existed). A loose or ambiguous mention could in principle be a
false positive, though the patterns below require a fairly explicit,
contract-specific phrase rather than a bare word that could appear in
unrelated context.

Returned labels are the same vocabulary the frontend's `contractTag()`
already recognizes (CDI, CDD, Freelance, Stage, Alternance, Intérim), so
this needs no frontend change to take effect -- it just fills in a field
that would otherwise be empty.
"""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Stage",
        re.compile(r"\bstage\b|\bstagiaire\b|\binternship\b", re.IGNORECASE),
    ),
    (
        "Alternance",
        re.compile(
            r"\balternance\b|\balternant\b|\bapprentissage\b|"
            r"contrat\s+de\s+professionnalisation",
            re.IGNORECASE,
        ),
    ),
    (
        "Intérim",
        re.compile(r"int[ée]rim(aire)?", re.IGNORECASE),
    ),
    (
        "Freelance",
        re.compile(
            r"\bfreelance\b|\bind[ée]pendant\b|portage\s+salarial|"
            r"profession\s+lib[ée]rale|\btjm\b",
            re.IGNORECASE,
        ),
    ),
    (
        "CDD",
        re.compile(r"\bcdd\b|contrat\s+à\s+dur[ée]e\s+d[ée]termin[ée]e", re.IGNORECASE),
    ),
    (
        "CDI",
        re.compile(
            r"\bcdi\b|contrat\s+à\s+dur[ée]e\s+ind[ée]termin[ée]e|\bpermanent\b",
            re.IGNORECASE,
        ),
    ),
]


def guess_contract_type(*texts: str | None) -> str | None:
    """Returns the first recognized contract-type label found in the given
    texts (typically an offer's title and description), or None if nothing
    matches. Checked in order from most to least specific -- Stage and
    Alternance first, since "permanent"/"CDI" wording sometimes shows up in
    boilerplate ("poste permanent au sein de l'entreprise d'accueil") on
    what is actually an apprenticeship/internship listing."""
    combined = " ".join(text for text in texts if text)
    for label, pattern in _PATTERNS:
        if pattern.search(combined):
            return label
    return None


# --- Normalizing a source's raw label (for candidate preferences) -------------
#
# The sources' raw `contract_type` strings are free text ("CDI", "permanent,
# full_time", "Contrat à durée déterminée - 12 Mois"...). The front shows them
# through a substring mapping (composables/useOfferDisplay.js `contractTag`);
# the same mapping lives here so that filtering offers by a candidate's
# contract preferences, in SQL, agrees with the tag the candidate sees.
# Lowercase substrings, per label; accents are written both ways because SQL
# LIKE does no accent folding.
CONTRACT_LABEL_SUBSTRINGS: dict[str, tuple[str, ...]] = {
    "CDI": ("cdi", "permanent", "durée indéterminée"),
    "CDD": ("cdd", "durée déterminée"),
    "Intérim": ("intérim", "interim"),
    "Alternance": ("alternance", "apprentissage", "professionnalisation"),
    "Stage": ("stage", "internship"),
    "Freelance": (
        "freelance",
        "indépendant",
        "independant",
        "portage",
        "libérale",
        "contract",
    ),
}
CONTRACT_LABELS: tuple[str, ...] = tuple(CONTRACT_LABEL_SUBSTRINGS)


def contract_label_of(raw: str | None) -> str | None:
    """One of CONTRACT_LABELS for a raw source label, or None when the label is
    missing or not one the app recognizes. Same precedence as the front's
    `contractTag`."""
    if not raw:
        return None
    text = raw.lower()
    for label in ("CDI", "CDD", "Intérim", "Alternance", "Stage", "Freelance"):
        if any(sub in text for sub in CONTRACT_LABEL_SUBSTRINGS[label]):
            return label
    return None
