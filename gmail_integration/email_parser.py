"""Convert full Gmail message payloads into email and attachment metadata."""

import base64
import binascii
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Any

from bs4 import BeautifulSoup
from document_processing.file_types import document_kind

_TEXT_TYPES = {"text/plain", "text/html"}


def _headers(part: dict[str, Any]) -> dict[str, str]:
    return {header["name"].lower(): header["value"] for header in part.get("headers", [])}


def _decode_body(data: str, content_type: str) -> str:
    try:
        decoded = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return ""

    content_header = Message()
    content_header["Content-Type"] = content_type
    charset = content_header.get_content_charset() or "utf-8"
    try:
        return decoded.decode(charset, errors="replace")
    except LookupError:
        return decoded.decode("utf-8", errors="replace")


def _collect_parts(
    part: dict[str, Any],
    bodies: dict[str, list[str]],
    attachments: list[dict[str, Any]],
    include_attachment_data: bool,
) -> None:
    headers = _headers(part)
    mime_type = part.get("mimeType") or None
    media_type = (mime_type or "").split(";", 1)[0].strip().lower()
    filename = part.get("filename") or None
    body = part.get("body", {})
    attachment_id = body.get("attachmentId")
    disposition = headers.get("content-disposition", "").split(";", 1)[0].strip().lower()
    kind = document_kind(mime_type, filename)

    if (
        filename or disposition == "attachment" or (attachment_id and media_type not in _TEXT_TYPES)
        or (kind == "image" and disposition == "inline")
    ):
        attachment = {
            "filename": filename,
            "mime_type": mime_type,
            "size": body.get("size"),
            "gmail_attachment_id": attachment_id,
            "is_supported": kind is not None,
        }
        if include_attachment_data and attachment["is_supported"] and not attachment_id and "data" in body:
            attachment["data"] = body["data"]
        attachments.append(attachment)
        return

    if media_type in _TEXT_TYPES and body.get("data"):
        value = _decode_body(body["data"], headers.get("content-type", mime_type or ""))
        if media_type == "text/html":
            soup = BeautifulSoup(value, "html.parser")
            for element in soup.select("head, script, style, template"):
                element.decompose()
            value = " ".join(soup.get_text(" ", strip=True).split())
        if value.strip():
            bodies[media_type].append(value.strip())

    for child in part.get("parts", []):
        _collect_parts(child, bodies, attachments, include_attachment_data)


def _received_at(internal_date: str | int | None) -> str | None:
    if internal_date is None:
        return None
    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        timestamp = epoch + timedelta(milliseconds=int(internal_date))
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp.isoformat()


def parse_message(
    raw_message: dict[str, Any], *, include_attachment_data: bool = False
) -> dict[str, Any]:
    """Parse a full Gmail message without requesting external attachment data."""
    payload = raw_message.get("payload", {})
    headers = _headers(payload)
    address = parseaddr(headers.get("from", ""))[1]
    subject = headers.get("subject") or None
    if subject:
        try:
            subject = str(make_header(decode_header(subject)))
        except (LookupError, UnicodeError, ValueError):
            pass

    bodies: dict[str, list[str]] = {"text/plain": [], "text/html": []}
    attachments: list[dict[str, Any]] = []
    _collect_parts(payload, bodies, attachments, include_attachment_data)
    body_parts = bodies["text/plain"] or bodies["text/html"]

    return {
        "gmail_message_id": raw_message.get("id"),
        "message_id_header": headers.get("message-id") or None,
        "sender_email": address.lower() if "@" in address else None,
        "subject": subject,
        "body_text": "\n\n".join(body_parts) or None,
        "received_at": _received_at(raw_message.get("internalDate")),
        "attachments": attachments,
    }
