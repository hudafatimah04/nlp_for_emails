"""Command-line entry point for the opportunity inbox reader."""

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from time import sleep
from typing import Any

from googleapiclient.discovery import Resource

from config import settings
from document_processing.document_extractor import extract_document
from document_processing.file_types import document_kind
from gmail_integration.attachment_downloader import download_attachment
from storage.runtime_storage import cleanup, processing_logs, storage_job
from gmail_integration.authentication import get_gmail_service
from gmail_integration.email_parser import parse_message
from gmail_integration.email_reader import get_message, list_message_ids
from nlp.inline_media import InlineFile
from nlp.llm_errors import LLMExtractionError
from nlp.provider_budget import ProviderBudget
from nlp.llm_extractor import AttachmentText, extract_opportunities
from output.local_json import write_json_file as _write_json_file
from pipeline.run_pipeline import PipelineServices, format_local_record, process_email
from storage.processed_store import clear_processed, is_processed, mark_processed


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
    service: Resource, message_id: str, budget: ProviderBudget, *, reprocess: bool, explain: bool
) -> bool:
    if not reprocess and is_processed(message_id, None):
        print("skipped (already processed)")
        return True

    message = parse_message(get_message(service, message_id), include_attachment_data=True)
    header = message["message_id_header"]
    if not reprocess and header and is_processed(message_id, header):
        print("skipped (already processed)")
        return True

    units, incomplete = _build_units(service, message_id, message, budget)
    record = units[0][0] if units else None
    processing = record["processing"] if record else {}
    result = {
        "classification": processing.get("classification", None if incomplete else "Other"),
        "requires_review": incomplete or processing.get("requires_review", False),
        "review_reasons": processing.get("review_reasons", []),
    }
    if incomplete and not record:
        result["review_reasons"].append("Attachment processing was incomplete.")
    if explain and record:
        result["evidence"] = {
            field: {key: item[key] for key in ("source", "source_id", "line_number")}
            for field, item in record["evidence"].items()
        }
    print(json.dumps({"gmail_message_id": message_id, "subject": message["subject"], **result}))
    return not incomplete


def _attachment_inputs(
    service: Resource, message_id: str, message: dict[str, Any]
) -> tuple[list[AttachmentText], list[dict[str, Any]], bool]:
    inputs = []
    metadata = []
    incomplete = False
    for number, (attachment, (download, extraction)) in enumerate(zip(
        message["attachments"], _attachment_results(service, message_id, message), strict=True,
    ), 1):
        if download["status"] != "downloaded" or extraction is None:
            reason = "unsupported file type" if download["status"] == "unsupported" else "download failed"
            raise LLMExtractionError(f"Attachment {number}: {reason}. Processing is incomplete.")
        kind = document_kind(download["file_type"], attachment["filename"])
        visual = None
        if kind == "image":
            visual = InlineFile(Path(download["storage_path"]), "image")
        elif kind == "pdf" and extraction.get("inline_pdf"):
            visual = InlineFile(Path(download["storage_path"]), "pdf")
        inputs.append(AttachmentText(attachment["filename"] or "unnamed attachment", extraction["text"], visual=visual))
        metadata.append({
            "filename": attachment["filename"],
            "file_type": download.get("file_type"),
            "storage_path": download.get("storage_path"),
            "extraction_status": extraction["status"],
            "original_supplied": visual is not None,
        })
        if visual is None and extraction["status"] != "success":
            incomplete = True
    return inputs, metadata, incomplete


def _opportunity_inputs(
    service: Resource, message_id: str, budget: ProviderBudget,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any] | None]], bool]:
    message = parse_message(get_message(service, message_id), include_attachment_data=True)
    units, incomplete = _build_units(service, message_id, message, budget)
    return message, units, incomplete


def _build_units(
    service: Resource, message_id: str, message: dict[str, Any], budget: ProviderBudget,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None]], bool]:
    attachments, metadata, incomplete = _attachment_inputs(service, message_id, message)
    opportunities = extract_opportunities(
        message["subject"], message["body_text"], sender=message.get("sender_email"), message_id=message_id,
        received_at=message.get("received_at"),
        attachments=attachments,
        max_attempts=budget.max_attempts, budget=budget,
    )
    units = []
    for opportunity in opportunities:
        source = opportunity["source"]
        source.update({key: message.get(key) for key in ("gmail_message_id", "message_id_header")})
        source["attachments"] = attachment_metadata = {
            f"attachment_{index}": item for index, item in enumerate(metadata, 1)
        }
        cited = {item["source_id"] for item in opportunity["evidence"].values()}
        for conflict in opportunity["processing"]["conflicts"]:
            cited.update(item["source_id"] for item in conflict["candidates"])
        primary = next((item for key, item in attachment_metadata.items() if key in cited), None)
        source["attachment_filename"] = primary["filename"] if primary is not None else None
        processing = opportunity["processing"]
        if incomplete:
            processing["requires_review"] = True
            processing["review_reasons"].append("Attachment processing was incomplete.")
        if any(item["original_supplied"] and item["extraction_status"] != "success" for item in metadata):
            processing["warnings"].append("Local attachment text was incomplete. The original was supplied to the model.")
        units.append((opportunity, primary))
    return units, incomplete


def _extract_fields_email(service: Resource, message_id: str, budget: ProviderBudget, explain: bool) -> bool:
    _, units, incomplete = _opportunity_inputs(service, message_id, budget)
    for opportunity, _ in units:
        citations = list(opportunity["evidence"].values())
        for conflict in opportunity["processing"]["conflicts"]:
            citations.extend(conflict["candidates"])
        for item in citations:
            if explain and "span" in item:
                item["span"] = item["span"][:80]
            else:
                item.pop("span", None)
        print(json.dumps(opportunity))
    if incomplete:
        print(f"Attachment processing was incomplete for Gmail message {message_id}.", file=sys.stderr)
    return not incomplete


def _build_json_email(
    service: Resource, message_id: str, out_dir: Path, budget: ProviderBudget, *, include_body: bool, reprocess: bool
) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", message_id):
        raise ValueError("Unsafe message ID for local output.")
    message, units, incomplete = _opportunity_inputs(service, message_id, budget)
    if units:
        out_dir.mkdir(parents=True, exist_ok=True)
    for index, (opportunity, attachment) in enumerate(units, start=1):
        payload = format_local_record(opportunity, message, attachment, include_body=include_body)
        validated = payload["processing"]
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


def _run_email(service: Resource, message_id: str, args: argparse.Namespace, budget: ProviderBudget) -> dict[str, Any]:
    try:
        if not args.reprocess and is_processed(message_id, None):
            return {"outcome": "processed", "skipped": True, "opportunities": [], "warnings": []}
        if args.reprocess and not args.dry_run:
            # Clear the Gmail marker even when fetching this explicit retry later fails.
            clear_processed(message_id, None)
        message = parse_message(get_message(service, message_id), include_attachment_data=True)
        services = PipelineServices(
            build_units=lambda parsed: _build_units(service, message_id, parsed, budget),
            out_dir=args.out_dir,
            include_body=args.include_body,
            check_processed=is_processed,
            save_processed=mark_processed,
        )
        with storage_job(temporary=args.dry_run) as job:
            result = process_email(message, services, reprocess=args.reprocess, dry_run=args.dry_run)
            if not args.dry_run and not result["opportunities"]:
                if result["error"]:
                    job.state = "failed"
                elif result["outcome"] == "not_an_opportunity":
                    job.temporary = True
                    job.state = "temporary"
            return result
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
        "error_type": result.get("error_type"), "error_message": result.get("error_message"),
        "warning_count": len(result["warnings"]),
    }))
    for item in result["opportunities"]:
        tally["opportunities_handed_off"] += int(item["handed_off"])
        tally["opportunities_already_delivered"] += int(item["already_delivered"])
        tally["rejected_not_sent"] += int(item["error"] == "rejected_not_sent")
        print(json.dumps({
            "file": Path(item["file"]).name, "status": item["status"], "action": item["action"],
            "error": item["error"], "permanent_error": item["permanent_error"],
        }))


def _run_command(args: argparse.Namespace, budget: ProviderBudget) -> int:
    tally = dict.fromkeys((
        "emails_seen", "skipped_as_duplicates", "processed", "not_an_opportunity", "errors",
        "retryable_errors", "permanent_errors", "opportunities_handed_off",
        "opportunities_already_delivered", "rejected_not_sent",
    ), 0)
    poll_failed = stop = False
    try:
        service = get_gmail_service()
        while True:
            try:
                message_ids = [args.message_id] if args.message_id else list_message_ids(
                    service, settings.ALLOWED_SENDERS, args.max,
                )
            except Exception:
                print("Could not poll the Gmail inbox.", file=sys.stderr)
                poll_failed = True
            else:
                for message_id in message_ids:
                    result = _run_email(service, message_id, args, budget)
                    _report_run_email(message_id, result, tally, args.dry_run)
                    stop = result.get("stop_llm_requests", False)
                    if stop or not budget.remaining:
                        break
            if stop or not budget.remaining or not args.watch:
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
    sweep = commands.add_parser("cleanup", help="List expired managed artifacts unless --apply is supplied.")
    sweep_flags = sweep.add_mutually_exclusive_group()
    sweep_flags.add_argument("--dry-run", action="store_true", help="List eligible files only (default).")
    sweep_flags.add_argument("--apply", action="store_true", help="Remove eligible files.")
    fetch = commands.add_parser("fetch", help="Fetch recent inbox messages.")
    fetch.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    fetch.add_argument("--show-body", action="store_true", help="Print up to 500 body characters.")
    extract = commands.add_parser("extract", help="Extract text from recent inbox attachments.")
    extract.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    extract.add_argument("--show-text", action="store_true", help="Print up to 200 extracted characters.")
    classify = commands.add_parser("classify", help="Classify new inbox messages.")
    classify.add_argument("--max", type=int, default=10, help="Maximum number of messages.")
    classify.add_argument("--reprocess", action="store_true", help="Include previously processed messages.")
    classify.add_argument("--dry-run", action="store_true", help="Compatibility flag. Classification previews never mark emails processed.")
    classify.add_argument("--explain", action="store_true", help="Show source locations for extracted evidence.")
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
    run.add_argument("--message-id", help="Process one selected Gmail message ID without polling.")
    run.add_argument("--out-dir", type=Path, default=Path("output_json"), help="Directory for JSON files.")
    run.add_argument("--include-body", action="store_true", help="Include the email body in the JSON payload.")
    run.add_argument("--reprocess", action="store_true", help="Reprocess emails without resending delivered records.")
    run.add_argument("--dry-run", action="store_true", help="Report actions without writing JSON or handing off.")
    run.add_argument("--watch", action="store_true", help="Poll until interrupted.")
    run.add_argument("--interval", type=int, help="Polling interval in seconds (default 60 if unset).")
    for command in (classify, fields, build, run):
        command.add_argument("--llm-limit", "--gemini-limit", type=int,
                             help="Optional total LLM API attempts across providers per command.")
        command.add_argument("--llm-attempts", "--gemini-attempts", type=int, choices=(1, 2, 3), default=1,
                             help="Maximum LLM attempts per email (default 1).")
    args = parser.parse_args(argv)
    if args.command == "cleanup":
        print(json.dumps(cleanup(apply=args.apply)))
        return 0
    budget = None
    if hasattr(args, "llm_limit"):
        if args.llm_limit is not None and args.llm_limit < 1:
            parser.error("--llm-limit must be a positive integer")
        try:
            budget = ProviderBudget(args.llm_limit, args.llm_attempts)
        except LLMExtractionError as error:
            parser.error(str(error))
    if args.max < 1:
        parser.error("--max must be a positive integer")
    if args.command == "run":
        if args.message_id and (args.watch or not re.fullmatch(r"[A-Za-z0-9_-]+", args.message_id)):
            parser.error("--message-id requires a valid Gmail message ID and cannot be combined with --watch")
        if args.interval is None:
            args.interval = settings.POLL_INTERVAL_SECONDS if settings.POLL_INTERVAL_SECONDS is not None else 60
        if args.watch and args.interval < 1:
            parser.error("--interval must be positive when watching")
        return _run_command(args, budget)

    failed = False
    try:
        service = get_gmail_service()
        for message_id in list_message_ids(service, settings.ALLOWED_SENDERS, args.max):
            if budget is not None:
                if not budget.remaining:
                    break
                try:
                    if args.command == "build-json":
                        success = _build_json_email(
                            service, message_id, args.out_dir, budget, include_body=args.include_body,
                            reprocess=args.reprocess,
                        )
                    elif args.command == "extract-fields":
                        with storage_job(temporary=True):
                            success = _extract_fields_email(service, message_id, budget, args.explain)
                    else:
                        with storage_job(temporary=True):
                            success = _classify_email(service, message_id, budget, reprocess=args.reprocess, explain=args.explain)
                    failed |= not success
                except LLMExtractionError as error:
                    print(f"Gmail message {message_id}: {error}", file=sys.stderr)
                    if error.quota:
                        return 1
                    failed = True
                except Exception:
                    print(f"Could not process Gmail message {message_id}.", file=sys.stderr)
                    failed = True
                continue
            raw = get_message(service, message_id)
            if args.command == "extract":
                message = parse_message(raw, include_attachment_data=True)
                with storage_job(temporary=True):
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
    for name in ("nlp.llm_extractor", "nlp.groq_provider"):
        telemetry = logging.getLogger(name)
        telemetry.setLevel(logging.INFO)
        telemetry.addHandler(logging.StreamHandler())
        telemetry.propagate = False
    if len(sys.argv) > 1 and sys.argv[1] in {"run", "classify", "build-json", "extract-fields"}:
        with processing_logs():
            raise SystemExit(main())
    raise SystemExit(main())
