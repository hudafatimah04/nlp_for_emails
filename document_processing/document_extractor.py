"""Select a document extractor and combine PDF text and OCR results."""

from pathlib import Path
from typing import Any

from document_processing.docx_extractor import extract_docx_text
from document_processing.ocr_processor import ocr_pdf_pages
from document_processing.pdf_extractor import extract_pdf_text

_PDF_TYPES = {"pdf", ".pdf", "application/pdf"}
_DOCX_TYPES = {
    "docx", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def extract_document(path: str | Path, file_type: str | None) -> dict[str, Any]:
    """Return text, extraction method, status, successful OCR count, and warnings."""
    warnings: list[str] = []
    result = {"text": None, "method": None, "status": "failed", "pages_ocr_count": 0, "warnings": warnings}
    kind = (file_type or "").split(";", 1)[0].strip().lower()
    if kind in _DOCX_TYPES:
        docx = extract_docx_text(path)
        if docx.get("error"):
            warnings.append(docx["error"])
        return {**result, "text": docx["text"], "method": "docx", "status": docx["status"]}
    if kind not in _PDF_TYPES:
        return {**result, "status": "unsupported", "warnings": ["Unsupported document type."]}

    result["method"] = "pdf_text"
    pdf = extract_pdf_text(path)
    if pdf["status"] == "failed":
        warnings.append(pdf["error"])
        return result

    pages = pdf["pages"]
    needs_ocr = [page["page_number"] for page in pages if not page["has_text"]]
    if not needs_ocr:
        return {**result, "text": pdf["full_text"], "status": "success"}

    ocr = ocr_pdf_pages(path, needs_ocr)
    if ocr.get("error"):
        warnings.append(ocr["error"])
    recognized = {page["page_number"]: page for page in ocr["pages"]}
    texts = []
    ocr_count = 0
    used_selectable = False
    for page in pages:
        number = page["page_number"]
        ocr_page = recognized.get(number, {})
        if ocr_page.get("text"):
            texts.append(ocr_page["text"])
            ocr_count += 1
            continue
        if page["text"]:
            texts.append(page["text"])
            used_selectable = True
        if not page["has_text"]:
            reason = ocr_page.get("error") or "OCR did not return text."
            warnings.append(f"Page {number}: {reason}")

    status = "success" if ocr_count == len(needs_ocr) else ("partial" if texts else "failed")
    return {
        **result,
        "text": "\n\n".join(texts) or None,
        "method": "mixed" if used_selectable else "ocr",
        "status": status,
        "pages_ocr_count": ocr_count,
    }
