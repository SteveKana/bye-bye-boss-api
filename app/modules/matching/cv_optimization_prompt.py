"""The CV-optimization prompt.

Backs "📄 Adapter mon CV pour cette offre" on the opportunity detail page --
a second, separate LLM call from the matching prompt (prompt.py), triggered
on demand by the candidate rather than run for every offer in the
background (see cv_optimization_service.py's docstring for why that's the
right call site for this one).

Takes the candidate's STRUCTURED experiences (not the raw CV text
prompt.py's matching call uses) precisely so the model can't add, drop, or
reorder whole experiences -- the frontend's side-by-side comparison pairs
output experiences with input experiences by position, so the count and
order must be preserved (see cv_optimization_service.py's pairing logic,
which also defends against the model drifting on this despite the prompt's
instruction).

The non-fabrication rule below is the same one already enforced everywhere
else in this app (see prompt.py's ETAPE 11, llm_schema's AtsGap) -- explicit
product decision (2026-09-22): a candidate-stated skill/tool not in the flat
`skills` list may still be surfaced if it's clearly implied by an
experience's own text (e.g. "animation des sprints" implies "Scrum"), but
nothing may be added on the strength of the offer alone.
"""

from __future__ import annotations

import json

PROMPT_TEMPLATE = """
Tu es un expert en rédaction de CV et en optimisation ATS (Applicant Tracking System).

OBJECTIF

À partir du CV structuré d'un candidat, d'une offre d'emploi précise, et d'une analyse
de compatibilité déjà calculée pour ce couple candidat/offre, produire une version du
CV optimisée pour CETTE offre précise : titre (headline), résumé professionnel, et
pour CHAQUE expérience déjà présente dans le CV, une liste de puces reformulées
mettant en valeur ce qui correspond à l'offre.

==================================================
RÈGLE ABSOLUE : NE JAMAIS INVENTER
==================================================

Interdiction totale d'ajouter une compétence, un outil, une responsabilité, un
diplôme, une certification ou une expérience qui n'a AUCUNE base réelle dans le CV
fourni. L'offre décrit ce qui est recherché, pas ce que le candidat a fait -- ne
jamais combler un écart en inventant.

Une compétence ou un outil ne peut être marqué "added" que s'il est clairement
déductible d'une phrase déjà présente dans le CV. Exemples de déductions valides :
  "animation des cérémonies Agile, sprint planning, daily" -> "Scrum" est déductible.
  "requêtes SQL pour analyser les indicateurs produit" -> "SQL" est déductible.
Exemple de déduction INVALIDE : l'offre demande "Power BI", rien dans le CV ne
mentionne d'outil de reporting -> ne pas ajouter "Power BI".
En cas de doute sur une déduction, ne pas l'ajouter.

Le nombre d'expériences en sortie doit être IDENTIQUE au nombre d'expériences en
entrée, dans le MÊME ordre, avec le même titre/entreprise/période -- seules les
puces à l'intérieur de chaque expérience peuvent changer.

==================================================
CE QUI PEUT CHANGER
==================================================

- Reformuler une puce existante ("modified") pour utiliser le vocabulaire de
  l'offre, sans changer le fait concret qu'elle décrit.
- Ajouter une puce ("added") uniquement si elle rend explicite quelque chose déjà
  réellement présent dans le CV mais qui n'était pas formulé comme une puce
  distincte (voir règle ci-dessus) -- jamais un fait nouveau.
- Le titre (headline) peut être reformulé pour refléter la spécialisation visée par
  l'offre, tant qu'il reste cohérent avec le poste réellement occupé.
- Le résumé professionnel peut être réécrit en mettant l'accent sur les éléments
  pertinents pour l'offre, à partir de faits réels du CV uniquement.
- Une puce peut rester "unchanged" si elle est déjà pertinente telle quelle.

Pour chaque puce "modified" ou "added", et pour le résumé (summary_why), justifier
brièvement et concrètement en quoi le changement aide pour CETTE offre précise --
jamais une justification générique interchangeable d'une offre à l'autre.

==================================================
CONTEXTE DÉJÀ CALCULÉ (base à utiliser, ne pas contredire)
==================================================

Une analyse de compatibilité a déjà été calculée pour ce candidat et cette offre
(écarts ATS, compétences demandées, actions suggérées). Elle a déjà appliqué la
même discipline de non-invention -- utilise-la comme guide prioritaire pour savoir
quoi mettre en valeur, sans la recopier telle quelle.

==================================================
FORMAT DE SORTIE
==================================================

Retourner UNIQUEMENT du JSON valide, sans aucun texte avant ou après, sans balises
Markdown.

{{
  "headline": "",
  "summary": "",
  "summary_why": "",
  "experiences": [
    {{"title": "", "company": "", "period": "", "bullets": [
      {{"text": "", "status": "unchanged | modified | added", "original_text": null, \
"why": ""}}
    ]}}
  ],
  "skills": [
    {{"skill": "", "added": false}}
  ],
  "advice": ""
}}

CV DU CANDIDAT (structuré)

{cv}

OFFRE

{offer}

ANALYSE DÉJÀ CALCULÉE POUR CE COUPLE CANDIDAT/OFFRE

{analysis}
"""


def build_prompt(cv: dict, offer: str, analysis: dict) -> str:
    return PROMPT_TEMPLATE.format(
        cv=json.dumps(cv, ensure_ascii=False, indent=2),
        offer=offer,
        analysis=json.dumps(analysis, ensure_ascii=False, indent=2),
    )
