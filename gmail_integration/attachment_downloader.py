"""Save supported Gmail attachment parts to local storage."""

import base64
import re
from itertools import count
from pathlib import Path
from typing import Any

from google.auth.exceptions import TransportError
from googleapiclient.discovery import Resource

from config import settings
from gmail_integration.email_reader import execute_request


def _safe_filename(filename: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    name = re.sub(r"\.{2,}", "_", name).strip(". ") or "attachment"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]", name.split(".")[0], re.I):
        name = "_" + name
    path = Path(name)
    return path.stem[:160] + path.suffix[:20]


def _write_unique(directory: Path, filename: str, data: bytes) -> Path:
    name = Path(filename)
    for number in count():
        candidate = directory / (f"{name.stem}_{number}{name.suffix}" if number else filename)
        try:
            stream = candidate.open("xb")
        except FileExistsError:
            continue
        try:
            with stream:
                stream.write(data)
        except OSError:
            candidate.unlink(missing_ok=True)
            raise
        return candidate


def download_attachment(
    service: Resource,
    gmail_message_id: str,
    attachment_meta: dict[str, Any],
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Save a supported part and return its original metadata and storage status."""
    filename = attachment_meta.get("filename")
    file_type = attachment_meta.get("mime_type")
    extension = Path(filename or "").suffix.lower()
    if extension in {".pdf", ".docx"} and (file_type or "").split(";", 1)[0].strip().lower() not in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        file_type = extension

    result = {
        "filename": filename,
        "file_type": file_type,
        "storage_path": None,
        "size": attachment_meta.get("size"),
        "status": "unsupported",
    }
    if attachment_meta.get("is_supported") is not True:
        return result

    result["status"] = "failed"
    directory_setting = base_dir if base_dir is not None else settings.ATTACHMENT_DIR
    if not directory_setting:
        return {**result, "error": "Set ATTACHMENT_DIR to the local attachment storage directory."}
    if not re.fullmatch(r"[A-Za-z0-9_-]+", gmail_message_id):
        return {**result, "error": "Invalid Gmail message ID for attachment storage."}

    attachment_id = attachment_meta.get("gmail_attachment_id")
    try:
        if attachment_id:
            request = service.users().messages().attachments().get(
                userId="me", messageId=gmail_message_id, id=attachment_id
            )
            encoded = execute_request(request).get("data")
        else:
            encoded = attachment_meta.get("data")
    except RuntimeError as error:
        return {**result, "error": str(error)}
    except (OSError, TransportError):
        return {**result, "error": "Could not retrieve the attachment from Gmail."}

    if not isinstance(encoded, str):
        return {**result, "error": "Gmail did not return attachment data."}
    try:
        data = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except ValueError:
        return {**result, "error": "Gmail returned invalid base64url attachment data."}

    try:
        base = Path(directory_setting).expanduser().resolve()
        directory = base / gmail_message_id
        if directory.resolve() != directory:
            return {**result, "error": "Attachment storage directory redirects to another location."}
        directory.mkdir(parents=True, exist_ok=True)
        path = _write_unique(directory, _safe_filename(filename or "attachment"), data)
    except (OSError, RuntimeError, ValueError):
        return {**result, "error": "Could not save the attachment under ATTACHMENT_DIR."}

    return {**result, "storage_path": str(path), "size": len(data), "status": "downloaded"}
