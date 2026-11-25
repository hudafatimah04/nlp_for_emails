"""Run the local NLP pipeline and hand off each eligible opportunity."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from config import settings
from integration.backend_client import HandoffResult, send_opportunity
from nlp.llm_errors import LLMExtractionError
from output.json_formatter import format_opportunity
from output.local_json import write_json_file
from storage.processed_store import clear_processed, is_delivered, is_processed, mark_processed, record_delivery
from validation.data_validator import validate_opportunity

Record = dict[str, Any]
OpportunityUnit = tuple[Record, Record | None]


def format_local_record(
    record: Record, email: Record, attachment: Record | None, *, include_body: bool = False,
) -> Record:
    """Validate and format an extracted opportunity."""
    validated = validate_opportunity(record)
    return format_opportunity(validated, email, attachment, include_body=include_body)


@dataclass(frozen=True)
class PipelineServices:
    """Inject message processing, handoff, storage, and local output dependencies."""

    build_units: Callable[[Record], tuple[list[OpportunityUnit], bool]]
    out_dir: Path = Path("output_json")
    include_body: bool = False
    check_processed: Callable[[str, str | None], bool] = is_processed
    save_processed: Callable[[str, str | None, str], None] = mark_processed
    reset_processed: Callable[[str, str | None], None] = clear_processed
    check_delivered: Callable[[str, str], bool] = is_delivered
    save_delivery: Callable[[str, str, str | None], None] = record_delivery
    send: Callable[[Record, str], HandoffResult] = send_opportunity
    write_file: Callable[[Path, Record, bool], bool] = write_json_file


def _attachment_order(attachment: Record) -> tuple[str, str, str, str]:
    filename = attachment.get("filename") or ""
    attachment_id = attachment.get("gmail_attachment_id") or ""
    # Gmail can include small parts inline without an attachment ID. Break that tie by content.
    digest = sha256((attachment.get("data") or "").encode()).hexdigest() if not attachment_id else ""
    return filename, attachment_id, digest, attachment.get("mime_type") or ""


def process_email(
    parsed_email: Record, services: PipelineServices, reprocess: bool = False, dry_run: bool = False
) -> Record:
    """Process one email, preserving successful handoffs when another record fails."""
    message_id = parsed_email.get("gmail_message_id")
    header = parsed_email.get("message_id_header")
    mode = settings.BACKEND_MODE
    result = {
        "gmail_message_id": message_id, "outcome": "error", "opportunities": [],
        "warnings": [], "skipped": False, "dry_run": dry_run, "error": None,
        "permanent_error": False, "retryable_error": False,
    }
    try:
        if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", message_id):
            raise ValueError("Invalid message ID for local output.")
        if not reprocess and services.check_processed(message_id, header):
            result.update(outcome="processed", skipped=True)
            return result
        if reprocess and not dry_run:
            # A new failed attempt must not be hidden by an older processed marker.
            services.reset_processed(message_id, header)

        message = {**parsed_email, "attachments": sorted(parsed_email.get("attachments", []), key=_attachment_order)}
        units, incomplete = services.build_units(message)
        if incomplete:
            result["warnings"].append("Attachment processing was incomplete. Handoff was deferred.")
            result.update(error="attachment_processing_incomplete", retryable_error=True)

        records = []
        for index, (record, attachment) in enumerate(sorted(units, key=lambda unit: unit[1] is not None), 1):
            payload = format_local_record(record, message, attachment, include_body=services.include_body)
            status = payload["processing"]["status"]
            folder = services.out_dir / "rejected" if status == "rejected" else services.out_dir
            path = folder / f"{message_id}__{index}.json"
            key = f"{message_id}:{index}"
            summary = {
                "file": str(path), "title": payload["opportunity"]["title"], "status": status,
                "idempotency_key": key, "handed_off": False, "already_delivered": False,
                "error": None, "permanent_error": False, "action": "pending",
            }
            result["opportunities"].append(summary)
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                services.write_file(path, payload, True)
            records.append((key, payload, summary))

        for key, payload, summary in records:
            if summary["status"] == "rejected":
                summary.update(error="rejected_not_sent", action="rejected_not_sent")
                continue
            if incomplete:
                # A missing document can change qualification and ordinal keys on retry.
                summary.update(error="attachment_processing_incomplete", action="deferred")
                continue
            if summary["status"] == "needs_review":
                summary["action"] = "held_for_review"
                continue
            if services.check_delivered(key, mode):
                summary.update(already_delivered=True, action="already_delivered")
                continue
            if dry_run:
                summary["action"] = "would_handoff"
                continue

            handoff = services.send(payload, key)
            if handoff["accepted"]:
                summary.update(handed_off=True, action="handed_off")
                # A crash between remote acceptance and this local write can cause a duplicate on retry.
                services.save_delivery(key, handoff["mode"], handoff["reference"])
            else:
                permanent = handoff["permanent_error"]
                summary.update(error=handoff["error"], permanent_error=permanent, action="handoff_failed")
                result["error"] = "backend_handoff_failed"
                result["permanent_error"] |= permanent
                result["retryable_error"] |= not permanent

        if result["error"] is None:
            outcome = "processed" if units else "not_an_opportunity"
            if not dry_run:
                services.save_processed(message_id, header, outcome)
            result["outcome"] = outcome
    except LLMExtractionError as error:
        code = "quota_reached" if error.quota else "extraction_failed"
        result.update(
            error=f"{error.provider}_{code}", stop_llm_requests=error.quota,
            error_type=type(error).__name__, error_message=str(error), retryable_error=True,
        )
    except Exception as error:
        # Exception text from libraries can carry private data or credentials.
        result.update(
            error="email_processing_failed",
            error_type=type(error).__name__,
            error_message="Could not complete email processing.",
            retryable_error=True,
        )
    return result
