"""Prompt for turning a handful of Reddit mentions of one employer into a
Regret Index -- see regret_gateway.py and CompanyRegretProfile's docstring.

Kept deliberately conservative: the model is told to say "insufficient" if
the material doesn't actually support a judgment, rather than to always
produce a number. Regret scoring from a handful of forum posts is inherently
noisier than the CV<->offer matching prompt in prompt.py (which reads a full
CV against a full offer) -- there is no pretending otherwise here.
"""

from __future__ import annotations

_MAX_ITEMS = 15
_MAX_CHARS_PER_ITEM = 600


def _format_mentions(mentions: list[dict]) -> str:
    blocks = []
    for i, m in enumerate(mentions[:_MAX_ITEMS], start=1):
        text = f"{m.get('title', '')}\n{m.get('body', '')}".strip()
        text = text[:_MAX_CHARS_PER_ITEM]
        blocks.append(f"[{i}] {text}")
    return "\n\n".join(blocks)


def build_regret_prompt(company_name: str, mentions: list[dict]) -> str:
    # Long lines below are wrapped with backslash line-continuations purely
    # to satisfy the 88-column lint limit (same convention as prompt.py) --
    # backslash-newline is removed by Python's string parser, so the
    # wrapping is invisible to the model.
    return f"""Tu analyses des messages Reddit mentionnant l'entreprise \
"{company_name}" pour estimer un "indice de regret" : le risque qu'un \
candidat regrette d'avoir rejoint cette entreprise, du point de vue d'un \
salarié ou ex-salarié.

MESSAGES REDDIT (peuvent être hors-sujet, ironiques, ou ne pas concerner \
directement l'expérience employé -- à toi de juger) :
{_format_mentions(mentions)}

RÈGLES STRICTES :
- N'utilise QUE les messages ci-dessus. N'invente rien, ne complète pas \
avec des connaissances générales sur l'entreprise.
- Si moins de 3 messages parlent réellement d'une expérience de travail \
dans cette entreprise (management, ambiance, conditions, turn-over, \
promesses non tenues...), réponds "insufficient" -- un score basé sur du \
bruit est pire que pas de score.
- Le score va de 0 (aucun signal de regret, avis plutôt positifs) à 100 \
(signal de regret très fort, avis très négatifs). La moyenne attendue \
pour une entreprise "normale" tourne autour de 30-40, pas 50 -- ne centre \
pas artificiellement.
- 2 à 4 raisons courtes (une phrase chacune), uniquement si elles sont \
explicitement présentes dans les messages.

Réponds en JSON strict, rien d'autre :
{{
  "availability": "available" | "insufficient",
  "score": <entier 0-100, uniquement si availability == "available", sinon null>,
  "reasons": [<0 à 4 phrases courtes>, uniquement si "available", sinon []]
}}"""
