"""The fixed list of job families and specialties every offer and every
profile is filed under (Steve, 2026-11-02: "Développeur" or "Communication"
alone mean nothing, so the AI picks a precise specialty from this list
instead of a keyword from the title).

The list is deliberately closed: the AI must choose from it, anything that
fits nowhere goes to "Autre" (reviewed monthly to decide whether to add a
case). Keeping it closed is what makes weekly counts comparable over time.
"""

from __future__ import annotations

OTHER_FAMILY = "Autre"

FAMILIES: dict[str, list[str]] = {
    "Produit & projet": [
        "Product Owner",
        "Product Manager",
        "Chef de projet IT",
        "Chef de projet (hors IT)",
        "Business Analyst / AMOA",
        "Scrum Master / Coach agile",
        "PMO / Gestion de portefeuille",
    ],
    "Développement logiciel": [
        "Développeur Front-end",
        "Développeur Back-end",
        "Développeur Fullstack",
        "Développeur Mobile",
        "DevOps / Cloud",
        "Test / QA",
        "Architecte logiciel",
        "Intégrateur ERP / progiciel",
    ],
    "Data & IA": [
        "Data Analyst",
        "Data Scientist",
        "Data Engineer",
        "BI / Reporting",
        "IA / Machine Learning",
    ],
    "Systèmes, réseaux & cybersécurité": [
        "Administrateur systèmes",
        "Ingénieur réseaux",
        "Cybersécurité",
        "Support / Helpdesk",
        "Infrastructure / Cloud",
    ],
    "Marketing": [
        "Marketing digital (SEO / SEA)",
        "Chef de produit marketing",
        "Marketing de contenu",
        "CRM / Growth",
        "Marketing opérationnel",
    ],
    "Communication": [
        "Communication digitale / réseaux sociaux",
        "Community manager",
        "Communication interne",
        "Relations presse / RP",
        "Communication événementielle",
        "Communication corporate",
    ],
    "Commercial & vente": [
        "Commercial B2B / Business developer",
        "Technico-commercial",
        "Ingénieur d'affaires",
        "Account manager / Customer success",
        "Commercial sédentaire / Télévente",
    ],
    "Commerce & distribution": [
        "Vendeur / Conseiller de vente",
        "Employé de libre-service / Caissier",
        "Responsable de magasin / rayon",
    ],
    "Comptabilité & finance": [
        "Comptable",
        "Contrôleur de gestion",
        "Audit / Contrôle interne",
        "Trésorerie",
        "Analyste financier",
    ],
    "Ressources humaines": [
        "Paie",
        "Recrutement",
        "Gestionnaire RH / Administration du personnel",
        "Formation",
        "Responsable RH / Business partner RH",
    ],
    "Administratif & secrétariat": [
        "Assistant(e) administratif(ve)",
        "Assistant(e) de direction",
        "Secrétaire / Accueil",
        "Gestionnaire administratif",
    ],
    "Logistique & transport": [
        "Magasinier / Cariste",
        "Préparateur de commandes",
        "Responsable logistique / Supply chain",
        "Chauffeur / Livreur",
    ],
    "Industrie & production": [
        "Opérateur de production",
        "Technicien de maintenance",
        "Responsable de production",
        "Méthodes / Industrialisation",
    ],
    "Qualité, sécurité & environnement": [
        "Qualité",
        "QSE / HSE",
        "Conformité / Réglementaire",
    ],
    "Juridique": [
        "Juriste",
        "Juriste droit social",
        "Assistant(e) juridique",
    ],
    "Santé & social": [
        "Infirmier(ère)",
        "Aide-soignant(e)",
        "Pharmacie",
        "Éducateur / Travailleur social",
        "Aide à domicile",
    ],
    "Éducation & formation": [
        "Enseignant(e)",
        "Formateur(trice)",
    ],
    "BTP & immobilier": [
        "Conducteur de travaux / Chef de chantier",
        "Bureau d'études / Dessinateur",
        "Agent / Négociateur immobilier",
        "Gestion locative / Syndic",
    ],
    "Hôtellerie, restauration & tourisme": [
        "Cuisinier",
        "Serveur / Commis de salle",
        "Réception / Hôtellerie",
        "Responsable de restauration",
    ],
    "Propreté, sécurité & services": [
        "Agent d'entretien / Propreté",
        "Agent de sécurité",
        "Services à la personne",
    ],
    "Design & création": [
        "UX / UI Designer",
        "Graphiste",
        "Vidéo / Motion design",
        "Rédacteur / Content writer",
    ],
    "Conseil & stratégie": [
        "Consultant en transformation",
        "Consultant SI / digital",
        "Consultant en organisation",
    ],
    "Direction & management": [
        "Direction générale",
        "Responsable d'équipe / Manager",
    ],
    OTHER_FAMILY: [],
}

SKILL_CATEGORIES = ("outil", "technologie", "certification", "methode", "metier")

SKILL_CATEGORY_LABELS = {
    "outil": "Outil",
    "technologie": "Technologie",
    "certification": "Certification",
    "methode": "Méthode",
    "metier": "Savoir-faire métier",
}


def is_valid(family: str, specialty: str | None) -> bool:
    return family in FAMILIES and (specialty is None or specialty in FAMILIES[family])


def normalize(family: str | None, specialty: str | None) -> tuple[str, str | None]:
    """Coerces whatever the model answered onto the closed list: an unknown
    family becomes "Autre", an unknown specialty is dropped (the offer then
    counts at the family level only)."""
    fam = (family or "").strip()
    if fam not in FAMILIES:
        return OTHER_FAMILY, None
    spec = (specialty or "").strip() or None
    if spec not in FAMILIES[fam]:
        return fam, None
    return fam, spec


def taxonomy_for_prompt() -> str:
    lines = []
    for family, specialties in FAMILIES.items():
        if specialties:
            lines.append(f"- {family} : " + " | ".join(specialties))
        else:
            lines.append(f"- {family} (si rien d'autre ne convient)")
    return "\n".join(lines)
