"""The AI step: files offers (and profiles) under the closed list of
specialties and extracts the hard skills an offer asks for.

Same convention as matching/labels_gateway.py: plain Responses API calls,
`max_retries=0`, explicit retries, JSON answer parsed forgivingly. A call that
fails is simply absent from the result and the caller tries again on its next
run, so a hiccup here can never break anything else.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.market.taxonomy import (
    SKILL_CATEGORIES,
    normalize,
    taxonomy_for_prompt,
)

logger = get_logger("market.classifier")

MAX_PARALLEL_CALLS = 4
MAX_SKILLS_PER_OFFER = 12

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_KEY_STRIP = re.compile(r"[\s\-_.'’/·]+")

OFFERS_PROMPT = """
Tu analyses des offres d'emploi pour un observatoire du marché de l'emploi.

Pour CHAQUE offre ci-dessous (numérotée), indique :
1. "famille" et "specialite" : le métier réellement recherché, choisi UNIQUEMENT
   dans la liste ci-dessous (recopie les libellés à l'identique). Base-toi sur
   les missions décrites, pas sur un seul mot du titre : "Développeur" ou
   "Communication" ne suffisent pas, choisis la spécialité précise. Si aucune
   spécialité ne convient, mets la famille et "specialite": null. Si aucune
   famille ne convient, mets "Autre".
2. "competences" : au maximum {max_skills} compétences TECHNIQUES (hard skills)
   demandées dans l'offre : outils, logiciels, langages, technologies,
   certifications, méthodes, savoir-faire métier précis. Exclus les qualités
   humaines (rigueur, autonomie...), les diplômes, les langues parlées et les
   avantages. Chaque compétence : {{"nom": "...", "categorie": "..."}}.
   - "nom" : nom canonique, court, orthographe officielle, au singulier
     (ex. "Jira", "Power BI", "Scrum", "SQL", "SAFe", "Comptabilité générale").
   - "categorie" : "outil", "technologie", "certification", "methode" ou
     "metier" (savoir-faire métier sans outil précis).

LISTE DES MÉTIERS
{taxonomy}

Retourne UNIQUEMENT du JSON valide, sans texte avant ou après, sans Markdown :
{{"offres": [{{"n": 1, "famille": "...", "specialite": "..." ou null,
"competences": [{{"nom": "...", "categorie": "..."}}]}}]}}

OFFRES
{offers}
"""

PROFILE_PROMPT = """
Tu dois ranger le profil d'un candidat dans UN métier, choisi UNIQUEMENT dans
la liste ci-dessous (recopie les libellés à l'identique). Base-toi sur
l'ensemble du profil (intitulé, résumé, compétences, expériences), pas sur un
seul mot. Si aucune spécialité ne convient, mets la famille et
"specialite": null. Si aucune famille ne convient, mets "Autre".

LISTE DES MÉTIERS
{taxonomy}

Retourne UNIQUEMENT du JSON valide, sans texte avant ou après, sans Markdown :
{{"famille": "...", "specialite": "..." ou null}}

PROFIL
{profile}
"""


@dataclass
class OfferInput:
    key: str  # opaque id the caller uses to match the answer back
    title: str
    description: str


@dataclass
class OfferTags:
    family: str
    specialty: str | None
    skills: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ClassifyUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


def skill_key(name: str) -> str:
    """Spelling-insensitive identity of a skill: "Power BI", "PowerBI" and
    "power-bi" are one skill."""
    return _KEY_STRIP.sub("", name.casefold())


def build_offers_prompt(offers: Sequence[OfferInput], *, max_chars: int) -> str:
    blocks = []
    for number, offer in enumerate(offers, start=1):
        description = (offer.description or "").strip()[:max_chars]
        blocks.append(
            f"### Offre {number}\nTitre : {offer.title.strip()}\n"
            f"Description : {description or '(aucune)'}"
        )
    return OFFERS_PROMPT.format(
        max_skills=MAX_SKILLS_PER_OFFER,
        taxonomy=taxonomy_for_prompt(),
        offers="\n\n".join(blocks),
    )


def _load_json(raw: str) -> object:
    text = raw.strip()
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def clean_skills(raw_skills: object) -> list[dict[str, str]]:
    if not isinstance(raw_skills, list):
        return []
    skills: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_skills:
        if not isinstance(item, dict):
            continue
        name = item.get("nom")
        if not isinstance(name, str):
            continue
        name = " ".join(name.split()).strip(" .,;")
        key = skill_key(name)
        if not name or len(name) > 60 or not key or key in seen:
            continue
        category = item.get("categorie")
        if category not in SKILL_CATEGORIES:
            category = "metier"
        seen.add(key)
        skills.append({"name": name, "key": key, "category": category})
        if len(skills) >= MAX_SKILLS_PER_OFFER:
            break
    return skills


def parse_offers_answer(raw: str, count: int) -> dict[int, OfferTags]:
    """offer number (1-based) -> tags, for the offers the model answered
    about. Anything unreadable is left out (the caller handles missing ones)."""
    data = _load_json(raw)
    items = data.get("offres") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    result: dict[int, OfferTags] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("n")
        if not isinstance(number, int) or not 1 <= number <= count:
            continue
        family, specialty = normalize(item.get("famille"), item.get("specialite"))
        result[number] = OfferTags(
            family=family,
            specialty=specialty,
            skills=clean_skills(item.get("competences")),
        )
    return result


def parse_profile_answer(raw: str) -> tuple[str, str | None] | None:
    data = _load_json(raw)
    if not isinstance(data, dict):
        return None
    return normalize(data.get("famille"), data.get("specialite"))


def _call_sync(*, prompt: str, usage: ClassifyUsage) -> str:
    settings = get_settings()
    client = OpenAI(
        api_key=settings.OPENAI_API_KEY or "",
        timeout=settings.MARKET_TIMEOUT_SECONDS,
        max_retries=0,
    )
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = client.responses.create(
                model=settings.MARKET_MODEL,
                input=prompt,
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
            )
            usage.calls += 1
            tokens = getattr(response, "usage", None)
            if tokens is not None:
                usage.input_tokens += int(getattr(tokens, "input_tokens", 0) or 0)
                usage.output_tokens += int(getattr(tokens, "output_tokens", 0) or 0)
            return response.output_text
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("market_llm_retry", attempt=attempt, error=str(exc))
    raise RuntimeError("market: OpenAI call failed") from last_exc


async def _classify_chunk(
    offers: Sequence[OfferInput], usage: ClassifyUsage
) -> dict[str, OfferTags] | None:
    """None when the whole call failed (retry later). Otherwise one entry per
    offer -- one the model skipped gets a plain "Autre" with no skills, so a
    single stubborn offer can never be retried forever."""
    settings = get_settings()
    prompt = build_offers_prompt(
        offers, max_chars=settings.MARKET_DESCRIPTION_MAX_CHARS
    )
    try:
        raw = await asyncio.to_thread(_call_sync, prompt=prompt, usage=usage)
    except RuntimeError:
        return None
    parsed = parse_offers_answer(raw, len(offers))
    if not parsed:
        logger.error("market_bad_answer", raw=raw[:500])
        return None
    return {
        offer.key: parsed.get(number) or OfferTags(family="Autre", specialty=None)
        for number, offer in enumerate(offers, start=1)
    }


async def classify_offers(
    offers: Sequence[OfferInput],
) -> tuple[dict[str, OfferTags], ClassifyUsage]:
    """offer key -> tags for every offer whose call succeeded."""
    usage = ClassifyUsage()
    if not offers:
        return {}, usage
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("market_no_api_key")
        return {}, usage
    size = max(1, settings.MARKET_OFFERS_PER_CALL)
    chunks = [list(offers[i : i + size]) for i in range(0, len(offers), size)]
    gate = asyncio.Semaphore(MAX_PARALLEL_CALLS)

    async def run(chunk: list[OfferInput]) -> dict[str, OfferTags] | None:
        async with gate:
            return await _classify_chunk(chunk, usage)

    results = await asyncio.gather(*(run(c) for c in chunks))
    merged: dict[str, OfferTags] = {}
    for part in results:
        if part:
            merged.update(part)
    return merged, usage


async def classify_profile(profile_text: str) -> tuple[str, str | None] | None:
    """(family, specialty) for a candidate profile, None if the call failed."""
    if not get_settings().OPENAI_API_KEY:
        return None
    prompt = PROFILE_PROMPT.format(
        taxonomy=taxonomy_for_prompt(), profile=profile_text[:4000]
    )
    try:
        raw = await asyncio.to_thread(_call_sync, prompt=prompt, usage=ClassifyUsage())
    except RuntimeError:
        return None
    return parse_profile_answer(raw)
