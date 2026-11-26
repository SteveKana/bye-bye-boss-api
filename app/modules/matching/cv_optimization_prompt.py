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

Déductions ÉVIDENTES : à accepter. Un élément qui accompagne presque toujours, dans la
pratique, ce que le CV décrit déjà (quelqu'un qui a fait cela l'a forcément ou presque
toujours manipulé) peut être ajouté ("added"). Exemples valides :
  "API REST" -> "JSON" ; "flux d'ingestion multi-sources" -> "mapping de données" ;
  "rédaction de user stories" -> "critères d'acceptation" ;
  "tableaux de bord Power BI" -> "visualisation de données".
Ce qui n'est PAS une déduction évidente : un outil ou une technologie distincte, même
voisine ou demandée par l'offre (Kafka, API Management...) -- un lien thématique ne
suffit pas. Le test à appliquer : « une personne qui a réellement fait ce que décrit le
CV a-t-elle presque forcément utilisé cet élément ? » Si oui, l'ajouter ; sinon, non. Le
doute ne porte que sur ce test, pas sur l'idée d'oser déduire.

Libellés des compétences (champ "skills") : chaque compétence ajoutée ("added") est
écrite en français naturel, comme sur un vrai CV, avec des espaces et une majuscule
initiale -- par exemple "Définition des critères d'acceptation", "Planification de
la roadmap", "Collaboration avec l'UX". JAMAIS d'underscore ni de snake_case : l'analyse
déjà calculée ci-dessous contient parfois des identifiants techniques de ce type
(comme "roadmap_planning") qui sont des étiquettes internes, à ne jamais recopier
tels quels. Les noms propres d'outils, de méthodes ou de technologies restent
inchangés (SQL, Scrum, Jira, API REST, Power BI...).

AUCUNE puce du CV d'origine ne doit disparaître : chacune doit réapparaître, soit
telle quelle ("unchanged"), soit reformulée ("modified", avec son "original_text").
Ne pas fusionner plusieurs puces en une seule, ne pas en omettre parce qu'elle
semble éloignée de l'offre.

Deux sortes de lignes dans une expérience, à distinguer pour chaque puce avec le champ
"kind" :
- "mission" : une phrase qui DÉCRIT la mission ou le contexte du poste (le projet, le
  produit, le périmètre, l'objectif général), pas une action précise. Exemples :
  « Refonte de la plateforme de paiement d'un site e-commerce », « Mise en place d'un
  nouveau système de facturation au sein d'une équipe projet ». Elle est en général au
  début de l'expérience, avant la liste des tâches. Il y en a une ou deux au plus par
  expérience, et parfois aucune.
- "task" : une action concrète réalisée (rédiger, animer, concevoir, suivre, tester...).
  Exemples : « Rédaction des spécifications », « Animation des ateliers ».
Les phrases "mission" sont placées EN PREMIER dans l'expérience, reformulées si utile en
phrases complètes (elles seront imprimées comme un court paragraphe sous le titre du
poste, pas comme des puces). Viennent ensuite les tâches, les plus pertinentes pour
l'offre d'abord. En cas de doute, "task".

Compétences (champ "skills") : reprendre TOUTES les compétences du CV, sans en
retirer aucune, puis les éventuelles compétences "added". Chaque compétence porte un
champ "category" : un intitulé court de rubrique (3 à 5 rubriques au total, par
exemple « Data & BI », « Méthodes Agile », « Outils et langages », « Pilotage de
projet »), le même intitulé pour les compétences de la même famille. Classer les
compétences de la plus à la moins pertinente pour l'offre : la rubrique la plus utile
pour cette offre vient donc en premier.

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

Mise en forme des puces (le CV part tel quel chez un recruteur) : une puce = UN seul
fait, jamais le caractère « | » dans une puce, et le même fait n'apparaît jamais deux
fois dans une même expérience.

Pour chaque puce "modified" ou "added", et pour le résumé (summary_why), justifier
brièvement et concrètement en quoi le changement aide pour CETTE offre précise --
jamais une justification générique interchangeable d'une offre à l'autre.

==================================================
CONTEXTE DÉJÀ CALCULÉ (guide prioritaire)
==================================================

Une analyse de compatibilité a déjà été calculée pour ce candidat et cette offre
(écarts ATS, compétences demandées, actions suggérées). Elle a déjà appliqué la
même discipline de non-invention -- utilise-la comme guide prioritaire pour savoir
quoi mettre en valeur, sans la recopier telle quelle.

Cette analyse est plus prudente que la règle des déductions évidentes ci-dessus : un
élément qu'elle classe comme manquant ou non étayé doit quand même être ajouté s'il est
une déduction évidente de ce que le CV décrit. Les autres écarts restent des écarts.

Le champ "advice" (2 à 4 phrases, texte brut, au vouvoiement, propre à CETTE offre) se
rédige EN DERNIER, une fois les puces et les compétences décidées : il ne signale comme
manquant, et ne déconseille d'ajouter, que ce que le CV de sortie ne contient pas et
qui n'est pas une déduction évidente. Un élément que tu viens d'ajouter, ou qui est
une déduction évidente, n'y est jamais présenté comme absent. Il ne pousse jamais à
inventer.

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
"why": "", "kind": "mission | task"}}
    ]}}
  ],
  "skills": [
    {{"skill": "", "added": false, "category": ""}}
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


NO_ANALYSIS_TEXT = (
    "Aucune analyse préalable pour cette offre (texte collé à la main). "
    "Déduis toi-même, à partir de l'offre, ce qu'il faut mettre en valeur, avec "
    "exactement les mêmes règles de non-invention que ci-dessus."
)


# Added to the prompt (before the data) when the CV must come out in the
# language of the offer -- the free CV adaptation for visitors, whose CV may
# be in one language and the offer in another. Written without curly braces:
# it is spliced into PROMPT_TEMPLATE before .format().
LANGUAGE_RULES = """\
==================================================
LANGUE DU CV : CELLE DE L'OFFRE
==================================================

Repère la langue dans laquelle l'OFFRE est rédigée. Le CV de sortie est rédigé dans
CETTE langue, même si le CV d'origine est dans une autre.

- Titre (headline), résumé, intitulés de poste, texte des puces, libellés et rubriques
  des compétences : dans la langue de l'offre. Traduction fidèle : mêmes faits, mêmes
  chiffres, même niveau de responsabilité ; rien n'est ajouté ni retiré à cause de la
  traduction. Les règles de non-invention ci-dessus s'appliquent à l'identique.
- Les noms propres (entreprises, outils, technologies, méthodes comme Scrum, SQL,
  Power BI) restent inchangés.
- Une puce simplement traduite, sans autre reformulation, garde le statut "unchanged",
  mais son "original_text" contient la puce d'origine recopiée à l'identique : c'est ce
  qui permet de la rapprocher de l'original. Une puce reformulée en plus de la
  traduction est "modified" (avec son "original_text"), comme d'habitude.
- Chaque compétence reçoit un champ "original" : son libellé EXACT dans le CV d'origine
  (chaîne vide pour une compétence "added"). Son champ "skill" est le libellé dans la
  langue de l'offre.
- "formations" : les formations du CV d'origine, dans le même nombre et le même ordre,
  avec les intitulés de diplôme dans la langue de l'offre ; noms d'écoles et dates
  inchangés. "languages" : les langues parlées du CV d'origine, dans le même nombre et
  le même ordre, nom de la langue et niveau écrits dans la langue de l'offre.
- Si le CV d'origine est déjà dans la langue de l'offre, rien ne change : recopie.
- "summary_why", les "why" et "advice" restent en FRANÇAIS : ils s'adressent au
  candidat, qui lit le site en français.
- Ajoute la clé "language" : le code ISO 639-1 de la langue de l'offre ("fr", "en",
  "es", "de", "it", "pt", "nl"...).

Dans le JSON de sortie, ajoute aux clés ci-dessous : "language" (texte), "formations"
(liste de titre et school_period), "languages" (liste de name et level), et "original"
dans chaque compétence.

"""

_DATA_MARKER = "CV DU CANDIDAT (structuré)"


def build_prompt(
    cv: dict,
    offer: str,
    analysis: dict | None,
    *,
    offer_language: bool = False,
) -> str:
    """`analysis` is None when the offer is a pasted text with no match
    computed for it (admin tool): the prompt then says so instead of showing
    an empty analysis. `offer_language` adds the rules that make the CV come
    out in the language of the offer."""
    template = PROMPT_TEMPLATE
    if offer_language:
        template = template.replace(_DATA_MARKER, LANGUAGE_RULES + _DATA_MARKER, 1)
    return template.format(
        cv=json.dumps(cv, ensure_ascii=False, indent=2),
        offer=offer,
        analysis=(
            NO_ANALYSIS_TEXT
            if analysis is None
            else json.dumps(analysis, ensure_ascii=False, indent=2)
        ),
    )


ADVICE_PROMPT_TEMPLATE = """
Tu es un expert en rédaction de CV et en recrutement.

Un candidat adapte son CV à une offre précise, et vient de MODIFIER lui-même son CV
(ajout ou retrait de puces, de compétences, de certifications, de langues...). Tu dois
rédiger de nouveau le "Conseil" affiché sous le CV, à partir du CV TEL QU'IL EST
MAINTENANT -- il remplace l'ancien conseil, qui ne tenait pas compte de ces
modifications.

RÈGLES

- Ce qui figure DÉJÀ dans le CV modifié (y compris ce que le candidat vient d'ajouter)
  ne doit plus être signalé comme manquant.
- Ne signale pas non plus comme manquant ce qui est une déduction évidente de ce que le
  CV décrit déjà (un élément que la personne a presque forcément utilisé pour faire ce
  qui est écrit : "JSON" avec "API REST", "mapping de données" avec "flux d'ingestion
  multi-sources"...).
- Signale ce que l'offre exige encore et que le CV ne documente toujours pas (hors
  déductions évidentes), et mets en garde contre le fait de revendiquer ces éléments
  s'ils ne correspondent à aucune expérience ou qualification réelle. Ne jamais inviter
  à inventer.
- Si le CV couvre bien l'essentiel de l'offre, dis-le simplement et indique ce qui
  pourrait encore être précisé.
- 2 à 4 phrases, concrètes, propres à CETTE offre. Vouvoie le candidat, comme dans les
  conseils précédents. Texte brut : pas de liste, pas de Markdown.

Retourner UNIQUEMENT du JSON valide, sans texte avant ou après, sans balises Markdown :

{{"advice": ""}}

OFFRE

{offer}

CV DU CANDIDAT (version actuelle, modifiée)

{cv}
"""


def build_advice_prompt(cv: dict, offer: str) -> str:
    return ADVICE_PROMPT_TEMPLATE.format(
        cv=json.dumps(cv, ensure_ascii=False, indent=2), offer=offer
    )
