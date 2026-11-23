"""Command-line entry point for the opportunity inbox reader."""

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from googleapiclient.discovery import Resource

from config import settings
from document_processing.document_extractor import extract_document
from gmail_integration.attachment_downloader import download_attachment
from gmail_integration.authentication import get_gmail_service
from gmail_integration.email_parser import parse_message
from gmail_integration.email_reader import get_message, list_message_ids


def _print_extractions(
    service: Resource, message_id: str, message: dict[str, Any], show_text: bool
) -> None:
    for attachment in message["attachments"]:
        download = download_attachment(service, message_id, attachment)
        summary = {
            "subject": message["subject"], "filename": download["filename"],
            "method": None, "status": download["status"], "extracted_character_count": 0,
        }
        if download["status"] == "downloaded":
            extraction = extract_document(download["storage_path"], download["file_type"])
            text = extraction["text"] or ""
            summary.update(
                method=extraction["method"], status=extraction["status"],
                extracted_character_count=len(text),
            )
            if extraction["warnings"]:
                summary["warnings"] = extraction["warnings"]
            if show_text:
                summary["text_preview"] = text[:200]
        elif download.get("error"):
            summary["error"] = download["error"]
        print(json.dumps(summary))


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch inbox messages or extract their supported attachments."""
    parser = argparse.ArgumentParser(description="Read the configured Gmail inbox.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch recent inbox messages.")
    fetch.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    fetch.add_argument("--show-body", action="store_true", help="Print up to 500 body characters.")
    extract = commands.add_parser("extract", help="Extract text from recent inbox attachments.")
    extract.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    extract.add_argument("--show-text", action="store_true", help="Print up to 200 extracted characters.")
    args = parser.parse_args(argv)
    if args.max < 1:
        parser.error("--max must be a positive integer")

    try:
        service = get_gmail_service()
        for message_id in list_message_ids(service, settings.ALLOWED_SENDERS, args.max):
            raw = get_message(service, message_id)
            if args.command == "extract":
                message = parse_message(raw, include_attachment_data=True)
                _print_extractions(service, message_id, message, args.show_text)
                continue
            summary = parse_message(raw)
            body = summary.pop("body_text") or ""
            summary["body_character_count"] = len(body)
            if args.show_body:
                summary["body_preview"] = body[:500]
            print(json.dumps(summary))
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
