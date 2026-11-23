"""Extract selectable text from individual PDF pages."""

from pathlib import Path
from typing import Any

import pymupdf

MIN_SELECTABLE_CHARACTERS = 30


def extract_pdf_text(path: str | Path) -> dict[str, Any]:
    """Return one-based page results and selectable text in document order."""
    pages: list[dict[str, Any]] = []
    result = {"pages": pages, "full_text": None, "status": "failed"}
    try:
        with pymupdf.open(path, filetype="pdf") as document:
            if document.needs_pass:
                return {**result, "error": "PDF is password-protected."}
            if not document.page_count:
                return {**result, "error": "PDF has no pages."}
            for page_number in range(1, document.page_count + 1):
                try:
                    text = document[page_number - 1].get_text().strip()
                except (RuntimeError, ValueError):
                    pages.append({
                        "page_number": page_number, "text": None, "char_count": 0,
                        "has_text": False, "error": "Could not read selectable text on this page.",
                    })
                    continue
                char_count = sum(not character.isspace() for character in text)
                pages.append({
                    "page_number": page_number, "text": text or None, "char_count": char_count,
                    "has_text": char_count >= MIN_SELECTABLE_CHARACTERS,
                })
    except (OSError, RuntimeError, ValueError):
        return {**result, "error": "Could not open or read the PDF."}

    full_text = "\n\n".join(page["text"] for page in pages if page["text"]) or None
    return {
        **result,
        "full_text": full_text,
        "status": "success" if all(page["has_text"] for page in pages) else "partial",
    }
