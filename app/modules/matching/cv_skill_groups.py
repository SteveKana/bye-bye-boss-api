"""Groups the skills of an adapted CV under short headings for the PDF.

A flat line of fifty skills is hard to scan: a recruiter wants "Data & BI",
"Méthodes Agile"... like on a real CV. Each skill's heading comes from the
model when it gave one (see CVOptimizationSkill.category -- it also puts the
most relevant heading first); a skill without one gets a heading from the
keyword table below, which covers the usual IT / product vocabulary; what
matches nothing goes under "Autres compétences", last.

When everything ends up under a single heading, grouping would add nothing,
so the caller gets the plain list back (empty heading).
"""

from __future__ import annotations

import re
import unicodedata

from app.modules.matching.skill_labels import clean_skill_label

OTHER_HEADING = "Autres compétences"

# (heading, keywords). First heading whose keyword matches a whole word (or
# word start for the "stem" entries ending in *) wins, so order matters: a
# "user story" is Agile before it is "rédaction".
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Data & BI",
        (
            "sql",
            "bi",
            "power bi",
            "kibana",
            "dynatrace",
            "knime",
            "etl",
            "bigquery",
            "excel",
            "python",
            "tableau",
            "looker",
            "donnee*",
            "data",
            "modelisation",
            "analyse*",
        ),
    ),
    (
        "Méthodes Agile",
        (
            "scrum",
            "kanban",
            "safe",
            "agile",
            "icescrum",
            "trello",
            "draft",
            "jira",
            "confluence",
            "backlog",
            "user stor*",
            "sprint",
            "roadmap",
            "priorisation",
            "product owner",
            "azure devops",
        ),
    ),
    (
        "Langages & outils",
        (
            "html*",
            "css*",
            "dsfr",
            "javascript",
            "php",
            "laravel",
            "java",
            "vs code",
            "postman",
            "oracle",
            "gcp",
            "google cloud",
            "api",
            "xml",
            "flutter",
            "git",
            "docker",
            "algolia",
            "adobe xd",
            "canva",
            "sap",
            "bus",
        ),
    ),
    (
        "Pilotage de projet",
        (
            "pilotage",
            "recueil",
            "specification*",
            "recette",
            "production",
            "conduite",
            "planning",
            "jalon*",
            "risque*",
            "migration",
            "cadrage",
            "coproj",
            "gestion",
            "coordination",
            "besoin*",
            "reporting",
        ),
    ),
)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _matches(plain_name: str, keyword: str) -> bool:
    if keyword.endswith("*"):
        return re.search(rf"\b{re.escape(keyword[:-1])}", plain_name) is not None
    return re.search(rf"\b{re.escape(keyword)}\b", plain_name) is not None


def keyword_heading(skill: str) -> str:
    """Heading for a skill from the keyword table, "" when nothing matches."""
    plain = _plain(skill)
    for heading, keywords in _KEYWORDS:
        if any(_matches(plain, keyword) for keyword in keywords):
            return heading
    return ""


def group_skills(skills: list[dict]) -> list[tuple[str, list[str]]]:
    """[(heading, [skill, ...]), ...] in order of first appearance, the
    "other" heading last; [("", [all skills])] when grouping is pointless."""
    groups: dict[str, list[str]] = {}
    everything: list[str] = []
    for item in skills:
        name = clean_skill_label(str(item.get("skill") or "")).strip()
        if not name:
            continue
        everything.append(name)
        heading = (
            str(item.get("category") or "").strip()
            or keyword_heading(name)
            or OTHER_HEADING
        )
        bucket = groups.setdefault(heading, [])
        if name.lower() not in (n.lower() for n in bucket):
            bucket.append(name)
    if len(groups) < 2:
        return [("", everything)] if everything else []
    ordered = [(h, names) for h, names in groups.items() if h != OTHER_HEADING]
    if OTHER_HEADING in groups:
        ordered.append((OTHER_HEADING, groups[OTHER_HEADING]))
    return ordered
