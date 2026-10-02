from __future__ import annotations

import pytest

from app.core.exceptions import BadRequestError
from app.modules.cv import extraction


def test_strips_nul_bytes_from_extracted_pdf_text(monkeypatch) -> None:
    # Real-world case: pypdf emitted a literal NUL byte for a malformed
    # PDF, which Postgres then rejected outright
    # (CharacterNotInRepertoireError: invalid byte sequence for encoding
    # "UTF8": 0x00) when the upload tried to store it.
    monkeypatch.setattr(
        extraction, "_extract_pdf", lambda _data: "Jean Dupont\nExpérience\x002024\x00"
    )
    text, kind = extraction.extract_text(
        filename="cv.pdf", content_type="application/pdf", data=b"fake"
    )
    assert "\x00" not in text
    assert text == "Jean Dupont\nExpérience2024"
    assert kind == "pdf"


def test_strips_nul_bytes_from_extracted_docx_text(monkeypatch) -> None:
    monkeypatch.setattr(extraction, "_extract_docx", lambda _data: "Marie\x00 Curie")
    text, _kind = extraction.extract_text(
        filename="cv.docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        data=b"fake",
    )
    assert text == "Marie Curie"


def test_still_raises_on_empty_content_after_stripping(monkeypatch) -> None:
    # A PDF that was nothing but NUL bytes (or whitespace) must still hit
    # the existing empty-content error, not silently succeed with "".
    monkeypatch.setattr(extraction, "_extract_pdf", lambda _data: "\x00\x00  \x00")
    with pytest.raises(BadRequestError) as exc_info:
        extraction.extract_text(
            filename="cv.pdf", content_type="application/pdf", data=b"fake"
        )
    assert exc_info.value.code == "empty_cv_content"
