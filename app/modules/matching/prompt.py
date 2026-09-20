"""The matching prompt.

Kept verbatim from the standalone `matchcareer_engine` prototype -- the
business logic (semantic matching, experience-from-dates, hard/medium/soft
blocker thresholds) lives here and was validated against real model output
during prototyping. Only the Regret Index is intentionally NOT asked of the
LLM: MatchCareer's regret scoring is deferred (see the `matching` module's
docstring), so no review-derived score is requested here at all.

Long lines below are wrapped with backslash line-continuations purely to
satisfy the 88-column lint limit -- verified byte-for-byte equal to the
original unwrapped prompt text (backslash-newline is removed by Python's
string parser, so the wrapping is invisible to the model).
"""

from __future__ import annotations

PROMPT_TEMPLATE = """
Tu es un moteur expert de matching carrière, compatibilité ATS et optimisation de CV.

OBJECTIF

Analyser un CV et une offre d'emploi afin de :

1. Mesurer la compatibilité carrière.
2. Mesurer la compatibilité ATS.
3. Estimer le potentiel d'amélioration ATS.
4. Identifier les critères bloquants.
5. Identifier les compétences transférables.
6. Expliquer les écarts.
7. Proposer des améliorations concrètes du CV.

==================================================
PRINCIPE FONDAMENTAL
====================

Le matching doit être sémantique.
Ne pas se limiter aux mots exacts.

Exemples :
Scrum ≈ Agile
Recueil du besoin ≈ Expression du besoin
Animation d'ateliers ≈ Ateliers métier
User Stories ≈ Formalisation des besoins fonctionnels
Backlog ≈ Gestion du backlog
Conduite du changement ≈ Accompagnement utilisateurs
Pilotage produit ≈ Product Ownership

Une compétence présente sous une formulation différente doit être reconnue.

==================================================
ETAPE 1 : EXTRACTION DES COMPETENCES DU CV
==========================================

Extraire : compétences, responsabilités, outils, méthodologies, secteurs,
formations, certifications, expériences.
Normaliser chaque élément sous forme de concepts (jamais de phrases complètes).
Exemples :
"Recueil du besoin" -> requirements_gathering
"Animation d'ateliers" -> workshop_facilitation
"User Stories" -> user_story
"Priorisation du backlog" -> backlog_management
"Agile Scrum" -> agile
"Pilotage projet" -> project_management

==================================================
ETAPE 2 : EXTRACTION DES EXIGENCES DE L'OFFRE
=============================================

Extraire : compétences, responsabilités, outils, formations, expériences,
certifications. Normaliser sous forme de concepts.
Pour chaque exigence : skill, category, importance.

==================================================
ETAPE 3 : CLASSIFICATION DES EXIGENCES PAR IMPORTANCE
======================================================

Importance possible : required, preferred, nice_to_have.
required : requis, obligatoire, indispensable, impératif, essentiel, minimum requis, \
doit posséder.
preferred : de préférence, apprécié, souhaité, idéalement, fortement apprécié.
nice_to_have : bonus, atout, serait un plus.

Une exigence preferred ou nice_to_have ne doit jamais être placée dans \
blocking_requirements.
Seules les exigences required peuvent apparaître dans blocking_requirements.

==================================================
ETAPE 4 : CALCUL DE L'EXPERIENCE
================================

Calculer l'expérience à partir des dates (jamais uniquement une phrase "X ans").
Méthode : identifier chaque expérience, extraire début et fin, calculer la durée,
identifier les expériences pertinentes, additionner les durées pertinentes.
Une expérience ne doit pas être considérée absente si elle peut être déduite
des missions et des dates.

==================================================
ETAPE 5 : CLASSIFICATION DES CRITERES BLOQUANTS
================================================

S'applique uniquement aux exigences required absentes ou insuffisantes.

5A. CRITÈRES QUALITATIFS
hard_blocker si au moins une condition est vraie :
  1. LANGUE indispensable, usage quotidien écrit et oral.
  2. DIPLÔME RÉGLEMENTAIRE exigé par la loi ou le client.
  3. CERTIFICATION CONTRACTUELLE requise pour l'appel d'offres ou la mission.
  4. HABILITATION / AUTORISATION administrative non substituable.
  5. FORMULATION ÉLIMINATOIRE EXPLICITE ("éliminatoire", "sine qua non"...).
soft_blocker si required mais aucune condition hard_blocker n'est remplie
(secteur, type de produit, outils non réglementaires, environnement particulier).

5B. CRITÈRES QUANTITATIFS (années, niveau de langue chiffré, niveau de diplôme)
1. Calculer l'écart entre requis et réel. 2. Exprimer en pourcentage du requis.
Écart ≤ 15%            -> soft_blocker
Écart entre 15% et 40% -> medium_blocker
Écart > 40%            -> hard_blocker
Valeur réelle = 0      -> hard_blocker systématique

5C. RÈGLE DE COHÉRENCE OBLIGATOIRE
blocking_requirements, blocking_message, ATS Score et ATS Potential doivent être \
cohérents.
Un hard_blocker pèse plus que tous les preferred + nice_to_have réunis et ne peut être \
compensé.
Un medium_blocker pèse significativement mais peut être partiellement compensé.
Un soft_blocker pèse modérément.
Si un hard_blocker est identifié : il apparaît dans blocking_requirements, l'ATS Score
doit être bas, et le blocking_message doit l'expliquer.

==================================================
ETAPE 6 : MATCHING SEMANTIQUE
=============================
Pour chaque exigence : matched / partially_matched / missing, avec un score de \
confiance 0..100.

==================================================
ETAPE 7 : CALCUL DU CAREER SCORE
================================
Cohérence de l'évolution professionnelle : compétences et responsabilités transférables,
niveau d'expérience, proximité fonctionnelle.
Ne pas pénaliser un changement de secteur/métier ni l'absence du titre exact.
Indépendant des filtres ATS et des blockers. Entre 0 et 100.

==================================================
ETAPE 8 : CALCUL DU ATS SCORE
=============================
TEMPS 1 — Score brut : présence des compétences pondérée (required fort, preferred \
moyen, nice_to_have faible).
TEMPS 2 — Pénalités de blocage : hard (majeure), medium (significative), soft \
(modérée). Cumuler ; le plus grave prime.
Le score final doit être cohérent avec le blocking_message.

==================================================
ETAPE 9 : CALCUL DU ATS POTENTIAL
==================================
"Score ATS atteignable sans inventer de compétences, en améliorant présentation et \
wording."
hard_blocker réellement absent -> potentiel reste bas.
hard_blocker présent dans le parcours mais absent du CV -> potentiel nettement plus \
élevé.
soft/medium seulement -> potentiel nettement supérieur possible.
Cohérent avec l'ATS Score.

==================================================
ETAPE 10 : DETECTION DES CRITERES BLOQUANTS
============================================
Lister dans blocking_requirements tous les hard_blocker et medium_blocker (jamais un \
soft seul,
ni un preferred/nice_to_have). Pour chacun : critère, niveau, raison, écart chiffré et \
% si quantitatif.

==================================================
ETAPE 11 : GENERATION DES ACTIONS
==================================
Compétence absente du profil -> expliquer l'écart, ne pas suggérer de l'inventer.
Compétence présente mais mal formulée -> expliquer concrètement comment la mettre en \
valeur.
Actions concrètes, applicables, jamais inventer une expérience inexistante.

==================================================
ETAPE 12 : JUSTIFICATION DES SCORES
=====================================
Expliquer le Career Score, le détail des pénalités de l'ATS Score (citer chaque \
blocker),
et pourquoi certains critères sont hard/medium plutôt que soft.

==================================================
FORMAT DE SORTIE
================
Retourner UNIQUEMENT du JSON valide, sans aucun texte avant ou après, sans balises \
Markdown.
Ne pas calculer d'indice de regret : ce champ est géré en dehors du modèle.

Pour "company_name" : extraire le nom de l'EMPLOYEUR RÉEL, c'est-à-dire l'entité qui
recrute et qui emploiera le candidat. Dans le cas d'une ESN / société de conseil qui
recrute pour le compte d'un client final, l'employeur réel est l'ESN elle-même, PAS le
client final (souvent anonyme, ex. "un partenaire du secteur Transport"). Si aucun
employeur n'est identifiable, renvoyer une chaîne vide.

{{
  "company_name": "",
  "career_score": 0,
  "ats_score": 0,
  "ats_potential": 0,
  "cv_skills": [],
  "job_skills": [],
  "mandatory_requirements": [],
  "preferred_requirements": [],
  "blocking_requirements": [
    {{"skill": "", "level": "hard_blocker | medium_blocker", "reason": "", \
"gap_value": null, "gap_percent": null}}
  ],
  "blocking_message": "",
  "matches": [
    {{"skill": "", "importance": "required | preferred | nice_to_have", "result": \
"matched | partially_matched | missing", "confidence": 0}}
  ],
  "ats_gaps": [
    {{"skill": "", "missing_in_cv": true, "why_it_matters": "", "cv_fix_example": ""}}
  ],
  "actions": [
    {{"action": "", "details": ""}}
  ],
  "career_explanation": [],
  "ats_explanation": []
}}

CV

{cv}

OFFRE

{offer}
"""


def build_prompt(cv: str, offer: str) -> str:
    return PROMPT_TEMPLATE.format(cv=cv, offer=offer)
