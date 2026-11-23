"""Command-line entry point for the opportunity inbox reader."""

import argparse
import json
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from googleapiclient.discovery import Resource

from config import settings
from document_processing.document_extractor import extract_document
from gmail_integration.attachment_downloader import download_attachment
from gmail_integration.authentication import get_gmail_service
from gmail_integration.email_parser import parse_message
from gmail_integration.email_reader import get_message, list_message_ids
from nlp.extraction_pipeline import build_opportunities
from nlp.opportunity_classifier import classify_opportunity
from output.json_formatter import format_opportunity
from storage.processed_store import is_processed, mark_processed
from validation.data_validator import validate_opportunity


def _attachment_results(
    service: Resource, message_id: str, message: dict[str, Any]
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    for attachment in message["attachments"]:
        download = download_attachment(service, message_id, attachment)
        extraction = None
        if download["status"] == "downloaded":
            extraction = extract_document(download["storage_path"], download["file_type"])
        yield download, extraction


def _print_extractions(
    service: Resource, message_id: str, message: dict[str, Any], show_text: bool
) -> None:
    for download, extraction in _attachment_results(service, message_id, message):
        summary = {
            "subject": message["subject"], "filename": download["filename"],
            "method": None, "status": download["status"], "extracted_character_count": 0,
        }
        if extraction is not None:
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


def _classify_email(
    service: Resource, message_id: str, *, reprocess: bool, dry_run: bool, explain: bool
) -> bool:
    if not reprocess and is_processed(message_id, None):
        print("skipped (already processed)")
        return True

    message = parse_message(get_message(service, message_id), include_attachment_data=True)
    header = message["message_id_header"]
    if not reprocess and header and is_processed(message_id, header):
        print("skipped (already processed)")
        return True

    texts, _, incomplete = _classification_inputs(service, message_id, message)
    result = classify_opportunity(message["subject"], message["body_text"], texts)
    if not explain:
        for document in result["documents"]:
            document.pop("category_scores")
    if incomplete:
        result["requires_review"] = True
        result["review_reasons"].append("Attachment processing was incomplete. The email can be retried.")
    print(json.dumps({"gmail_message_id": message_id, "subject": message["subject"], **result}))
    if not dry_run and not incomplete:
        mark_processed(message_id, header, result["classification"])
    return not incomplete


def _classification_inputs(
    service: Resource, message_id: str, message: dict[str, Any]
) -> tuple[list[tuple[str | None, str | None]], list[dict[str, Any]], bool]:
    texts = []
    metadata = []
    incomplete = False
    for attachment, (download, extraction) in zip(
        message["attachments"], _attachment_results(service, message_id, message), strict=True
    ):
        texts.append((attachment["filename"], extraction["text"] if extraction else None))
        metadata.append({
            "filename": attachment["filename"],
            "file_type": download.get("file_type"),
            "storage_path": download.get("storage_path"),
            "extraction_status": extraction["status"] if extraction else download["status"],
        })
        if download["status"] == "failed" or (
            extraction and extraction["status"] in {"failed", "partial"}
        ):
            incomplete = True
    return texts, metadata, incomplete


def _opportunity_inputs(
    service: Resource, message_id: str
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any] | None]], bool]:
    message = parse_message(get_message(service, message_id), include_attachment_data=True)
    texts, metadata, incomplete = _classification_inputs(service, message_id, message)
    classification = classify_opportunity(message["subject"], message["body_text"], texts)
    opportunities = build_opportunities(message, classification, texts)
    # Extraction preserves the order of qualifying attachment documents, even with duplicate names.
    sources = [
        item for doc, item in zip(classification["documents"][1:], metadata, strict=True)
        if doc["classification"] != "Other"
    ]
    units = list(zip(opportunities, sources or [None] * len(opportunities), strict=True))
    for opportunity, _ in units:
        if incomplete:
            processing = opportunity["processing"]
            processing["requires_review"] = True
            processing["review_reasons"].append("Attachment processing was incomplete.")
    return message, units, incomplete


def _extract_fields_email(service: Resource, message_id: str, explain: bool) -> bool:
    _, units, incomplete = _opportunity_inputs(service, message_id)
    for opportunity, _ in units:
        if not explain:
            for item in opportunity["evidence"].values():
                item.pop("span", None)
        print(json.dumps(opportunity))
    if incomplete:
        print(f"Attachment processing was incomplete for Gmail message {message_id}.", file=sys.stderr)
    return not incomplete


def _write_json_file(path: Path, payload: dict[str, Any], reprocess: bool) -> bool:
    data = json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    if reprocess:
        # Replace only after the new file is complete.
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True

    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError:
        return False
    try:
        with stream:
            stream.write(data)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return True


def _build_json_email(
    service: Resource, message_id: str, out_dir: Path, *, include_body: bool, reprocess: bool
) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", message_id):
        raise ValueError("Unsafe message ID for local output.")
    message, units, incomplete = _opportunity_inputs(service, message_id)
    if units:
        out_dir.mkdir(parents=True, exist_ok=True)
    for index, (opportunity, attachment) in enumerate(units, start=1):
        validated = validate_opportunity(opportunity)
        payload = format_opportunity(validated, message, attachment, include_body=include_body)
        path = out_dir / f"{message_id}__{index}.json"
        # Rejected records are written for inspection and must never be sent to a backend.
        written = _write_json_file(path, payload, reprocess)
        print(json.dumps({
            "filename": path.name,
            "title": payload["opportunity"]["title"],
            "status": validated["status"],
            "requires_review": validated["requires_review"],
            "error_count": len(validated["errors"]),
            "warning_count": len(validated["warnings"]),
            "written": written,
        }))
    if incomplete:
        print(f"Attachment processing was incomplete for Gmail message {message_id}.", file=sys.stderr)
    return not incomplete


def main(argv: Sequence[str] | None = None) -> int:
    """Fetch inbox messages, extract attachments, or classify opportunities."""
    parser = argparse.ArgumentParser(description="Read the configured Gmail inbox.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch recent inbox messages.")
    fetch.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    fetch.add_argument("--show-body", action="store_true", help="Print up to 500 body characters.")
    extract = commands.add_parser("extract", help="Extract text from recent inbox attachments.")
    extract.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    extract.add_argument("--show-text", action="store_true", help="Print up to 200 extracted characters.")
    classify = commands.add_parser("classify", help="Classify new inbox messages.")
    classify.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    classify.add_argument("--reprocess", action="store_true", help="Include previously processed messages.")
    classify.add_argument("--dry-run", action="store_true", help="Classify without updating the processed store.")
    classify.add_argument("--explain", action="store_true", help="Show category scores for each document.")
    fields = commands.add_parser("extract-fields", help="Extract fields for each opportunity.")
    fields.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    fields.add_argument("--explain", action="store_true", help="Include short matched evidence spans.")
    build = commands.add_parser("build-json", help="Validate opportunities and write local JSON files.")
    build.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    build.add_argument("--out-dir", type=Path, default=Path("output_json"), help="Directory for JSON files.")
    build.add_argument("--include-body", action="store_true", help="Include the original email body in JSON files.")
    build.add_argument("--reprocess", action="store_true", help="Replace existing JSON files for these opportunities.")
    args = parser.parse_args(argv)
    if args.max < 1:
        parser.error("--max must be a positive integer")

    failed = False
    try:
        service = get_gmail_service()
        for message_id in list_message_ids(service, settings.ALLOWED_SENDERS, args.max):
            if args.command == "build-json":
                try:
                    if not _build_json_email(
                        service, message_id, args.out_dir, include_body=args.include_body,
                        reprocess=args.reprocess,
                    ):
                        failed = True
                except Exception:
                    print(f"Could not build JSON for Gmail message {message_id}.", file=sys.stderr)
                    failed = True
                continue
            if args.command == "extract-fields":
                try:
                    if not _extract_fields_email(service, message_id, args.explain):
                        failed = True
                except Exception:
                    print(f"Could not extract fields for Gmail message {message_id}.", file=sys.stderr)
                    failed = True
                continue
            if args.command == "classify":
                try:
                    if not _classify_email(
                        service, message_id, reprocess=args.reprocess, dry_run=args.dry_run,
                        explain=args.explain,
                    ):
                        failed = True
                except Exception:
                    # Do not expose exception details from email or document processing.
                    print(f"Could not classify or record Gmail message {message_id}.", file=sys.stderr)
                    failed = True
                continue
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
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
