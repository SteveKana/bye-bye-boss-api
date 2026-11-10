from __future__ import annotations

from app.modules.matching.cv_skill_groups import group_skills, keyword_heading


def test_keyword_heading_knows_the_usual_it_and_product_vocabulary() -> None:
    assert keyword_heading("SQL (requêtage, analyse)") == "Data & BI"
    assert keyword_heading("Power BI") == "Data & BI"
    assert keyword_heading("Rédaction de user stories") == "Méthodes Agile"
    assert keyword_heading("SAFe 5") == "Méthodes Agile"
    assert keyword_heading("Google Cloud Platform") == "Langages & outils"
    assert keyword_heading("Conduite du changement") == "Pilotage de projet"
    assert keyword_heading("Reporting d'avancement (COPROJ)") == "Pilotage de projet"
    assert keyword_heading("Cuisine italienne") == ""


def test_keyword_heading_does_not_match_inside_a_word() -> None:
    # "bi" must not match "abilité", "git" must not match "digital".
    assert keyword_heading("Habilité électrique") == ""
    assert keyword_heading("Marketing digital") == ""


def test_group_skills_uses_the_model_heading_first_then_the_keywords() -> None:
    groups = group_skills(
        [
            {"skill": "Jira", "category": "Outils produit"},
            {"skill": "SQL"},
            {"skill": "Confluence", "category": "Outils produit"},
            {"skill": "Cuisine italienne"},
        ]
    )

    assert groups == [
        ("Outils produit", ["Jira", "Confluence"]),
        ("Data & BI", ["SQL"]),
        ("Autres compétences", ["Cuisine italienne"]),
    ]


def test_group_skills_keeps_the_order_of_first_appearance() -> None:
    groups = group_skills([{"skill": "Scrum"}, {"skill": "SQL"}, {"skill": "Kanban"}])

    assert groups == [("Méthodes Agile", ["Scrum", "Kanban"]), ("Data & BI", ["SQL"])]


def test_group_skills_gives_a_flat_list_when_one_heading_would_hold_everything() -> (
    None
):
    assert group_skills([{"skill": "Scrum"}, {"skill": "Kanban"}]) == [
        ("", ["Scrum", "Kanban"])
    ]
    assert group_skills([{"skill": "Cuisine"}, {"skill": "Danse"}]) == [
        ("", ["Cuisine", "Danse"])
    ]


def test_group_skills_ignores_blank_skills_and_cleans_labels() -> None:
    assert group_skills([{"skill": "  "}, {"skill": ""}, {}]) == []
    assert group_skills([{"skill": "roadmap_planning"}]) == [("", ["Roadmap planning"])]
