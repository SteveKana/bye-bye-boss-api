from __future__ import annotations

from app.modules.matching.cv_bullets import (
    clean_bullets,
    description_lines,
    pin_context_first,
    similarity,
)

GLUED = (
    "Refonte technique de l'application historique|Pilotage de la migration de "
    "l'application de préconisation des fonds de rayon à destination des "
    "adhérents E. Leclerc vers une solution intégrant de l'intelligence "
    "artificielle, afin d'optimiser les préconisations en fonction des "
    "comportements clients (tickets de caisse) | Migration de SAP Business "
    "Object vers Knime"
)
MIGRATION = (
    "Pilotage de la migration de l’application de préconisation des fonds de "
    "rayon destinée aux adhérents E. Leclerc vers une solution intégrant de "
    "l’intelligence artificielle, afin d’optimiser les préconisations selon les "
    "comportements clients (tickets de caisse)."
)


def _bullet(text: str, status: str = "unchanged", original: str | None = None):
    return {"text": text, "status": status, "original_text": original, "why": ""}


def test_similarity_ignores_case_accents_and_punctuation() -> None:
    assert similarity("Création d'ETL via Knime.", "création d’etl VIA knime") == 1.0
    assert similarity("Power BI", "Rédaction des spécifications") == 0.0
    assert similarity("", "x y z") == 0.0


def test_description_lines_cut_glued_lines_and_drop_repeats() -> None:
    lines = description_lines(f"- Qualité des données\n• {GLUED}\nQualité des données")

    assert lines[0] == "Qualité des données"
    assert "Refonte technique de l'application historique" in lines
    assert "Migration de SAP Business Object vers Knime" in lines
    assert all("|" not in line for line in lines)
    assert lines.count("Qualité des données") == 1


def test_clean_bullets_removes_the_glued_copy_of_bullets_already_present() -> None:
    bullets = [
        _bullet(MIGRATION, "modified", GLUED),
        _bullet("Migration de SAP Business Object vers Knime."),
        _bullet("Refonte technique de l’application historique."),
        _bullet(GLUED),
    ]

    texts = [b["text"] for b in clean_bullets(bullets)]

    assert texts == [
        MIGRATION,
        "Migration de SAP Business Object vers Knime.",
        "Refonte technique de l’application historique.",
    ]


def test_clean_bullets_keeps_the_new_facts_of_a_glued_line() -> None:
    texts = [
        b["text"]
        for b in clean_bullets([_bullet("Recette | Mise en production Knime")])
    ]

    assert texts == ["Recette", "Mise en production Knime"]


def test_clean_bullets_keeps_distinct_bullets_that_share_a_few_words() -> None:
    bullets = [
        _bullet("Rédaction des spécifications fonctionnelles"),
        _bullet("Rédaction des spécifications fonctionnelles et techniques"),
        _bullet("Rédaction des user stories"),
    ]

    assert len(clean_bullets(bullets)) == 3


def test_clean_bullets_drops_a_reformulation_repeated_verbatim() -> None:
    bullets = [
        _bullet("Animation des ateliers de recueil des besoins métiers", "modified"),
        _bullet("Animation des ateliers de recueil des besoins métiers."),
    ]

    assert len(clean_bullets(bullets)) == 1


def test_pin_context_first_puts_the_context_of_the_job_on_top() -> None:
    bullets = [
        _bullet("Pilotage de la qualité des données (golden source)", "modified"),
        _bullet("Rédaction des user stories"),
        _bullet(MIGRATION, "modified", GLUED),
        _bullet("Migration de SAP Business Object vers Knime."),
    ]

    pinned = pin_context_first(bullets, GLUED)
    texts = [b["text"] for b in pinned]
    # The pinned bullets are flagged: the PDF prints them as a description.
    assert [bool(b.get("context")) for b in pinned] == [True, True, False, False]

    assert texts[0] == MIGRATION
    assert texts[1] == "Migration de SAP Business Object vers Knime."
    assert texts[2:] == [
        "Pilotage de la qualité des données (golden source)",
        "Rédaction des user stories",
    ]


def test_pin_context_first_leaves_the_list_alone_without_a_match() -> None:
    bullets = [_bullet("Power BI"), _bullet("Excel")]

    assert pin_context_first(bullets, "Création d'une plateforme") == bullets
    assert pin_context_first(bullets, "") == bullets
    assert pin_context_first(bullets[:1], "Power BI") == bullets[:1]


def test_a_lone_context_bullet_stays_a_bullet() -> None:
    bullets = [_bullet("Création d'une plateforme de services en ligne")]

    assert pin_context_first(bullets, "Création d'une plateforme de services") == (
        bullets
    )
    only_context = [
        _bullet("Création d'une plateforme de services en ligne"),
        _bullet("Création d'une plateforme de services"),
    ]
    assert not any(
        b.get("context")
        for b in pin_context_first(
            only_context, "Création d'une plateforme de services"
        )
    )
