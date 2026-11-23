"""Command-line entry point for the opportunity inbox reader."""

import argparse
import json
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from time import sleep
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
from output.local_json import write_json_file as _write_json_file
from pipeline.run_pipeline import PipelineServices, process_email
from storage.processed_store import clear_processed, is_processed, mark_processed
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
    units, incomplete = _build_units(service, message_id, message)
    return message, units, incomplete


def _build_units(
    service: Resource, message_id: str, message: dict[str, Any]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None]], bool]:
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
    return units, incomplete


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


def _run_email(service: Resource, message_id: str, args: argparse.Namespace) -> dict[str, Any]:
    try:
        if not args.reprocess and is_processed(message_id, None):
            return {"outcome": "processed", "skipped": True, "opportunities": [], "warnings": []}
        if args.reprocess and not args.dry_run:
            # Clear the Gmail marker even when fetching this explicit retry later fails.
            clear_processed(message_id, None)
        message = parse_message(get_message(service, message_id), include_attachment_data=True)
        services = PipelineServices(
            build_units=lambda parsed: _build_units(service, message_id, parsed),
            out_dir=args.out_dir,
            include_body=args.include_body,
            check_processed=is_processed,
            save_processed=mark_processed,
        )
        return process_email(message, services, reprocess=args.reprocess, dry_run=args.dry_run)
    except Exception as error:
        return {
            "outcome": "error", "skipped": False, "opportunities": [], "warnings": [],
            "retryable_error": True, "error": "email_fetch_or_parse_failed", "error_type": type(error).__name__,
        }


def _report_run_email(message_id: str, result: dict[str, Any], tally: dict[str, int], dry_run: bool) -> None:
    tally["emails_seen"] += 1
    outcome = result["outcome"]
    if result["skipped"]:
        tally["skipped_as_duplicates"] += 1
        outcome = "skipped"
    elif outcome == "error":
        tally["errors"] += 1
        tally["retryable_errors"] += int(result.get("retryable_error", False))
        tally["permanent_errors"] += int(result.get("permanent_error", False))
    else:
        tally[outcome] += 1
    print(json.dumps({
        "gmail_message_id": message_id, "outcome": outcome, "dry_run": dry_run,
        "opportunities": len(result["opportunities"]), "error": result.get("error"),
        "error_type": result.get("error_type"), "warning_count": len(result["warnings"]),
    }))
    for item in result["opportunities"]:
        tally["opportunities_handed_off"] += int(item["handed_off"])
        tally["opportunities_already_delivered"] += int(item["already_delivered"])
        tally["rejected_not_sent"] += int(item["error"] == "rejected_not_sent")
        print(json.dumps({
            "file": Path(item["file"]).name, "status": item["status"], "action": item["action"],
            "error": item["error"], "permanent_error": item["permanent_error"],
        }))


def _run_command(args: argparse.Namespace) -> int:
    tally = dict.fromkeys((
        "emails_seen", "skipped_as_duplicates", "processed", "not_an_opportunity", "errors",
        "retryable_errors", "permanent_errors", "opportunities_handed_off",
        "opportunities_already_delivered", "rejected_not_sent",
    ), 0)
    poll_failed = False
    try:
        service = get_gmail_service()
        while True:
            try:
                message_ids = list_message_ids(service, settings.ALLOWED_SENDERS, args.max)
            except Exception:
                print("Could not poll the Gmail inbox.", file=sys.stderr)
                poll_failed = True
            else:
                for message_id in message_ids:
                    _report_run_email(message_id, _run_email(service, message_id, args), tally, args.dry_run)
            if not args.watch:
                break
            sleep(args.interval)
    except KeyboardInterrupt:
        print("Run stopped.")
    except Exception:
        print("Could not start or continue the inbox run.", file=sys.stderr)
        poll_failed = True
    print(json.dumps({"tally": tally, "dry_run": args.dry_run}))
    return int(bool(tally["errors"] or poll_failed))


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
    run = commands.add_parser("run", help="Process emails and hand off eligible opportunities.")
    run.add_argument("--max", type=int, default=10, help="Maximum number of messages per poll.")
    run.add_argument("--out-dir", type=Path, default=Path("output_json"), help="Directory for JSON files.")
    run.add_argument("--include-body", action="store_true", help="Include the email body in the JSON payload.")
    run.add_argument("--reprocess", action="store_true", help="Reprocess emails without resending delivered records.")
    run.add_argument("--dry-run", action="store_true", help="Report actions without writing JSON or handing off.")
    run.add_argument("--watch", action="store_true", help="Poll until interrupted.")
    run.add_argument("--interval", type=int, help="Polling interval in seconds (default 60 if unset).")
    args = parser.parse_args(argv)
    if args.max < 1:
        parser.error("--max must be a positive integer")
    if args.command == "run":
        if args.interval is None:
            args.interval = settings.POLL_INTERVAL_SECONDS if settings.POLL_INTERVAL_SECONDS is not None else 60
        if args.watch and args.interval < 1:
            parser.error("--interval must be positive when watching")
        return _run_command(args)

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
