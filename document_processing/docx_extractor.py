"""Read DOCX paragraphs and tables in their document order."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from docx import Document
from docx.document import Document as WordDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.table import _Cell
from docx.text.paragraph import Paragraph
from lxml.etree import LxmlError


def _blocks(container: WordDocument | _Cell) -> Iterator[str]:
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                yield block.text.strip()
            continue
        for row in block.rows:
            seen = set()
            cells = []
            for cell in row.cells:
                if cell._tc not in seen:
                    cells.append(" / ".join(_blocks(cell)))
                    seen.add(cell._tc)
            if any(cells):
                yield " | ".join(cells)


def extract_docx_text(path: str | Path) -> dict[str, Any]:
    """Return main-document text, including paragraphs and table rows."""
    try:
        text = "\n".join(_blocks(Document(path))) or None
    except (OSError, ValueError, KeyError, BadZipFile, PackageNotFoundError, LxmlError):  # Invalid package.
        return {"text": None, "status": "failed", "error": "Could not open or read the DOCX."}
    if text is None:
        return {"text": None, "status": "failed", "error": "DOCX contains no extractable text."}
    return {"text": text, "status": "success"}
