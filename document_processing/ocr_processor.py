"""Render selected PDF pages and extract text using Tesseract."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

import pymupdf
import pytesseract
from PIL import Image, ImageOps

from config import settings

# Higher-resolution grayscale preserved small text and URLs in the demo scan.
OCR_DPI = 425
_OCR_LOCK = Lock()
_MAX_IMAGE_PIXELS = 24_000_000


@contextmanager
def _tesseract_command() -> Iterator[None]:
    with _OCR_LOCK:
        engine = pytesseract.pytesseract
        previous_command = engine.tesseract_cmd
        engine.tesseract_cmd = settings.TESSERACT_CMD or "tesseract"
        try:
            yield
        finally:
            engine.tesseract_cmd = previous_command


def ocr_image(path: str | Path) -> dict[str, Any]:
    """Read a JPEG or PNG directly, respecting its EXIF orientation."""
    text = None
    warnings: list[str] = []
    try:
        with Image.open(path, formats=["JPEG", "PNG"]) as original:
            if original.width * original.height > _MAX_IMAGE_PIXELS:
                raise ValueError("Image exceeds the OCR pixel limit.")
            with ImageOps.exif_transpose(original) as oriented, oriented.convert("RGBA") as rgba:
                with Image.new("RGBA", rgba.size, "white") as background:
                    background.alpha_composite(rgba)
                    with background.convert("L") as gray, ImageOps.autocontrast(gray) as image:
                        with _tesseract_command():
                            text = pytesseract.image_to_string(image, timeout=60).strip() or None
        if not text:
            warnings.append("Image OCR returned no text.")
    except (pytesseract.TesseractNotFoundError, PermissionError):
        warnings.append("Tesseract is unavailable. Install it or set TESSERACT_CMD.")
    except (OSError, RuntimeError, ValueError, Image.DecompressionBombError):
        warnings.append("Could not read or OCR the image.")
    return {
        "text": text, "method": "ocr", "status": "success" if text else "failed",
        "pages_ocr_count": int(bool(text)), "warnings": warnings,
    }


def ocr_pdf_pages(path: str | Path, page_numbers: Sequence[int]) -> dict[str, Any]:
    """Return OCR text and errors for the requested one-based PDF pages."""
    pages: list[dict[str, Any]] = []
    result = {"pages": pages, "status": "failed"}
    if not page_numbers:
        return {**result, "status": "success"}

    with _tesseract_command():
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
                        pixels = document[number - 1].get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY)
                        with Image.frombytes("L", (pixels.width, pixels.height), pixels.samples) as grayscale:
                            with ImageOps.autocontrast(grayscale) as image:
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

    successes = sum(page["text"] is not None for page in pages)
    if successes == len(pages):
        result["status"] = "success"
    elif successes:
        result["status"] = "partial"
    return result
