"""Readable skill names -- the pure helpers (no database, no LLM).

The matching analysis names skills with internal snake_case concepts
("roadmap_planning") on purpose: that is what lets an offer's requirements be
matched against the CV's skills (see prompt.py's ETAPE 1/2). Candidates should
never read them like that, so every skill item sent to the frontend gets a
`label` next to its `skill`: the French wording from the shared glossary
(skill_labels_service.py) when it has one, a plain underscore-free version
otherwise.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping

# The analysis lists whose items carry a `skill` name.
SKILL_LISTS = ("job_skills", "matches", "ats_gaps", "blocking_requirements")


def clean_skill_label(name: str) -> str:
    """Safety net behind the prompt's "libellés en français naturel" rule: a
    label that still looks like an internal snake_case identifier
    ("roadmap_planning") gets its underscores turned into spaces and a
    capital first letter. Anything else (SQL Server, API REST...) is left
    exactly as is."""
    name = name.strip()
    if "_" not in name:
        return name
    spaced = " ".join(name.replace("_", " ").split())
    return spaced[:1].upper() + spaced[1:]


def needs_label(key: str) -> bool:
    """True for an internal concept ("roadmap_planning", "agile") -- a single
    lowercase token -- as opposed to a name that is already written for
    humans ("SQL Server", "Gestion de projet")."""
    return bool(key) and " " not in key and key == key.lower()


def collect_keys(analysis: Mapping) -> set[str]:
    keys: set[str] = set()
    for field in SKILL_LISTS:
        for item in analysis.get(field) or []:
            if isinstance(item, Mapping):
                skill = item.get("skill")
                if isinstance(skill, str) and needs_label(skill.strip()):
                    keys.add(skill.strip())
    return keys


def apply_labels(analysis: dict, labels: Mapping[str, str]) -> dict:
    """A copy of `analysis` where every skill item has a `label`. Never
    mutates the stored analysis (the `skill` key must stay the internal one:
    it is what the offer/CV matching is built on)."""
    if not analysis:
        return analysis
    result = copy.deepcopy(analysis)
    for field in SKILL_LISTS:
        for item in result.get(field) or []:
            if not isinstance(item, dict):
                continue
            skill = item.get("skill")
            if not isinstance(skill, str) or not skill.strip():
                continue
            skill = skill.strip()
            item["label"] = labels.get(skill) or clean_skill_label(skill)
    return result


def unique_keys(analyses: Iterable[Mapping]) -> set[str]:
    keys: set[str] = set()
    for analysis in analyses:
        keys |= collect_keys(analysis)
    return keys
