"""LLM gateway: turns raw CV text into a structured candidate profile.

Isolated behind this module so swapping providers (or models) later touches
one file. Uses the OpenAI Responses API — same provider and call shape as
`matchcareer_engine` (the standalone matching engine), so the whole project
stays on one LLM vendor. A failed call or a malformed JSON body raises — the
route turns that into a clear error rather than saving garbage.
"""

from __future__ import annotations

import asyncio
import json
import re

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger("cv.gateway")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_PROMPT_TEMPLATE = """Tu extrais les informations d'un CV en français vers un \
objet JSON strict. Réponds UNIQUEMENT avec du JSON valide, sans texte autour, \
sans balises markdown. Si une information est absente du CV, mets une chaîne \
vide "" (ou une liste vide []) — n'invente jamais de donnée.

Règle spécifique pour "location" : uniquement le nom d'une ville française \
précise (ex: "Lyon", "Paris"), jamais une région, un département ou un pays \
(ex: "Île-de-France", "Auvergne-Rhône-Alpes", "France"). Si le CV ne mentionne \
qu'une zone large sans ville précise, laisse ce champ vide "".

Règles pour les quatre champs de synthèse ci-dessous ("professional_summary", \
"identified_roles", "domains", "skill_categories") : contrairement aux autres \
champs, ce ne sont pas des copies littérales du CV mais une synthèse que tu \
dois produire toi-même à partir de son contenu réel — reste néanmoins \
strictement fondé sur ce que le CV décrit, sans inventer d'employeur, de \
diplôme ou de compétence qui n'y figure pas.
- "professional_summary" : 1 à 2 phrases en français résumant le profil \
professionnel du candidat (son métier principal et ses domaines de \
spécialisation), rédigées naturellement comme une accroche de CV.
- "identified_roles" : 2 à 4 intitulés de poste/métiers auxquels ce profil \
correspond globalement (ex: "Product Owner", "Chef de projet SI") — pas \
nécessairement une recopie exacte des intitulés déjà présents dans \
"experiences", plutôt une classification du profil dans son ensemble.
- "domains" : 2 à 4 secteurs ou domaines fonctionnels dans lesquels le \
candidat a de l'expérience (ex: "Data", "Retail", "Finance"), déduits des \
entreprises et missions décrites.
- "skill_categories" : les compétences détectées (reprises de "skills"), \
regroupées sous des noms de catégories que tu choisis toi-même en fonction \
du profil (ex: "Product & Delivery", "Data & Tech", "Outils", "Méthodes" \
pour un profil produit/data ; des catégories différentes conviendront à un \
autre profil). Chaque compétence de "skills" doit apparaître dans au moins \
une catégorie.

Schéma exact à respecter :
{{
  "first_name": "",
  "last_name": "",
  "email": "",
  "location": "",
  "total_experience": "",
  "experiences": [
    {{"title": "", "company": "", "period": "", "description": "", "tools": []}}
  ],
  "skills": [],
  "formations": [{{"title": "", "school_period": ""}}],
  "languages": [{{"name": "", "level": ""}}],
  "certifications": [{{"title": "", "issuer_period": ""}}],
  "professional_summary": "",
  "identified_roles": [],
  "domains": [],
  "skill_categories": [{{"category": "", "skills": []}}]
}}

CV

{cv}
"""


class CvParsingUnavailableError(AppError):
    status_code = 503
    code = "cv_parsing_unavailable"
    message = "L'analyse automatique du CV n'est pas disponible pour le moment."


class CvParsingFailedError(AppError):
    status_code = 502
    code = "cv_parsing_failed"
    message = "L'analyse du CV a échoué. Merci de réessayer."


def _extract_json(text: str) -> str:
    """Pull the JSON object out of the model output.

    Handles: clean JSON, JSON wrapped in ```json fences, or JSON with stray
    prose around it (falls back to the outermost {...} span).
    """
    text = text.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    raise CvParsingFailedError()


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    # max_retries=0: the SDK itself retries transient errors by default (2
    # extra attempts per call), which stacks with our own retry loop below
    # and can multiply the worst-case wait far past `timeout * max_retries`.
    # We already retry explicitly, so the SDK's own retries are disabled.
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            # This is a structured-extraction task (read the CV, fill in a
            # fixed schema, write a short summary) -- not the kind of
            # multi-step problem that needs heavy reasoning. Without these,
            # the model defaults to a much deeper (and slower) reasoning
            # mode, which was pushing real calls past the timeout below.
            response = client.responses.create(
                model=model,
                input=prompt,
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
            )
            return response.output_text
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("cv_parsing_retry", attempt=attempt, error=str(exc))
    raise CvParsingFailedError() from last_exc


async def structure_cv_text(raw_text: str) -> dict:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("cv_parsing_no_api_key")
        raise CvParsingUnavailableError()

    prompt = _PROMPT_TEMPLATE.format(cv=raw_text)

    try:
        # The OpenAI SDK call is synchronous/blocking — run it off the event
        # loop so it doesn't stall other requests.
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            timeout=settings.OPENAI_TIMEOUT_SECONDS,
            prompt=prompt,
        )
    except CvParsingFailedError:
        raise

    payload = _extract_json(raw)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.error("cv_parsing_bad_json", raw=raw)
        raise CvParsingFailedError() from exc
