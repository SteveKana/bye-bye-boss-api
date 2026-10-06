from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching import labels_gateway
from app.modules.matching.jobs import process_skill_labels
from app.modules.matching.models import CandidateMatch, MatchStatus, SkillLabel
from app.modules.matching.repository import (
    CandidateMatchRepository,
    SkillLabelRepository,
)
from app.modules.matching.skill_labels import (
    apply_labels,
    collect_keys,
    needs_label,
)
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository

ANALYSIS = {
    "cv_skills": ["agile"],
    "job_skills": [{"skill": "roadmap_planning", "importance": "required"}],
    "matches": [
        {"skill": "roadmap_planning", "result": "matched"},
        {"skill": "SQL Server", "result": "missing"},
    ],
    "ats_gaps": [{"skill": "workshop_facilitation", "why_it_matters": "x"}],
    "blocking_requirements": [{"skill": "english_fluency", "level": "hard_blocker"}],
}


def test_needs_label_only_for_internal_concepts() -> None:
    assert needs_label("roadmap_planning")
    assert needs_label("agile")
    assert not needs_label("SQL Server")
    assert not needs_label("Gestion de projet")
    assert not needs_label("")


def test_collect_keys_reads_every_skill_list() -> None:
    assert collect_keys(ANALYSIS) == {
        "roadmap_planning",
        "workshop_facilitation",
        "english_fluency",
    }


def test_apply_labels_uses_glossary_then_falls_back_and_keeps_skill() -> None:
    result = apply_labels(ANALYSIS, {"roadmap_planning": "Planification de roadmap"})

    assert result["job_skills"][0]["label"] == "Planification de roadmap"
    assert result["job_skills"][0]["skill"] == "roadmap_planning"
    # Not in the glossary yet: readable fallback, no underscore.
    assert result["ats_gaps"][0]["label"] == "Workshop facilitation"
    assert result["blocking_requirements"][0]["label"] == "English fluency"
    # Already human: untouched.
    assert result["matches"][1]["label"] == "SQL Server"
    # The stored analysis is never mutated.
    assert "label" not in ANALYSIS["job_skills"][0]


def test_parse_labels_keeps_only_asked_keys_and_cleans_underscores() -> None:
    raw = (
        '```json\n{"labels": {"a_b": "Mon libellé", "x": "ignoré", '
        '"c_d": "texte_avec_underscore", "e": ""}}\n```'
    )
    assert labels_gateway.parse_labels(raw, ["a_b", "c_d", "e"]) == {
        "a_b": "Mon libellé",
        "c_d": "Texte avec underscore",
    }
    assert labels_gateway.parse_labels("pas du json", ["a_b"]) == {}


async def _user_id(email: str) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        return user.id


async def _make_match(user_id: uuid.UUID, external_id: str) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).find_one(user_id=user_id)
        if profile is None:
            profile = await CandidateProfileRepository(session).create(
                CandidateProfile(
                    user_id=user_id, status=ProfileStatus.complete.value, raw_text="cv"
                )
            )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id=external_id,
                title="Offre",
                url="https://example.com/o",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer.id,
                status=MatchStatus.scored.value,
                career_score=80,
                ats_score=80,
                ats_potential=90,
                analysis=ANALYSIS,
                computed_at=utcnow(),
            )
        )
        await session.commit()
        return match.id


async def test_job_translates_once_stores_glossary_and_flags_matches(
    monkeypatch, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    calls: list[list[str]] = []

    async def _fake_translate(keys):
        calls.append(sorted(keys))
        return {k: f"FR {k}" for k in keys}

    monkeypatch.setattr(labels_gateway, "translate_keys", _fake_translate)

    user_id = await _user_id("user@example.com")
    first = await _make_match(user_id, "labels-1")
    second = await _make_match(user_id, "labels-2")

    assert await process_skill_labels() == 2

    # One call for the three distinct concepts, whatever the number of matches.
    assert calls == [["english_fluency", "roadmap_planning", "workshop_facilitation"]]
    async with AsyncSessionLocal() as session:
        assert len(await SkillLabelRepository(session).list()) == 3
        for match_id in (first, second):
            match = await CandidateMatchRepository(session).get(match_id)
            assert match.labels_done is True

    # Nothing left to do: no new LLM call.
    assert await process_skill_labels() == 0
    assert len(calls) == 1

    # A new match reusing known concepts costs nothing either.
    await _make_match(user_id, "labels-3")
    assert await process_skill_labels() == 1
    assert len(calls) == 1


async def test_failed_translation_keeps_match_pending_and_falls_back(
    monkeypatch, client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    async def _down(keys):
        return {}  # OpenAI unreachable: nothing translated

    monkeypatch.setattr(labels_gateway, "translate_keys", _down)
    user_id = await _user_id("user@example.com")
    match_id = await _make_match(user_id, "labels-fail")

    assert await process_skill_labels() == 0
    async with AsyncSessionLocal() as session:
        match = await CandidateMatchRepository(session).get(match_id)
        assert match.labels_done is False

    # The API still answers, with the underscore-free fallback.
    r = await client.get(f"/api/v1/matching/{match_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["analysis"]["job_skills"][0]["label"] == "Roadmap planning"


async def test_api_returns_french_labels_from_glossary(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    user_id = await _user_id("user@example.com")
    match_id = await _make_match(user_id, "labels-api")
    async with AsyncSessionLocal() as session:
        await SkillLabelRepository(session).create(
            SkillLabel(key="roadmap_planning", label="Planification de roadmap")
        )
        await session.commit()

    for url in (
        f"/api/v1/matching/{match_id}",
        "/api/v1/matching/top",
        "/api/v1/matching/dashboard",
    ):
        r = await client.get(url, headers=auth_headers)
        assert r.status_code == 200, url
        body = r.json()
        analysis = (body[0] if isinstance(body, list) else body)["analysis"]
        assert analysis["job_skills"][0]["label"] == "Planification de roadmap"
        assert analysis["job_skills"][0]["skill"] == "roadmap_planning"
        assert analysis["matches"][1]["label"] == "SQL Server"
