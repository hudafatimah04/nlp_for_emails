"""Render selected PDF pages and extract text using Tesseract."""

from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any

import pymupdf
import pytesseract
from PIL import Image

from config import settings

OCR_DPI = 300
_OCR_LOCK = Lock()


def ocr_pdf_pages(path: str | Path, page_numbers: Sequence[int]) -> dict[str, Any]:
    """Return OCR text and errors for the requested one-based PDF pages."""
    pages: list[dict[str, Any]] = []
    result = {"pages": pages, "status": "failed"}
    if not page_numbers:
        return {**result, "status": "success"}

    with _OCR_LOCK:
        engine = pytesseract.pytesseract
        previous_command = engine.tesseract_cmd
        engine.tesseract_cmd = settings.TESSERACT_CMD or "tesseract"
        try:
            with pymupdf.open(path, filetype="pdf") as document:
                if document.needs_pass:
                    return {**result, "error": "PDF is password-protected."}
                for number in page_numbers:
                    page = {"page_number": number, "text": None, "error": None}
                    pages.append(page)
                    if not 1 <= number <= document.page_count:
                        page["error"] = "Page number is outside the PDF."
                        continue
                    try:
                        pixels = document[number - 1].get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csRGB)
                        with Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples) as image:
                            image.info["dpi"] = (OCR_DPI, OCR_DPI)
                            text = pytesseract.image_to_string(image, timeout=60).strip()
                        page["text"] = text or None
                        if not text:
                            page["error"] = "OCR returned no text."
                    except (pytesseract.TesseractNotFoundError, PermissionError):
                        return {**result, "error": "Tesseract is unavailable. Install it or set TESSERACT_CMD."}
                    except (OSError, RuntimeError, ValueError):
                        page["error"] = "OCR could not process this page."
        except (OSError, RuntimeError, ValueError):
            return {**result, "error": "Could not open or render the PDF for OCR."}
        finally:
            engine.tesseract_cmd = previous_command

    successes = sum(page["text"] is not None for page in pages)
    if successes == len(pages):
        result["status"] = "success"
    elif successes:
        result["status"] = "partial"
    return result
