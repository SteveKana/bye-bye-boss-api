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
from app.modules.matching.cv_skill_groups import group_skills

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
    keepWithNext=1,
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
    keepWithNext=1,
)
_EXP_META_STYLE = ParagraphStyle(
    "CvExpMeta",
    parent=_STYLES["Normal"],
    fontName="Helvetica-Oblique",
    fontSize=9,
    leading=12,
    textColor=_LIGHT_GRAY,
    spaceAfter=4,
    keepWithNext=1,
)
_TOOLS_STYLE = ParagraphStyle(
    "CvTools",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9,
    textColor=_GRAY,
    leading=12,
    spaceBefore=3,
)
_SKILL_GROUP_STYLE = ParagraphStyle(
    "CvSkillGroup",
    parent=_BODY_STYLE,
    spaceAfter=3,
)
_BULLET_STYLE = ParagraphStyle(
    "CvBullet",
    parent=_STYLES["Normal"],
    fontName="Helvetica",
    fontSize=9.5,
    textColor=_GRAY,
    leading=13,
)


# The PDF uses reportlab's built-in Helvetica, which only knows the Windows
# "cp1252" character set. The LLM likes look-alike characters outside it
# (non-breaking hyphen, narrow no-break space...) which would print as a black
# square -- swapped for the plain equivalent below before anything is drawn.
_PDF_REPLACEMENTS = {
    **dict.fromkeys("\u2010\u2011\u2012\u2015\u2212\u2043\ufe63\uff0d", "-"),
    **dict.fromkeys(
        "\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000",
        " ",
    ),
    **dict.fromkeys("\u200b\u200c\u200d\u2060\ufeff\u00ad", ""),
    "\u2192": "->",
    "\u2190": "<-",
    "\u21d2": "=>",
    **dict.fromkeys("\u25cf\u25aa\u25a0\u2023\u25e6\u2219", "\u2022"),
    **dict.fromkeys("\u2018\u201b\u2032", "'"),
    "\u0142": "l",
    "\u0141": "L",
    "\u0111": "d",
    "\u0110": "D",
    "\u0131": "i",
    **dict.fromkeys("\u201f\u2033", '"'),
}


def pdf_safe(value: str | None) -> str:
    """`value` with every character Helvetica cannot draw replaced by a plain
    equivalent (see _PDF_REPLACEMENTS); anything else unsupported falls back
    to its unaccented form, or is dropped -- never a black square."""
    out: list[str] = []
    for char in value or "":
        char = _PDF_REPLACEMENTS.get(char, char)
        for c in char:
            try:
                c.encode("cp1252")
            except UnicodeEncodeError:
                c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode()
            out.append(c)
    return "".join(out)


def _text(value: str | None) -> str:
    """Escapes untrusted text (candidate CV content, LLM output) before it
    goes into a Paragraph -- reportlab's Paragraph interprets a small
    HTML-like markup, so a raw '<', '>' or '&' in the source text (a CV
    mentioning "R&D" or "C++ <embedded>" is entirely plausible) would either
    break parsing or render wrong without this."""
    return escape(pdf_safe(value).strip())


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
        # A title must never be left alone at the bottom of a page.
        before, after = Spacer(1, 10), Spacer(1, 6)
        for flowable in (before, chip, after):
            flowable.keepWithNext = 1
        return [before, chip, after]
    return [Paragraph(label.upper(), _SECTION_STYLE)]


# A French / international phone number, as written on a CV header: 06 16 53 82 61,
# 06.16.53.82.61, 0616538261, +33 6 16 53 82 61.
_PHONE = re.compile(r"(?<![\d+])(?:\+33|0033|0)[\s.\-]?[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
# The header of a CV is its first lines: further down, a number is a reference.
_PHONE_SEARCH_CHARS = 800


def candidate_phone(profile: CandidateProfile) -> str | None:
    """The phone number written at the top of the CV the candidate uploaded
    (the profile has no phone field of its own), or None."""
    match = _PHONE.search((profile.raw_text or "")[:_PHONE_SEARCH_CHARS])
    return re.sub(r"\s+", " ", match.group(0)).strip() if match else None


def _contact_parts(profile: CandidateProfile) -> list[str]:
    parts = [profile.email, candidate_phone(profile), profile.location]
    return [p for p in parts if p]


def _level_text(level: str) -> str:
    """ "Langue maternelle" -> "langue maternelle"; "B2 (TOEIC)" stays."""
    level = level.strip()
    if len(level) > 1 and level[0].isupper() and level[1].islower():
        return level[0].lower() + level[1:]
    return level


def _normalized(text: str | None) -> str:
    plain = unicodedata.normalize("NFKD", (text or "").lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _experience_tools(profile: CandidateProfile, index: int, exp: dict) -> list[str]:
    """The "Environnement technique" of an experience: the tools the profile
    holds for the experience at the same position, provided it is the same
    company (the CV never reorders or drops experiences, but a mismatch must
    never print another job's tools)."""
    experiences = profile.experiences or []
    if index >= len(experiences):
        return []
    original = experiences[index]
    if _normalized(original.get("company")) != _normalized(exp.get("company")):
        return []
    return [str(t).strip() for t in original.get("tools") or [] if str(t).strip()]


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
    canvas.drawString(left_x, name_y, pdf_safe(_candidate_name(profile)))

    headline = optimization.headline or profile.headline or ""
    if headline:
        canvas.setFillColor(_BAND_HEADLINE)
        canvas.setFont("Helvetica-Bold", 12.5)
        canvas.drawString(left_x, name_y - 8 * mm, pdf_safe(headline))

    contact_parts = _contact_parts(profile)
    if contact_parts:
        canvas.setFillColor(_BAND_CONTACT)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(left_x, name_y - 15 * mm, pdf_safe(" · ".join(contact_parts)))

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
        contact_parts = _contact_parts(profile)
        if contact_parts:
            story.append(Paragraph(_text(" · ".join(contact_parts)), _CONTACT_STYLE))
        story.append(HRFlowable(width="100%", thickness=1, color=_BRAND, spaceAfter=10))

    summary = optimization.summary or profile.professional_summary or ""
    if summary:
        story.extend(_section_title("Profil", template))
        story.append(Paragraph(_text(summary), _BODY_STYLE))

    if optimization.experiences:
        story.extend(_section_title("Expérience", template))
        for index, exp in enumerate(optimization.experiences):
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
            tools = _experience_tools(profile, index, exp)
            if tools:
                story.append(
                    Paragraph(
                        f"<b>Environnement technique :</b> {_text(', '.join(tools))}",
                        _TOOLS_STYLE,
                    )
                )

    skill_groups = group_skills(optimization.skills or [])
    if skill_groups:
        story.extend(_section_title("Compétences", template))
        for heading, names in skill_groups:
            line = _text(" · ".join(names))
            if heading:
                line = f"<b>{_text(heading)}</b> : {line}"
            story.append(Paragraph(line, _SKILL_GROUP_STYLE))

    certification_keys = [
        _normalized(c.get("title")) for c in profile.certifications or []
    ]
    formations = [
        item
        for item in profile.formations or []
        if _normalized(item.get("title")) not in certification_keys
    ]
    if formations:
        story.extend(_section_title("Formations", template))
        for item in formations:
            title = _text(item.get("title"))
            period = _text(item.get("school_period"))
            line = f"<b>{title}</b> — {period}" if period else f"<b>{title}</b>"
            story.append(Paragraph(line, _BODY_STYLE))

    if profile.languages:
        story.extend(_section_title("Langues", template))
        parts = [
            f"{item.get('name')} : {_level_text(str(item.get('level')))}"
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


def _ascii_slug(text: str, max_len: int | None = None) -> str:
    slug = unicodedata.normalize("NFKD", text)
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    if max_len:
        slug = slug[:max_len].strip("_")
    return slug


def cv_pdf_filename(profile: CandidateProfile, role: str | None = None) -> str:
    """ASCII-safe filename for the Content-Disposition header -- built
    ourselves (not echoing a user-supplied name) so it never needs the
    RFC 5987 dance a non-ASCII filename would require. `role` (the title the
    CV was adapted to, e.g. "Product Owner Data") is appended when given, so
    several CVs adapted for different offers don't all share one name."""
    name = _ascii_slug(_candidate_name(profile)) or "candidat"
    suffix = _ascii_slug(role or "", 40)
    return f"CV_{name}_{suffix}.pdf" if suffix else f"CV_{name}.pdf"
