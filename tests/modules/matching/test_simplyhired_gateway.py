from __future__ import annotations

from app.modules.matching.simplyhired_gateway import _parse, _parse_reviews, _slugify

# Shape reconstructed from the real `/api/next/company/reviews` response
# (Randstad and Groupe-Sii, verified live via a JS-executing browser,
# 2026-10-02 -- see the module docstring's "Individual reviews" section).
# Real field names: overallRating, normalizedJobTitle, normalizedLocation,
# dateCreated (ISO-8601), title, text, pros, cons -- pros/cons are plain
# strings or null, never a nested object.
_SAMPLE_REVIEWS_PAYLOAD = {
    "companyName": "Randstad",
    "reviewsContentGroup": [
        {
            "overallRating": 4,
            "normalizedJobTitle": "Chargé de Recrutement (H/F)",
            "normalizedLocation": "Rueil-Malmaison (92)",
            "dateCreated": "2014-09-30T15:29:54.888Z",
            "title": "Semaine type",
            "text": "Au vu de la forte polyvalence...",
            "pros": None,
            "cons": None,
        },
        {
            "overallRating": 4,
            "normalizedJobTitle": "Ingénieur Systèmes Et Réseaux (H/F)",
            "normalizedLocation": "Guipavas (29)",
            "dateCreated": "2018-12-10T19:01:32.416Z",
            "title": "Très prometteur",
            "text": "Je ne suis entré chez SII que très récemment...",
            "pros": "ESN très humaine, directeur et commerciaux à l'écoute",
            "cons": "Le pôle infra n'est pas encore développé mais tout reste à faire",
        },
    ],
}

# Minimal HTML reconstructed from the one verified live page fetch (Groupe
# SII, 2026-09-30, see simplyhired_gateway.py's docstring) -- exercises the
# text-pattern parser against realistic wording without depending on
# SimplyHired's actual markup/class names, which this module was never able
# to inspect directly.
_SAMPLE_HTML = """
<html><body>
<h1>Groupe SII</h1>
<div class="rating">3,5 sur 5</div>
<p>Note globale basée sur 80 avis</p>
<ul>
  <li>Équilibre vie privée/professionnelle 3,7</li>
  <li>Salaire et avantages 3,1</li>
  <li>Sécurité de l'emploi et évolution 3,0</li>
  <li>Direction 3,2</li>
  <li>Culture d'entreprise 3,5</li>
</ul>
<p>48% Pourcentage d'employés satisfaits de leur salaire</p>
</body></html>
"""


def test_slugify_hyphenates_title_cased_words() -> None:
    assert _slugify("Groupe SII") == "Groupe-Sii"
    assert _slugify("Blaser Group GmbH") == "Blaser-Group-Gmbh"


def test_slugify_single_word_unchanged() -> None:
    assert _slugify("Paritel") == "Paritel"


def test_slugify_keeps_apostrophes() -> None:
    assert _slugify("Ander'Clim") == "Ander'clim"


def test_parse_extracts_overall_rating_and_review_count() -> None:
    ratings = _parse(_SAMPLE_HTML, "https://example.test/Groupe-Sii")

    assert ratings is not None
    assert ratings.overall_rating == 3.5
    assert ratings.review_count == 80
    assert ratings.source_url == "https://example.test/Groupe-Sii"


def test_parse_extracts_category_scores() -> None:
    ratings = _parse(_SAMPLE_HTML, "https://example.test/Groupe-Sii")

    assert ratings is not None
    assert ratings.category_scores == {
        "work_life_balance": 3.7,
        "compensation": 3.1,
        "job_security": 3.0,
        "management": 3.2,
        "culture": 3.5,
    }


def test_parse_extracts_satisfaction_percent() -> None:
    ratings = _parse(_SAMPLE_HTML, "https://example.test/Groupe-Sii")

    assert ratings is not None
    assert ratings.satisfaction_percent == 48


def test_parse_returns_none_when_no_rating_found() -> None:
    assert _parse("<html><body>Aucune donnée ici.</body></html>", "url") is None


def test_parse_returns_none_when_rating_found_but_no_review_count() -> None:
    html = "<html><body>3,5 sur 5</body></html>"
    assert _parse(html, "url") is None


def test_parse_missing_category_is_simply_absent() -> None:
    html = """
    <html><body>
    3,5 sur 5
    basée sur 80 avis
    Direction 3,2
    </body></html>
    """
    ratings = _parse(html, "url")

    assert ratings is not None
    assert ratings.category_scores == {"management": 3.2}


def test_parse_reviews_extracts_all_fields() -> None:
    reviews = _parse_reviews(_SAMPLE_REVIEWS_PAYLOAD, "https://example.test/Randstad")

    assert len(reviews) == 2
    first, second = reviews

    assert first.overall_rating == 4
    assert first.job_title == "Chargé de Recrutement (H/F)"
    assert first.location == "Rueil-Malmaison (92)"
    assert first.review_date is not None
    assert first.review_date.year == 2014
    assert first.title == "Semaine type"
    assert first.pros == ""
    assert first.cons == ""
    assert first.source_url == "https://example.test/Randstad"

    assert second.pros == "ESN très humaine, directeur et commerciaux à l'écoute"
    assert second.cons.startswith("Le pôle infra")


def test_parse_reviews_handles_empty_group() -> None:
    assert _parse_reviews({"companyName": "X", "reviewsContentGroup": []}, "url") == []


def test_parse_reviews_handles_missing_group_key() -> None:
    assert _parse_reviews({"companyName": "X"}, "url") == []
