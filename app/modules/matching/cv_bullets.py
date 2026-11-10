"""Small, deterministic clean-ups applied to the bullets of an adapted CV.

The model is asked to follow these rules, but a CV is a document people send
to recruiters: a rule the model forgets once in a while must not show up in
the PDF. So the same rules are enforced here, in plain code, on what comes
back (and on what goes in):

* a line holding several facts glued with " | " is cut into one bullet per
  fact -- the " | " is how some CV extractions flatten a sentence, never
  something to print;
* the same fact is never printed twice (a reformulated bullet and the original
  one it came from, or a glued line and its own pieces);
* the sentences that describe the mission of the job (not a task) go on top,
  flagged `context`, so the PDF prints them as a description paragraph under
  the job title instead of bullets -- the model tells which they are, the code
  only checks its answer.

No model call, no database: pure text functions, easy to test.
"""

from __future__ import annotations

import re
import unicodedata

# Two bullets sharing at least this share of their words say the same thing.
DUPLICATE_SIMILARITY = 0.85
# A piece cut out of a glued line is dropped when this share of its words is
# already in another bullet.
PIECE_CONTAINMENT = 0.8
# What the model labels a bullet that describes the mission (see
# place_mission_first), the share of words a bullet must have in common with
# one of the candidate's first lines to be accepted as such, how many such lines
# are looked at and how many bullets may be descriptions.
MISSION_KIND = "mission"
MISSION_SIMILARITY = 0.35
MISSION_SOURCE_LINES = 2
MAX_MISSION_BULLETS = 3
MIN_PIECE_WORDS = 3

_BULLET_MARKS = " -•–—·*\t"
_PIPE = re.compile(r"\s*\|\s*")


def _words(text: str | None) -> set[str]:
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return {w for w in re.split(r"[^a-z0-9]+", plain) if len(w) >= 3}


def similarity(a: str | None, b: str | None) -> float:
    """Share of words in common (0..1) between two texts, accents and case
    ignored."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _contained(piece: str, other: str) -> float:
    wp, wo = _words(piece), _words(other)
    if len(wp) < MIN_PIECE_WORDS or not wo:
        return 0.0
    return len(wp & wo) / len(wp)


def split_pieces(text: str | None) -> list[str]:
    """`text` cut on " | " into its facts, each stripped of any bullet mark."""
    pieces = (p.strip(_BULLET_MARKS) for p in _PIPE.split(text or ""))
    return [p for p in pieces if p]


def description_lines(description: str | None) -> list[str]:
    """The lines of an experience description, one fact per line: cut on line
    breaks and on " | ", bullet marks removed, a fact already given dropped."""
    kept: list[str] = []
    for raw in (description or "").splitlines():
        for piece in split_pieces(raw):
            if not any(similarity(piece, k) >= DUPLICATE_SIMILARITY for k in kept):
                kept.append(piece)
    return kept


def first_lines(description: str | None, count: int) -> list[str]:
    lines = [raw.strip(_BULLET_MARKS) for raw in (description or "").splitlines()]
    return [line for line in lines if line][:count]


def clean_bullets(bullets: list[dict]) -> list[dict]:
    """The bullets with glued lines cut into facts and repeated facts removed
    (the first occurrence wins). Each bullet is a dict with at least "text"."""
    out: list[dict] = []
    for bullet in bullets:
        pieces = split_pieces(bullet.get("text"))
        glued = len(pieces) > 1
        for piece in pieces:
            repeated = any(
                similarity(piece, kept["text"]) >= DUPLICATE_SIMILARITY
                or (glued and _contained(piece, kept["text"]) >= PIECE_CONTAINMENT)
                for kept in out
            )
            if not repeated:
                out.append({**bullet, "text": piece})
    return out


def place_mission_first(
    bullets: list[dict], original_description: str | None
) -> list[dict]:
    """The sentences describing the MISSION of the job (the project, the
    product, the scope) moved on top and flagged `context`, so the PDF prints
    them as a short paragraph under the job title and keeps the bullets for the
    concrete tasks.

    Telling a description from a task is a judgment on the meaning of the
    sentence, so it is the model's (it labels each bullet `kind`: "mission" or
    "task"). The code only guards that judgment: a "mission" is accepted when it
    comes from the first lines of the candidate's own description (that is where
    a CV puts it), at most a few of them, and only when tasks follow. Anything
    else stays a plain bullet. `kind` itself never goes further."""
    source = [
        piece
        for line in first_lines(original_description, MISSION_SOURCE_LINES)
        for piece in split_pieces(line)
    ]

    def comes_from_source(bullet: dict) -> bool:
        return any(
            max(
                similarity(bullet.get("text"), piece),
                similarity(bullet.get("original_text"), piece),
            )
            >= MISSION_SIMILARITY
            for piece in source
        )

    accepted: list[int] = []
    for index, bullet in enumerate(bullets):
        said_mission = str(bullet.get("kind") or "").strip().lower() == MISSION_KIND
        if (
            said_mission
            and len(accepted) < MAX_MISSION_BULLETS
            and comes_from_source(bullet)
        ):
            accepted.append(index)
    if len(accepted) == len(bullets):
        accepted = []  # nothing but a description: a bullet list it stays

    plain = [{k: v for k, v in b.items() if k != "kind"} for b in bullets]
    head = [{**plain[i], "context": True} for i in accepted]
    return head + [b for i, b in enumerate(plain) if i not in accepted]
