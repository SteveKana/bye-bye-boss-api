"""Raw text extraction from an uploaded CV file (PDF or DOCX).

Deliberately dumb: no layout/section understanding here — that judgment
call is delegated to the LLM in `gateway.py`, which is far better suited to
it than hand-rolled heuristics.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import docx
from pypdf import PdfReader

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError

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


def extract_text(
    *, filename: str, content_type: str | None, data: bytes
) -> tuple[str, str]:
    """Returns (text, kind) -- kind ('pdf'/'docx') is what the caller needs
    to save the original file under the right extension."""
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
    return text, kind


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


def _storage_path(user_id: uuid.UUID, kind: str) -> Path:
    return Path(get_settings().CV_UPLOAD_DIR) / f"{user_id}.{kind}"


def save_original_file(*, user_id: uuid.UUID, kind: str, data: bytes) -> None:
    """Persist the raw uploaded file so it can be served back on download.

    One file per user, named by kind -- overwritten on re-import (a new CV
    replaces the old one, same as every other field). Any stale file left
    over from a previous upload of a *different* kind (e.g. re-importing a
    .docx after a .pdf) is cleaned up so there's never more than one file
    per user lying around.
    """
    upload_dir = Path(get_settings().CV_UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for other in SUPPORTED_CONTENT_TYPES.values():
        if other != kind:
            _storage_path(user_id, other).unlink(missing_ok=True)
    _storage_path(user_id, kind).write_bytes(data)


def stored_file_path(user_id: uuid.UUID, kind: str) -> Path:
    path = _storage_path(user_id, kind)
    if not path.is_file():
        raise NotFoundError("Aucun CV importé.")
    return path
