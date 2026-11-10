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
* the first line of a candidate's description is the context of the job (the
  project, the product, the mission): it stays at the top of the experience,
  flagged `context`, so the PDF prints it as a description paragraph under the
  job title instead of a bullet.

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
# Words (not counting very short ones) in common with a context line for a
# bullet to be considered as coming from it.
CONTEXT_SIMILARITY = 0.5
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


def first_line(description: str | None) -> str:
    for raw in (description or "").splitlines():
        if raw.strip(_BULLET_MARKS):
            return raw.strip(_BULLET_MARKS)
    return ""


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


def pin_context_first(
    bullets: list[dict], original_description: str | None
) -> list[dict]:
    """The bullets, those coming from the first line of the original
    description first (in that line's own order), the others after them in
    their current order. Nothing is added or removed."""
    context = split_pieces(first_line(original_description))
    if not context or len(bullets) < 2:
        return bullets

    def best_piece(bullet: dict) -> int | None:
        scored = [
            (
                max(
                    similarity(bullet.get("text"), piece),
                    similarity(bullet.get("original_text"), piece),
                ),
                -index,
            )
            for index, piece in enumerate(context)
        ]
        score, neg_index = max(scored)
        return -neg_index if score >= CONTEXT_SIMILARITY else None

    pinned = [(best_piece(b), n, b) for n, b in enumerate(bullets)]
    head = sorted(
        ((piece, n, b) for piece, n, b in pinned if piece is not None),
        key=lambda item: (item[0], item[1]),
    )
    tail = [b for piece, _, b in pinned if piece is None]
    head_bullets = [b for _, _, b in head]
    if not tail:
        # Nothing follows: a lone sentence stays a bullet.
        head_bullets = list(head_bullets)
        return head_bullets
    if len(head_bullets) > 1:
        # The richest sentence of the context (the project itself) leads; the
        # shorter facts cut from the same line follow in their own order.
        lead = max(head_bullets, key=lambda b: len(_words(b.get("text"))))
        head_bullets = [lead] + [b for b in head_bullets if b is not lead]
    # Flagged so the CV prints them as the mission's description (a short
    # paragraph under the job title), like on the candidate's own CV, and not
    # as bullets.
    return [{**b, "context": True} for b in head_bullets] + tail
