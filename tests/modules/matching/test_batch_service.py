from __future__ import annotations

import json

import pytest
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import AppError
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.mailer.models import EmailMessage
from app.modules.matching import batch_gateway, batch_service
from app.modules.matching.batch_gateway import BatchInfo
from app.modules.matching.batch_service import MatchingBatchService
from app.modules.matching.models import CandidateMatch, MatchStatus
from app.modules.matching.repository import (
    CandidateMatchRepository,
    MatchingBatchRepository,
)
from tests.modules.matching.test_service import _make_complete_profile, _make_offer

_ANALYSIS_JSON = json.dumps(
    {
        "company_name": "Astek",
        "career_score": 80,
        "ats_score": 78,
        "ats_potential": 90,
        "blocking_message": "Aucun blocage.",
    }
)


class FakeOpenAI:
    """Stands in for batch_gateway's three network functions."""

    _counter = 0

    def __init__(self, monkeypatch) -> None:
        self.submitted: list[list[dict]] = []
        self.results: dict[str, str | None] = {}
        self.info = BatchInfo(status="completed", output_file_id="file_1")
        self.fail_submit = False
        monkeypatch.setattr(batch_gateway, "submit_batch", self._submit)
        monkeypatch.setattr(batch_gateway, "get_batch_info", self._info)
        monkeypatch.setattr(batch_gateway, "download_results", self._download)

    async def _submit(self, requests):
        if self.fail_submit:
            raise AppError("no key")
        self.submitted.append(requests)
        # Unique across fakes: openai_batch_id is unique in the table and a
        # test may build several fakes in a row.
        FakeOpenAI._counter += 1
        return f"batch_{FakeOpenAI._counter}"

    async def _info(self, openai_batch_id):
        return self.info

    async def _download(self, file_id):
        return self.results


async def _add_match(profile, offer, **values) -> CandidateMatch:
    defaults = {
        "candidate_profile_id": profile.id,
        "job_offer_id": offer.id,
        "career_score": 0,
        "ats_score": 0,
        "ats_potential": 0,
        "computed_at": utcnow(),
        "status": MatchStatus.shortlisted.value,
    }
    defaults.update(values)
    async with AsyncSessionLocal() as session:
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(**defaults)
        )
        await session.commit()
        return match


async def _reload(match: CandidateMatch) -> CandidateMatch:
    async with AsyncSessionLocal() as session:
        reloaded = await CandidateMatchRepository(session).get(match.id)
    assert reloaded is not None
    return reloaded


async def _queued_emails(to_email: str) -> list[EmailMessage]:
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(EmailMessage).where(EmailMessage.to_email == to_email)
        )
        return list(result.all())


async def _profile_with_user():
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).create(
            User(email=f"{utcnow().timestamp()}@example.com", is_verified=True)
        )
        await session.commit()
    profile = await _make_complete_profile(user_id=user.id)
    return user, profile


# ---- submit -----------------------------------------------------------------


async def test_submit_sends_prefilter_requests_with_the_cheap_model(
    monkeypatch,
) -> None:
    fake = FakeOpenAI(monkeypatch)
    profile = await _make_complete_profile()
    rows = [
        await _add_match(profile, await _make_offer(title=f"Offre {i}"))
        for i in range(2)
    ]
    rows.append(
        await _add_match(
            profile,
            await _make_offer(title="Offre visible"),
            status=MatchStatus.placeholder.value,
        )
    )

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).submit_pending()

    assert report.batches_submitted == 1
    assert report.requests_submitted == 3
    (requests,) = fake.submitted
    assert {r["custom_id"] for r in requests} == {str(r.id) for r in rows}
    settings = get_settings()
    for request in requests:
        assert request["body"]["model"] == settings.MATCHING_PREFILTER_MODEL
        assert request["body"]["reasoning"] == {"effort": "low"}
        assert "ats_score" in request["body"]["input"]  # the short output format
        assert profile.raw_text in request["body"]["input"]
    for row in rows:
        assert (await _reload(row)).batch_id is not None

    # Already in flight: a second submission must not send them again.
    async with AsyncSessionLocal() as session:
        again = await MatchingBatchService(session).submit_pending()
    assert again.batches_submitted == 0


async def test_submit_never_splits_one_candidates_rows_across_batches(
    monkeypatch,
) -> None:
    fake = FakeOpenAI(monkeypatch)
    monkeypatch.setattr(get_settings(), "MATCHING_BATCH_MAX_REQUESTS", 3)
    first = await _make_complete_profile()
    second = await _make_complete_profile()
    for profile in (first, second):
        for i in range(2):
            await _add_match(profile, await _make_offer(title=f"{profile.id}-{i}"))

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).submit_pending()

    assert report.batches_submitted == 2
    assert sorted(len(r) for r in fake.submitted) == [2, 2]


async def test_submit_sends_full_analysis_for_pending_rows(monkeypatch) -> None:
    fake = FakeOpenAI(monkeypatch)
    profile = await _make_complete_profile()
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.pending.value
    )

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).submit_pending()

    (requests,) = fake.submitted
    body = requests[0]["body"]
    assert requests[0]["custom_id"] == str(row.id)
    assert body["model"] == get_settings().MATCHING_OPENAI_MODEL
    assert body["reasoning"] == {"effort": "medium"}
    assert "career_score" in body["input"]  # the full output format


async def test_submit_leaves_rows_untouched_when_openai_is_unavailable(
    monkeypatch,
) -> None:
    fake = FakeOpenAI(monkeypatch)
    fake.fail_submit = True
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).submit_pending()

    assert report.batches_submitted == 0
    reloaded = await _reload(row)
    assert reloaded.batch_id is None
    assert reloaded.status == MatchStatus.shortlisted.value


# ---- poll: pre-filter -----------------------------------------------------


async def _submit_then(monkeypatch):
    fake = FakeOpenAI(monkeypatch)
    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).submit_pending()
    return fake


async def test_prefilter_keeps_best_scores_at_or_above_threshold_up_to_the_cap(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "MATCHING_PREFILTER_MIN_ATS", 75)
    monkeypatch.setattr(get_settings(), "MATCHING_MAX_OFFERS_PER_CANDIDATE", 2)
    profile = await _make_complete_profile()
    scores = [90, 82, 75, 74, 40]
    rows = [
        await _add_match(profile, await _make_offer(title=f"Offre {s}")) for s in scores
    ]
    fake = await _submit_then(monkeypatch)
    fake.results = {
        str(row.id): json.dumps({"ats_score": score})
        for row, score in zip(rows, scores, strict=True)
    }

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()

    assert report.batches_completed == 1
    assert report.promoted == 2
    assert report.filtered_out == 3
    statuses = [(await _reload(r)).status for r in rows]
    assert statuses == [
        MatchStatus.pending.value,  # 90
        MatchStatus.pending.value,  # 82
        MatchStatus.filtered_out.value,  # 75: above threshold but over the cap
        MatchStatus.filtered_out.value,  # 74: below threshold
        MatchStatus.filtered_out.value,  # 40
    ]
    assert (await _reload(rows[1])).prefilter_score == 82
    assert (await _reload(rows[0])).batch_id is None


async def test_prefilter_exactly_at_threshold_is_kept(monkeypatch) -> None:
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): json.dumps({"ats_score": 75})}

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).poll_batches()

    assert (await _reload(row)).status == MatchStatus.pending.value


async def test_prefilter_failed_results_are_retried_then_given_up_on(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "MATCHING_MAX_ATTEMPTS", 2)
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())

    for expected_attempts, expected_status in (
        (1, MatchStatus.shortlisted.value),
        (2, MatchStatus.filtered_out.value),
    ):
        fake = await _submit_then(monkeypatch)
        fake.results = {str(row.id): None}  # request errored
        async with AsyncSessionLocal() as session:
            report = await MatchingBatchService(session).poll_batches()
        assert report.failed == 1
        reloaded = await _reload(row)
        assert reloaded.attempts == expected_attempts
        assert reloaded.status == expected_status
        assert reloaded.batch_id is None


async def test_unreadable_prefilter_output_counts_as_a_failure(monkeypatch) -> None:
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): "pas du json"}

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()

    assert report.failed == 1
    assert (await _reload(row)).attempts == 1


async def test_batch_still_running_is_left_alone(monkeypatch) -> None:
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())
    fake = await _submit_then(monkeypatch)
    fake.info = BatchInfo(status="in_progress")

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()

    assert report.batches_completed == 0
    assert (await _reload(row)).batch_id is not None
    async with AsyncSessionLocal() as session:
        assert len(await MatchingBatchRepository(session).list_submitted()) == 1


async def test_failed_batch_releases_its_rows_for_a_retry(monkeypatch) -> None:
    profile = await _make_complete_profile()
    row = await _add_match(profile, await _make_offer())
    fake = await _submit_then(monkeypatch)
    fake.info = BatchInfo(status="failed")
    fake.results = {}

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()

    assert report.batches_failed == 1
    reloaded = await _reload(row)
    assert reloaded.batch_id is None
    assert reloaded.attempts == 1
    assert reloaded.status == MatchStatus.shortlisted.value


async def test_first_run_candidate_with_no_offer_passing_is_still_told(
    monkeypatch,
) -> None:
    """Steve's rule (see emails.py): a brand-new candidate always hears that
    the analysis finished, even when nothing made it through the filter."""
    user, profile = await _profile_with_user()
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.placeholder.value
    )
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): json.dumps({"ats_score": 30})}

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).poll_batches()
        await session.commit()

    assert (await _reload(row)).status == MatchStatus.filtered_out.value
    assert len(await _queued_emails(user.email)) == 1


# ---- poll: full analysis ----------------------------------------------------


async def test_analysis_results_become_a_scored_match_and_first_email(
    monkeypatch,
) -> None:
    emitted: list = []

    async def _emit(event):
        emitted.append(event)

    monkeypatch.setattr(batch_service.event_bus, "emit", _emit)

    user, profile = await _profile_with_user()
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.pending.value
    )
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): _ANALYSIS_JSON}

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()
        await session.commit()

    assert report.scored == 1
    scored = await _reload(row)
    assert scored.status == MatchStatus.scored.value
    assert (scored.career_score, scored.ats_score, scored.ats_potential) == (80, 78, 90)
    assert scored.company_name == "Astek"
    assert scored.blocking_message == "Aucun blocage."
    assert scored.analysis["ats_score"] == 78
    assert scored.batch_id is None
    # First analysed offer ever: the "first opportunities" email, not the brief.
    assert len(await _queued_emails(user.email)) == 1
    assert emitted == []


async def test_later_analysis_results_trigger_the_brief_event(monkeypatch) -> None:
    emitted: list = []

    async def _emit(event):
        emitted.append(event)

    monkeypatch.setattr(batch_service.event_bus, "emit", _emit)

    user, profile = await _profile_with_user()
    await _add_match(
        profile,
        await _make_offer(title="Déjà analysée"),
        status=MatchStatus.scored.value,
        career_score=70,
        ats_score=70,
        ats_potential=70,
    )
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.pending.value
    )
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): _ANALYSIS_JSON}

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).poll_batches()
        await session.commit()

    assert len(emitted) == 1
    assert emitted[0].profile_ids == [profile.id]
    assert await _queued_emails(user.email) == []  # no "first matches" mail


async def test_unparseable_analysis_is_retried(monkeypatch) -> None:
    profile = await _make_complete_profile()
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.pending.value
    )
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): json.dumps({"career_score": "n/a"})}

    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()

    assert report.failed == 1
    reloaded = await _reload(row)
    assert reloaded.status == MatchStatus.pending.value
    assert reloaded.attempts == 1
    assert reloaded.batch_id is None


@pytest.fixture(autouse=True)
def _no_real_events(monkeypatch):
    """Keep the real notification listener (which opens its own DB session)
    out of these tests unless one replaces `emit` itself."""

    async def _noop(event):
        return None

    monkeypatch.setattr(batch_service.event_bus, "emit", _noop)


async def test_career_score_is_lowered_when_the_offer_asks_far_more_years(
    monkeypatch,
) -> None:
    """Seniority rule (Steve, 2026-10-05): 7 years of experience against an
    offer asking 15 -> 5 years beyond the +3 window -> -40 (capped); the model's
    own score stays readable in the analysis."""

    async def _emit(event):
        return None

    monkeypatch.setattr(batch_service.event_bus, "emit", _emit)
    user, profile = await _profile_with_user()
    async with AsyncSessionLocal() as session:
        await CandidateProfileRepository(session).update(
            profile, {"total_experience": "7 ans"}
        )
        await session.commit()
    offer = await _make_offer(description="Nous exigeons 15 ans d'expérience minimum.")
    row = await _add_match(profile, offer, status=MatchStatus.pending.value)
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): _ANALYSIS_JSON}

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).poll_batches()
        await session.commit()

    scored = await _reload(row)
    assert scored.career_score == 40
    assert scored.analysis["career_score_model"] == 80
    assert scored.analysis["seniority_penalty"] == 40
    # ATS scores are untouched.
    assert (scored.ats_score, scored.ats_potential) == (78, 90)


async def test_career_score_is_kept_when_the_offer_states_no_years(
    monkeypatch,
) -> None:
    async def _emit(event):
        return None

    monkeypatch.setattr(batch_service.event_bus, "emit", _emit)
    _, profile = await _profile_with_user()
    row = await _add_match(
        profile, await _make_offer(), status=MatchStatus.pending.value
    )
    fake = await _submit_then(monkeypatch)
    fake.results = {str(row.id): _ANALYSIS_JSON}

    async with AsyncSessionLocal() as session:
        await MatchingBatchService(session).poll_batches()
        await session.commit()

    scored = await _reload(row)
    assert scored.career_score == 80
    assert "seniority_penalty" not in scored.analysis
