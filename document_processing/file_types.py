"""Canonical attachment types shared by the Gmail and document stages."""

from pathlib import PurePath

_TYPES = {
    "pdf": {"pdf", ".pdf", "application/pdf"},
    "docx": {"docx", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "image": {"jpg", "jpeg", "png", ".jpg", ".jpeg", ".png", "image/jpeg", "image/jpg", "image/png"},
    "zip": {"zip", ".zip", "application/zip", "application/x-zip-compressed"},
}


def document_kind(file_type: str | None, filename: str | None = None) -> str | None:
    """Resolve a recognized MIME type or attachment extension."""
    media_type = (file_type or "").split(";", 1)[0].strip().lower()
    extension = PurePath(filename or "").suffix.lower()
    for value in (media_type, extension):
        for kind, aliases in _TYPES.items():
            if value in aliases:
                return kind
    return None
