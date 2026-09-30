from __future__ import annotations

from app.modules.matching.simplyhired_gateway import _parse, _slugify

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
