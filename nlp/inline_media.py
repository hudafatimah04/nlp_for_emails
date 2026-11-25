"""Validate local images and PDFs before sending inline Gemini parts."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

import pymupdf
from PIL import Image

MAX_FILE_BYTES = 6 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_IMAGE_PIXELS = 24_000_000
MAX_REQUEST_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class InlineFile:
    """A downloaded original selected for native processing."""

    path: Path
    kind: Literal["image", "pdf"]


@dataclass(frozen=True)
class InlineMedia:
    """Validated file data and its citation bounds."""

    data: bytes
    mime_type: str
    page_count: int | None = None

    @property
    def encoded_size(self) -> int:
        """Return the exact base64 length of the binary data."""
        return 4 * ((len(self.data) + 2) // 3)


def read_inline_file(file: InlineFile) -> InlineMedia:
    """Read a bounded file and validate the actual format, not its extension."""
    if file.kind not in ("image", "pdf"):
        raise ValueError("unsupported inline file type")
    try:
        with file.path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ValueError("original file could not be read") from None
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"original file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MiB inline file limit")
    if file.kind == "pdf":
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if not document.is_pdf or document.needs_pass or not document.page_count:
                    raise ValueError("invalid, empty, or password-protected PDF")
                if document.page_count > MAX_PDF_PAGES:
                    raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page inline limit")
                return InlineMedia(data, "application/pdf", document.page_count)
        except (RuntimeError, TypeError):
            raise ValueError("invalid or unreadable PDF") from None

    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG"]) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("image exceeds the inline pixel limit")
            mime_type = "image/jpeg" if image.format == "JPEG" else "image/png"
            image.verify()
            return InlineMedia(data, mime_type)
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError("invalid image or unsupported image format (expected JPEG or PNG)") from None
