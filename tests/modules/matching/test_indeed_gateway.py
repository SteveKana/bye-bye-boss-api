from __future__ import annotations

from app.core.config import get_settings
from app.modules.matching import indeed_gateway
from app.modules.matching.indeed_gateway import (
    _parse_reviews_page,
    _review_page_url,
    fetch_company_reviews,
    is_configured,
)

# Minimal HTML reconstructed from the real, live structure verified on
# fr.indeed.com/cmp/Randstad/reviews and .../Groupe-Sii/reviews (2026-10-02,
# via a JS-executing browser -- see indeed_gateway.py's docstring): one
# `[data-testid="reviews[]"]` block per review, an `aria-label` reading
# "X,X/5 étoiles." for the rating, `[itemprop="author"]` wrapping an <h4>
# job title and (when present) a single <span> location, `[itemprop=
# "datePublished"]`'s `content` attribute for the French long-form date,
# and `[data-testid="title"]`/`[data-testid="review-text"]` for the
# headline and body.
_SAMPLE_REVIEWS_HTML = """
<html><body>
<div data-testid="reviews[]">
  <div aria-label="4,0/5 étoiles."></div>
  <span itemprop="author">
    <meta itemprop="name" content="Consultant">
    <div>
      <h4>Consultant</h4>
      <div><svg></svg><span>Yonne</span></div>
    </div>
  </span>
  <meta itemprop="datePublished" content="7 septembre 2026">
  <h3 data-testid="title">Très bonne expérience</h3>
  <span data-testid="review-text">Beaucoup de pression commerciale.</span>
</div>
<div data-testid="reviews[]">
  <div aria-label="2,0/5 étoiles."></div>
  <span itemprop="author">
    <meta itemprop="name" content="RH">
    <div>
      <h4>RH</h4>
    </div>
  </span>
  <meta itemprop="datePublished" content="27 août 2026">
  <h3 data-testid="title">Bon lieu de travail</h3>
  <span data-testid="review-text">Plutôt satisfait, RH professionnels.</span>
</div>
</body></html>
"""


def test_review_page_url_first_page_has_no_start_param() -> None:
    assert _review_page_url("Randstad", start=0) == (
        "https://fr.indeed.com/cmp/Randstad/reviews"
    )


def test_review_page_url_later_page_has_start_param() -> None:
    assert _review_page_url("Randstad", start=20) == (
        "https://fr.indeed.com/cmp/Randstad/reviews?start=20"
    )


def test_parse_extracts_rating_title_and_text() -> None:
    reviews = _parse_reviews_page(_SAMPLE_REVIEWS_HTML, "https://example.test")

    assert len(reviews) == 2
    first, second = reviews
    assert first.overall_rating == 4.0
    assert first.title == "Très bonne expérience"
    assert "pression commerciale" in first.text
    assert first.source_url == "https://example.test"
    assert second.overall_rating == 2.0


def test_parse_extracts_job_title_and_location() -> None:
    reviews = _parse_reviews_page(_SAMPLE_REVIEWS_HTML, "url")

    assert reviews[0].job_title == "Consultant"
    assert reviews[0].location == "Yonne"


def test_parse_handles_missing_location() -> None:
    reviews = _parse_reviews_page(_SAMPLE_REVIEWS_HTML, "url")

    assert reviews[1].job_title == "RH"
    assert reviews[1].location == ""


def test_parse_extracts_french_date() -> None:
    reviews = _parse_reviews_page(_SAMPLE_REVIEWS_HTML, "url")

    assert reviews[0].review_date is not None
    assert reviews[0].review_date.year == 2026
    assert reviews[0].review_date.month == 9


def test_parse_returns_empty_list_when_no_reviews_found() -> None:
    assert _parse_reviews_page("<html><body>Rien ici.</body></html>", "url") == []


def test_is_configured_false_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "")
    assert is_configured() is False


def test_is_configured_true_with_credentials(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "fake-key")
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")
    assert is_configured() is True


async def test_fetch_company_reviews_returns_empty_when_not_configured(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "")
    assert await fetch_company_reviews("Randstad") == []


async def test_fetch_company_reviews_returns_empty_for_blank_name(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "fake-key")
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")
    assert await fetch_company_reviews("   ") == []


async def test_fetch_company_reviews_stops_pagination_when_page_fetch_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "fake-key")
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")
    monkeypatch.setattr(get_settings(), "INDEED_REVIEWS_MAX_PAGES_PER_COMPANY", 3)

    calls = {"count": 0}

    async def _fake_fetch(url):
        calls["count"] += 1
        return None  # simulates a failed Bright Data request

    monkeypatch.setattr(indeed_gateway, "_fetch_unblocked_html", _fake_fetch)

    reviews = await fetch_company_reviews("Randstad")

    assert reviews == []
    assert calls["count"] == 1  # never retries past the first failure


async def test_fetch_company_reviews_paginates_up_to_the_configured_cap(
    monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_API_KEY", "fake-key")
    monkeypatch.setattr(get_settings(), "BRIGHTDATA_WEB_UNLOCKER_ZONE", "web_unlocker1")
    monkeypatch.setattr(get_settings(), "INDEED_REVIEWS_MAX_PAGES_PER_COMPANY", 2)

    calls = {"urls": []}

    async def _fake_fetch(url):
        calls["urls"].append(url)
        return _SAMPLE_REVIEWS_HTML  # same 2 reviews on every "page"

    monkeypatch.setattr(indeed_gateway, "_fetch_unblocked_html", _fake_fetch)

    reviews = await fetch_company_reviews("Randstad")

    assert len(calls["urls"]) == 2
    assert calls["urls"][0] == "https://fr.indeed.com/cmp/Randstad/reviews"
    assert calls["urls"][1] == "https://fr.indeed.com/cmp/Randstad/reviews?start=20"
    assert len(reviews) == 4  # 2 reviews x 2 pages
