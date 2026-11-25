"""Read supported ZIP members without extracting archive paths to disk."""

import re
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import BadZipFile, ZipFile
from zlib import error as CompressionError

from document_processing.file_types import document_kind

_MAX_ENTRIES = 100
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 50 * 1024 * 1024


def extract_archive(
    path: str | Path, extract: Callable[[Path, str], dict[str, Any]]
) -> dict[str, Any]:
    """Process PDF, DOCX, JPEG, and PNG members of a bounded ZIP archive."""
    warnings: list[str] = []
    pages = 0
    texts: list[str] = []
    incomplete = False
    consumed = 0
    try:
        with ZipFile(path) as archive, TemporaryDirectory() as scratch:
            members = archive.infolist()
            if len(members) > _MAX_ENTRIES:
                raise ValueError("Too many members.")
            for index, member in enumerate(members):
                if member.is_dir():
                    continue
                name = member.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if (
                    name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", name)
                    or stat.S_ISLNK(member.external_attr >> 16)
                ):
                    warnings.append("An unsafe archive member was skipped.")
                    incomplete = True
                    continue
                extension = PurePosixPath(name).suffix.lower()
                if document_kind(extension) in {None, "zip"}:
                    continue
                if member.flag_bits & 1 or member.file_size > _MAX_MEMBER_BYTES:
                    warnings.append("An encrypted or oversized archive member was skipped.")
                    incomplete = True
                    continue
                remaining = min(_MAX_MEMBER_BYTES, _MAX_TOTAL_BYTES - consumed)
                if member.file_size > remaining:
                    warnings.append("The archive exceeded the extraction byte limit.")
                    incomplete = True
                    break
                try:
                    with archive.open(member) as source:
                        data = source.read(remaining + 1)
                    if len(data) > remaining:
                        raise ValueError("Member exceeds extraction limit.")
                    consumed += len(data)
                    local_path = Path(scratch) / f"member-{index}{extension}"
                    local_path.write_bytes(data)
                    result = extract(local_path, extension)
                except (BadZipFile, CompressionError, OSError, RuntimeError, ValueError, NotImplementedError):
                    warnings.append("An archive member could not be read.")
                    incomplete = True
                    continue
                if result["text"]:
                    texts.append(result["text"])
                pages += result["pages_ocr_count"]
                incomplete |= result["status"] != "success"
                warnings.extend(result["warnings"])
    except (BadZipFile, OSError, RuntimeError, ValueError):
        warnings.append("The ZIP archive could not be safely processed.")
        incomplete = True
    if not texts:
        warnings.append("The ZIP contained no readable supported documents.")
    status = ("partial" if incomplete else "success") if texts else "failed"
    return {
        "text": "\n\n".join(texts) or None, "method": "zip", "status": status,
        "pages_ocr_count": pages, "warnings": warnings,
    }
