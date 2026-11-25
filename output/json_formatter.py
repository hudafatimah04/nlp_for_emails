"""Build the versioned, per-opportunity JSON payload."""

from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "1.0"
_OPPORTUNITY_FIELDS = (
    "type", "title", "description", "organizer", "deadline", "start_date", "end_date",
    "location", "registration_link", "fee", "team_based",
)
_ELIGIBILITY_FIELDS = ("min_year", "max_year", "min_cgpa", "department_scope", "eligible_departments")
_SOURCE_FIELDS = ("gmail_message_id", "message_id_header", "sender_email", "subject", "received_at")
_ATTACHMENT_FIELDS = {
    "attachment_filename": "filename",
    "attachment_file_type": "file_type",
    "attachment_storage_path": "storage_path",
    "attachment_extraction_status": "extraction_status",
}
_PROCESSING_FIELDS = (
    "classification", "status", "requires_review", "review_reasons", "missing_fields",
    "conflicts", "errors", "warnings",
)


def _nullable(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


def _format_conflict_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    internal = {"citation_corrected", "reported_line_number"}
    if candidate.get("kind") == "visual":
        internal.update({"kind", "page_number", "verified", "span"})
    return {key: value for key, value in candidate.items() if key not in internal}


def format_opportunity(
    validated: Mapping[str, Any],
    parsed_email: Mapping[str, Any],
    attachment_info: Mapping[str, Any] | None,
    *,
    include_body: bool = False,
) -> dict[str, Any]:
    """Format one validated opportunity. Include the source body only on request."""
    extracted_source = validated.get("source", {})
    source = {
        field: _nullable(parsed_email.get(field, extracted_source.get(field)))
        for field in _SOURCE_FIELDS
    }
    attachment = attachment_info or {}
    source.update({
        field: _nullable(attachment.get(key, extracted_source.get(field)))
        for field, key in _ATTACHMENT_FIELDS.items()
    })
    if include_body:
        source["email_body"] = _nullable(parsed_email.get("body_text"))
    processing = {field: validated[field] for field in _PROCESSING_FIELDS}
    processing["conflicts"] = [
        {**conflict, "candidates": [_format_conflict_candidate(candidate) for candidate in conflict["candidates"]]}
        for conflict in processing["conflicts"]
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "opportunity": {field: _nullable(validated["opportunity"].get(field)) for field in _OPPORTUNITY_FIELDS},
        "eligibility": {field: validated["eligibility"].get(field) for field in _ELIGIBILITY_FIELDS},
        "source": source,
        "processing": processing,
        "evidence": {
            field: {"source": item.get("source"), "line_number": item.get("line_number")}
            for field, item in validated["evidence"].items()
        },
    }
