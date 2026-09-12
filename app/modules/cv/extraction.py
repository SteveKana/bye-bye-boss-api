"""Raw text extraction from an uploaded CV file (PDF or DOCX).

Deliberately dumb: no layout/section understanding here — that judgment
call is delegated to the LLM in `gateway.py`, which is far better suited to
it than hand-rolled heuristics.
"""

from __future__ import annotations

import io

import docx
from pypdf import PdfReader

from app.core.exceptions import BadRequestError

SUPPORTED_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def detect_kind(filename: str, content_type: str | None) -> str:
    if content_type in SUPPORTED_CONTENT_TYPES:
        return SUPPORTED_CONTENT_TYPES[content_type]
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(".docx"):
        return "docx"
    raise BadRequestError(
        "Format non supporté. Merci d'importer un fichier PDF ou DOCX.",
        code="unsupported_file_type",
    )


def extract_text(*, filename: str, content_type: str | None, data: bytes) -> str:
    kind = detect_kind(filename, content_type)
    try:
        text = _extract_pdf(data) if kind == "pdf" else _extract_docx(data)
    except BadRequestError:
        raise
    except Exception as exc:  # pypdf/python-docx raise their own exception types
        raise BadRequestError(
            "Le fichier semble corrompu ou illisible. Merci de réessayer avec "
            "un autre export du CV.",
            code="unreadable_file",
        ) from exc
    text = text.strip()
    if not text:
        raise BadRequestError(
            "Impossible de lire le contenu du fichier — il est peut-être "
            "scanné en image ou vide.",
            code="empty_cv_content",
        )
    return text


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)
