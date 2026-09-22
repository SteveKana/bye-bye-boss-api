"""Renders a CVOptimization (plus the profile fields it doesn't touch --
formations, languages, certifications) as a downloadable PDF.

Backs the actual download behind "Créer cette variante de CV" (see
cv_optimization_routes.py) -- until now that button only recorded a
timestamp, see CVOptimization's own docstring for that earlier limitation.

Pure Python (reportlab): no system-level libraries to install on the
server, unlike an HTML-to-PDF renderer such as WeasyPrint, which needs
Cairo/Pango. One clean, sober template for this first version -- not an
attempt to reproduce the visual style of whatever the candidate originally
uploaded (see the "Adapter mon CV" conversation: an arbitrary PDF/DOCX has
no reliably extractable "style", and the optimized text rarely has the same
length as the original anyway, so reusing its exact layout would risk
overflow/misalignment). More templates can follow once this one is
validated.

Shows the OPTIMIZED wording only -- final bullet text, no added/modified
highlighting and no "why" explanations. Those belong on the comparison
screen (cv-optimise.vue); this is a real CV meant to be sent to a
recruiter, not an internal diff view.
"""

from __future__ import annotations

import io
import re
import unicodedata
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from app.modules.cv import CandidateProfile
from app.modules.matching.cv_optimization_models import CVOptimization

# Same palette as the frontend (tailwind.config.js's brand/navy colors) --
# the PDF should read as the same product, not a generic template.
_BRAND = colors.HexColor("#5B3FE8")
_NAVY = colors.HexColor("#0F0B2E")
_GRAY = colors.HexColor("#4B5563")
_LIGHT_GRAY = colors.HexColor("#9CA3AF")

_STYLES = getSampleStyleSheet()

_NAME_STYLE = ParagraphStyle(
    "CvName",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=20,
    leading=24,
    textColor=_NAVY,
    spaceAfter=4,
)
_HEADLINE_STYLE = ParagraphStyle(
    "CvHeadline",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=12.5,
    leading=16,
    textColor=_BRAND,
    spaceAfter=4,
)
_CONTACT_STYLE = ParagraphStyle(
    "CvContact",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    leading=13,
    textColor=_GRAY,
    spaceAfter=10,
)
_SECTION_STYLE = ParagraphStyle(
    "CvSection",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10.5,
    leading=13,
    textColor=_NAVY,
    spaceBefore=12,
    spaceAfter=6,
)
_BODY_STYLE = ParagraphStyle(
    "CvBody",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    textColor=_GRAY,
    leading=13,
)
_EXP_TITLE_STYLE = ParagraphStyle(
    "CvExpTitle",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=10,
    leading=13,
    textColor=_NAVY,
    spaceBefore=8,
    spaceAfter=1,
)
_EXP_META_STYLE = ParagraphStyle(
    "CvExpMeta",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    leading=12,
    textColor=_LIGHT_GRAY,
    spaceAfter=4,
)
_BULLET_STYLE = ParagraphStyle(
    "CvBullet",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    textColor=_GRAY,
    leading=13,
)


def _text(value: str | None) -> str:
    """Escapes untrusted text (candidate CV content, LLM output) before it
    goes into a Paragraph -- reportlab's Paragraph interprets a small
    HTML-like markup, so a raw '<', '>' or '&' in the source text (a CV
    mentioning "R&D" or "C++ <embedded>" is entirely plausible) would either
    break parsing or render wrong without this."""
    return escape((value or "").strip())


def _section_title(label: str) -> Paragraph:
    return Paragraph(label.upper(), _SECTION_STYLE)


def _candidate_name(profile: CandidateProfile) -> str:
    parts = [p for p in (profile.first_name, profile.last_name) if p]
    return " ".join(parts).strip() or "Candidat"


def build_cv_pdf(profile: CandidateProfile, optimization: CVOptimization) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"CV -- {_candidate_name(profile)}",
    )

    story: list = []

    story.append(Paragraph(_text(_candidate_name(profile)), _NAME_STYLE))
    headline = optimization.headline or profile.headline or ""
    if headline:
        story.append(Paragraph(_text(headline), _HEADLINE_STYLE))
    contact_parts = [p for p in (profile.email, profile.location) if p]
    if contact_parts:
        story.append(Paragraph(_text(" · ".join(contact_parts)), _CONTACT_STYLE))
    story.append(HRFlowable(width="100%", thickness=1, color=_BRAND, spaceAfter=10))

    summary = optimization.summary or profile.professional_summary or ""
    if summary:
        story.append(_section_title("Profil"))
        story.append(Paragraph(_text(summary), _BODY_STYLE))

    if optimization.experiences:
        story.append(_section_title("Expérience"))
        for exp in optimization.experiences:
            title = exp.get("title") or ""
            if title:
                story.append(Paragraph(_text(title), _EXP_TITLE_STYLE))
            meta_parts = [p for p in (exp.get("company"), exp.get("period")) if p]
            if meta_parts:
                story.append(Paragraph(_text(" · ".join(meta_parts)), _EXP_META_STYLE))
            bullets = [b.get("text") for b in exp.get("bullets") or [] if b.get("text")]
            if bullets:
                story.append(
                    ListFlowable(
                        [
                            ListItem(
                                Paragraph(_text(bullet), _BULLET_STYLE), spaceAfter=2
                            )
                            for bullet in bullets
                        ],
                        bulletType="bullet",
                        bulletFontSize=9.5,
                        bulletColor=_BRAND,
                        bulletOffsetY=-1,
                        leftIndent=12,
                        spaceBefore=2,
                    )
                )

    if optimization.skills:
        skill_names = [s.get("skill") for s in optimization.skills if s.get("skill")]
        if skill_names:
            story.append(_section_title("Compétences"))
            story.append(Paragraph(_text(" · ".join(skill_names)), _BODY_STYLE))

    if profile.formations:
        story.append(_section_title("Formations"))
        for item in profile.formations:
            title = _text(item.get("title"))
            period = _text(item.get("school_period"))
            line = f"<b>{title}</b> — {period}" if period else f"<b>{title}</b>"
            story.append(Paragraph(line, _BODY_STYLE))

    if profile.languages:
        story.append(_section_title("Langues"))
        parts = [
            f"{item.get('name')} ({item.get('level')})"
            if item.get("level")
            else item.get("name")
            for item in profile.languages
            if item.get("name")
        ]
        story.append(Paragraph(_text(" · ".join(p for p in parts if p)), _BODY_STYLE))

    if profile.certifications:
        story.append(_section_title("Certifications"))
        for item in profile.certifications:
            title = _text(item.get("title"))
            period = _text(item.get("issuer_period"))
            line = f"<b>{title}</b> — {period}" if period else f"<b>{title}</b>"
            story.append(Paragraph(line, _BODY_STYLE))

    story.append(Spacer(1, 1))
    doc.build(story)
    return buffer.getvalue()


def cv_pdf_filename(profile: CandidateProfile) -> str:
    """ASCII-safe filename for the Content-Disposition header -- built
    ourselves (not echoing a user-supplied name) so it never needs the
    RFC 5987 dance a non-ASCII filename would require."""
    slug = unicodedata.normalize("NFKD", _candidate_name(profile))
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_") or "candidat"
    return f"CV_{slug}.pdf"
