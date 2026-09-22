"""Renders a CVOptimization (plus the profile fields it doesn't touch --
formations, languages, certifications) as a downloadable PDF, in one of two
templates the candidate picks at download time -- see cv_optimization_routes.py.

Backs the actual download behind "Créer cette variante de CV" -- until now
that button only recorded a timestamp, see CVOptimization's own docstring
for that earlier limitation.

Pure Python (reportlab): no system-level libraries to install on the
server, unlike an HTML-to-PDF renderer such as WeasyPrint, which needs
Cairo/Pango. Two clean, sober-vs-visual templates rather than an attempt to
reproduce the visual style of whatever the candidate originally uploaded
(see the "Adapter mon CV" conversation: an arbitrary PDF/DOCX has no
reliably extractable "style", and the optimized text rarely has the same
length as the original anyway, so reusing its exact layout would risk
overflow/misalignment):

- "sobre": plain white background, brand-colored text accents only. The
  original, still the default.
- "visuelle": a full-width brand-purple header band (name/headline/contact
  in white, drawn directly on the canvas rather than as flowables --
  see _draw_visuelle_first_page) and colored background chips for section
  titles, for a candidate who wants something with more visual presence.

Both templates share the same content-building logic below (same sections,
same order, same escaping) -- only the header and section-title rendering
differ, via the `template` parameter threaded through build_cv_pdf.

Shows the OPTIMIZED wording only -- final bullet text, no added/modified
highlighting and no "why" explanations. Those belong on the comparison
screen (cv-optimise.vue); this is a real CV meant to be sent to a
recruiter, not an internal diff view.
"""

from __future__ import annotations

import functools
import io
import re
import unicodedata
from typing import Literal
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
    Table,
    TableStyle,
)

from app.modules.cv import CandidateProfile
from app.modules.matching.cv_optimization_models import CVOptimization

# The two templates a candidate can choose from when downloading. CvTemplate
# is the single source of truth for the allowed values -- the route imports
# it too, rather than keeping a second Literal in sync by hand. A third
# option can be added later once these two have been validated with real
# candidates.
CvTemplate = Literal["sobre", "visuelle"]
CV_TEMPLATES: tuple[CvTemplate, ...] = ("sobre", "visuelle")
DEFAULT_CV_TEMPLATE: CvTemplate = "sobre"

# Same palette as the frontend (tailwind.config.js's brand/navy colors) --
# the PDF should read as the same product, not a generic template.
_BRAND = colors.HexColor("#5B3FE8")
_NAVY = colors.HexColor("#0F0B2E")
_GRAY = colors.HexColor("#4B5563")
_LIGHT_GRAY = colors.HexColor("#9CA3AF")
_BAND_HEADLINE = colors.HexColor("#E8E4FB")
_BAND_CONTACT = colors.HexColor("#D8D2F5")

# Height of the "visuelle" template's colored header band, and the thin
# strip repeated at the top of any later page for visual continuity.
_VISUELLE_BAND_HEIGHT = 40 * mm
_VISUELLE_CONTINUATION_STRIP_HEIGHT = 4

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
_SECTION_CHIP_STYLE = ParagraphStyle(
    "CvSectionChip",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9.5,
    leading=12,
    textColor=colors.white,
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


def _section_title(label: str, template: str) -> list:
    """One or more flowables for a section title. 'sobre' is a single
    colored line of text (spacing handled by _SECTION_STYLE itself).
    'visuelle' is a colored background chip -- Table flowables don't
    reliably honor spaceBefore/spaceAfter the way Paragraph does, so its
    spacing is added explicitly via Spacers instead."""
    if template == "visuelle":
        chip = Table([[Paragraph(label.upper(), _SECTION_CHIP_STYLE)]], hAlign="LEFT")
        chip.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _BRAND),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return [Spacer(1, 10), chip, Spacer(1, 6)]
    return [Paragraph(label.upper(), _SECTION_STYLE)]


def _candidate_name(profile: CandidateProfile) -> str:
    parts = [p for p in (profile.first_name, profile.last_name) if p]
    return " ".join(parts).strip() or "Candidat"


def _draw_visuelle_first_page(
    canvas, doc, *, profile: CandidateProfile, optimization: CVOptimization
) -> None:
    """Draws the 'visuelle' template's header directly on the canvas rather
    than as flowables, so it can be a true full-width colored band (a
    flowable is confined to the page's margins). Name/headline/contact are
    plain canvas text here, not Paragraph markup, so they don't need
    _text()'s HTML-escaping -- only Paragraph's mini-parser needs that."""
    canvas.saveState()
    page_w, page_h = A4
    canvas.setFillColor(_BRAND)
    canvas.rect(
        0,
        page_h - _VISUELLE_BAND_HEIGHT,
        page_w,
        _VISUELLE_BAND_HEIGHT,
        fill=1,
        stroke=0,
    )

    left_x = doc.leftMargin
    name_y = page_h - 16 * mm
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(left_x, name_y, _candidate_name(profile))

    headline = optimization.headline or profile.headline or ""
    if headline:
        canvas.setFillColor(_BAND_HEADLINE)
        canvas.setFont("Helvetica-Bold", 12.5)
        canvas.drawString(left_x, name_y - 8 * mm, headline)

    contact_parts = [p for p in (profile.email, profile.location) if p]
    if contact_parts:
        canvas.setFillColor(_BAND_CONTACT)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(left_x, name_y - 15 * mm, " · ".join(contact_parts))

    canvas.restoreState()


def _draw_visuelle_later_page(canvas, doc) -> None:
    """A thin brand-colored strip at the top of any page after the first --
    visual continuity without repeating the full name/headline band (most
    CVs are one page anyway; this only matters for longer profiles)."""
    canvas.saveState()
    page_w, page_h = A4
    canvas.setFillColor(_BRAND)
    canvas.rect(
        0,
        page_h - _VISUELLE_CONTINUATION_STRIP_HEIGHT,
        page_w,
        _VISUELLE_CONTINUATION_STRIP_HEIGHT,
        fill=1,
        stroke=0,
    )
    canvas.restoreState()


def build_cv_pdf(
    profile: CandidateProfile,
    optimization: CVOptimization,
    template: CvTemplate = DEFAULT_CV_TEMPLATE,
) -> bytes:
    if template not in CV_TEMPLATES:
        raise ValueError(f"Modèle de CV inconnu : {template!r}")

    buffer = io.BytesIO()
    top_margin = (
        (_VISUELLE_BAND_HEIGHT + 10 * mm) if template == "visuelle" else 18 * mm
    )
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=top_margin,
        bottomMargin=18 * mm,
        title=f"CV -- {_candidate_name(profile)}",
    )

    story: list = []

    if template == "sobre":
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
        story.extend(_section_title("Profil", template))
        story.append(Paragraph(_text(summary), _BODY_STYLE))

    if optimization.experiences:
        story.extend(_section_title("Expérience", template))
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
            story.extend(_section_title("Compétences", template))
            story.append(Paragraph(_text(" · ".join(skill_names)), _BODY_STYLE))

    if profile.formations:
        story.extend(_section_title("Formations", template))
        for item in profile.formations:
            title = _text(item.get("title"))
            period = _text(item.get("school_period"))
            line = f"<b>{title}</b> — {period}" if period else f"<b>{title}</b>"
            story.append(Paragraph(line, _BODY_STYLE))

    if profile.languages:
        story.extend(_section_title("Langues", template))
        parts = [
            f"{item.get('name')} ({item.get('level')})"
            if item.get("level")
            else item.get("name")
            for item in profile.languages
            if item.get("name")
        ]
        story.append(Paragraph(_text(" · ".join(p for p in parts if p)), _BODY_STYLE))

    if profile.certifications:
        story.extend(_section_title("Certifications", template))
        for item in profile.certifications:
            title = _text(item.get("title"))
            period = _text(item.get("issuer_period"))
            line = f"<b>{title}</b> — {period}" if period else f"<b>{title}</b>"
            story.append(Paragraph(line, _BODY_STYLE))

    story.append(Spacer(1, 1))

    if template == "visuelle":
        doc.build(
            story,
            onFirstPage=functools.partial(
                _draw_visuelle_first_page, profile=profile, optimization=optimization
            ),
            onLaterPages=_draw_visuelle_later_page,
        )
    else:
        doc.build(story)

    return buffer.getvalue()


def cv_pdf_filename(profile: CandidateProfile) -> str:
    """ASCII-safe filename for the Content-Disposition header -- built
    ourselves (not echoing a user-supplied name) so it never needs the
    RFC 5987 dance a non-ASCII filename would require. Same regardless of
    template -- only one file is ever downloaded at a time, so there's no
    collision to disambiguate."""
    slug = unicodedata.normalize("NFKD", _candidate_name(profile))
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_") or "candidat"
    return f"CV_{slug}.pdf"
