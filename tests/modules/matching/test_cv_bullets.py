from __future__ import annotations

from app.modules.matching.cv_bullets import (
    clean_bullets,
    description_lines,
    place_mission_first,
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


def _kind(text: str, kind: str = "", **extra):
    return {**_bullet(text), "kind": kind, **extra}


def test_mission_sentences_go_first_flagged_and_kind_is_removed() -> None:
    bullets = [
        _kind("Pilotage de la qualité des données (golden source)", "task"),
        _kind("Rédaction des user stories", "task"),
        _kind(MIGRATION, "mission", original_text=GLUED),
        _kind("Migration de SAP Business Object vers Knime.", "mission"),
    ]

    placed = place_mission_first(bullets, GLUED + "\nRédaction des user stories")

    assert [b["text"] for b in placed] == [
        MIGRATION,
        "Migration de SAP Business Object vers Knime.",
        "Pilotage de la qualité des données (golden source)",
        "Rédaction des user stories",
    ]
    assert [bool(b.get("context")) for b in placed] == [True, True, False, False]
    assert all("kind" not in b for b in placed)


def test_a_task_the_model_did_not_call_a_mission_is_never_a_description() -> None:
    """The first line of the description is not assumed to be the mission:
    only what the model labelled "mission" is."""
    bullets = [
        _kind("Rédaction des spécifications fonctionnelles", "task"),
        _kind("Animation des ateliers", "task"),
        _kind("Création d'ETL via Knime"),
    ]

    placed = place_mission_first(bullets, "Rédaction des spécifications fonctionnelles")

    assert [b["text"] for b in placed] == [b["text"] for b in bullets]
    assert not any(b.get("context") for b in placed)


def test_a_mission_label_is_ignored_when_it_does_not_come_from_the_first_lines() -> (
    None
):
    bullets = [
        _kind("Création d'ETL via Knime", "mission"),
        _kind("Rédaction des spécifications fonctionnelles", "task"),
    ]
    original = (
        "Pilotage de la migration vers une solution IA\n"
        "Rédaction des spécifications fonctionnelles\n"
        "Animation d'ateliers\nCréation d'ETL via Knime"
    )

    placed = place_mission_first(bullets, original)

    assert not any(b.get("context") for b in placed)


def test_a_mission_alone_stays_a_bullet() -> None:
    bullets = [
        _kind("Création d'une plateforme de services en ligne", "mission"),
        _kind("Création d'une plateforme de services", "mission"),
    ]

    placed = place_mission_first(bullets, "Création d'une plateforme de services")

    assert not any(b.get("context") for b in placed)
    assert all("kind" not in b for b in placed)


def test_at_most_three_missions_are_accepted() -> None:
    lines = "Refonte du site web\nMigration du site web\nPilotage du site web\n"
    bullets = [
        _kind("Refonte du site web", "mission"),
        _kind("Migration du site web", "mission"),
        _kind("Pilotage du site web", "mission"),
        _kind("Suivi du site web", "mission"),
        _kind("Rédaction des spécifications", "task"),
    ]

    placed = place_mission_first(bullets, lines + "Suivi du site web")

    assert sum(1 for b in placed if b.get("context")) <= 3
