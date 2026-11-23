"""Validate extracted values without guessing replacements."""

import re
from collections.abc import Mapping
from datetime import date
from math import isfinite
from typing import Any, TypedDict
from urllib.parse import urlsplit

from config import validation_rules as rules
from config.departments import DEPARTMENT_ALIASES


class Issue(TypedDict):
    field: str | None
    code: str
    message: str


def _issue(field: str | None, code: str, message: str) -> Issue:
    return {"field": field, "code": code, "message": message}


def _numeric(value: Any) -> bool:
    return type(value) is int or (type(value) is float and isfinite(value))


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str) or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        return False
    try:
        parts = urlsplit(value)
        # Accessing port also checks for a malformed authority.
        port = parts.port
        return parts.scheme in rules.ALLOWED_URL_SCHEMES and bool(parts.hostname) and (
            port is None or port > 0
        )
    except ValueError:
        return False


def _validate_dates(
    opportunity: dict[str, Any], reference_date: date, errors: list[Issue], warnings: list[Issue]
) -> None:
    dates: dict[str, date] = {}
    for field in ("deadline", "start_date", "end_date"):
        value = opportunity.get(field)
        if value is None:
            continue
        try:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("Date is not in ISO calendar form.")
            dates[field] = date.fromisoformat(value)
        except ValueError:
            opportunity[field] = None
            errors.append(_issue(field, "invalid_date", "Expected a real calendar date in YYYY-MM-DD form."))

    start, end, deadline = (dates.get(field) for field in ("start_date", "end_date", "deadline"))
    if start and end and end < start:
        opportunity["start_date"] = opportunity["end_date"] = None
        errors.append(_issue("end_date", "end_before_start", "End date precedes start date. Both dates were cleared."))
        start = None
    if deadline and start and deadline > start:
        warnings.append(_issue("deadline", "deadline_after_start", "Deadline is after the start date."))
    if deadline and deadline < reference_date:
        warnings.append(_issue("deadline", "deadline_passed", "Deadline is before the reference date."))


def _validate_eligibility(
    eligibility: dict[str, Any], errors: list[Issue], warnings: list[Issue]
) -> None:
    cgpa = eligibility.get("min_cgpa")
    if cgpa is not None and not (_numeric(cgpa) and rules.MIN_CGPA_ALLOWED <= cgpa <= rules.MAX_CGPA_ALLOWED):
        eligibility["min_cgpa"] = None
        errors.append(_issue("min_cgpa", "invalid_cgpa", "CGPA must be a number within the configured range."))

    for field in ("min_year", "max_year"):
        value = eligibility.get(field)
        if value is not None and not (
            type(value) is int and rules.MIN_YEAR_ALLOWED <= value <= rules.MAX_YEAR_ALLOWED
        ):
            eligibility[field] = None
            errors.append(_issue(field, "invalid_year_range", "Year must be an integer within the configured range."))
    minimum, maximum = eligibility.get("min_year"), eligibility.get("max_year")
    if minimum is not None and maximum is not None and minimum > maximum:
        eligibility["min_year"] = eligibility["max_year"] = None
        errors.append(_issue("max_year", "invalid_year_range", "Minimum year exceeds maximum year. Both were cleared."))

    departments = eligibility.get("eligible_departments")
    if isinstance(departments, list):
        # These canonical codes are provisional and have not been verified against Supabase.
        known = []
        for code in departments:
            if isinstance(code, str) and code in DEPARTMENT_ALIASES:
                known.append(code)
            else:
                label = f" {code}" if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,20}", code) else ""
                warnings.append(_issue(
                    "eligible_departments", "unknown_department",
                    f"Department code{label} is absent from the provisional mapping and was removed.",
                ))
        eligibility["eligible_departments"] = departments = known
    elif departments is not None:
        eligibility["eligible_departments"] = departments = None
        errors.append(_issue("eligible_departments", "invalid_departments", "Expected a list of department codes."))

    scope = eligibility.get("department_scope")
    if scope is not None and (
        scope not in ("SPECIFIC", "ALL") or (scope == "SPECIFIC" and not departments)
        or (scope == "ALL" and bool(departments))
    ):
        eligibility["department_scope"] = None
        warnings.append(_issue(
            "department_scope", "scope_mismatch",
            "SPECIFIC requires at least one department and ALL requires an empty department list.",
        ))


def validate_opportunity(
    opportunity_dict: Mapping[str, Any], reference_date: date | None = None
) -> dict[str, Any]:
    """Validate a Phase 4 record and carry forward its provenance and review state."""
    opportunity = dict(opportunity_dict.get("opportunity", {}))
    eligibility = dict(opportunity_dict.get("eligibility", {}))
    extracted = opportunity_dict.get("processing", {})
    errors: list[Issue] = list(extracted.get("errors", []))
    warnings: list[Issue] = [
        _issue(None, "extraction_warning", warning) if isinstance(warning, str) else warning
        for warning in extracted.get("warnings", [])
    ]
    reasons = list(extracted.get("review_reasons", []))
    conflicts = list(extracted.get("conflicts", []))

    title = opportunity.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        opportunity["title"] = None
        errors.append(_issue("title", "invalid_title", "Title must be nonempty text."))
    category = opportunity.get("type")
    if category is not None and category not in rules.ALLOWED_TYPES:
        opportunity["type"] = None
        errors.append(_issue("type", "invalid_type", "Type must be an allowed opportunity type."))

    for field in ("description", "organizer", "location"):
        value = opportunity.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            opportunity[field] = None
            errors.append(_issue(field, "invalid_text", "Expected nonempty text or null."))
    fee = opportunity.get("fee")
    if fee is not None and not (_numeric(fee) and fee >= 0):
        opportunity["fee"] = None
        errors.append(_issue("fee", "invalid_fee", "Fee must be a nonnegative number or null."))
    team = opportunity.get("team_based")
    if team is not None and type(team) is not bool:
        opportunity["team_based"] = None
        errors.append(_issue("team_based", "invalid_team_based", "Expected true, false, or null."))
    url = opportunity.get("registration_link")
    if url is not None and not _valid_url(url):
        opportunity["registration_link"] = None
        errors.append(_issue("registration_link", "invalid_url", "Expected an HTTP or HTTPS URL with a host."))

    _validate_dates(opportunity, reference_date or date.today(), errors, warnings)
    _validate_eligibility(eligibility, errors, warnings)

    values = {**opportunity, **eligibility}
    required_missing = [field for field in rules.REQUIRED_FIELDS if values.get(field) is None]
    for field in required_missing:
        if opportunity_dict.get("opportunity", {}).get(field) is None:
            errors.append(_issue(field, "missing_required", "A required field is missing."))
    for field in rules.RECOMMENDED_FIELDS:
        if values.get(field) is None:
            warnings.append(_issue(field, "missing_recommended", "A recommended field is missing."))

    missing = list(dict.fromkeys([
        *extracted.get("missing_fields", []), *(field for field, value in values.items() if value is None),
        *(field for field in [*rules.REQUIRED_FIELDS, *rules.RECOMMENDED_FIELDS] if values.get(field) is None),
    ]))
    if errors:
        reasons.append("Validation found errors.")
    missing_already_explained = any(reason.startswith("Missing fields:") for reason in reasons)
    explained_fields = extracted.get("missing_fields", []) if missing_already_explained else []
    unexplained = [
        issue for issue in warnings
        if issue["code"] != "missing_recommended" or issue["field"] not in explained_fields
    ]
    if unexplained:
        labels = dict.fromkeys(
            "missing recommended fields" if issue["code"] == "missing_recommended" else issue["code"]
            for issue in unexplained
        )
        noun = "warning" if len(unexplained) == 1 else "warnings"
        reasons.append(f"{len(unexplained)} {noun}: {', '.join(labels)}.")
    if conflicts:
        reasons.append("Extracted values conflict.")
    if extracted.get("requires_review") and not reasons:
        reasons.append("Extraction flagged this opportunity for review.")

    requires_review = bool(extracted.get("requires_review") or errors or warnings or conflicts or reasons)
    status = "rejected" if required_missing else ("needs_review" if requires_review else "ready")
    return {
        "opportunity": opportunity,
        "eligibility": eligibility,
        "source": dict(opportunity_dict.get("source", {})),
        "classification": extracted.get("classification", opportunity_dict.get("opportunity", {}).get("type")),
        "errors": errors,
        "warnings": warnings,
        "status": status,
        "requires_review": requires_review,
        "review_reasons": list(dict.fromkeys(reasons)),
        "missing_fields": missing,
        "conflicts": conflicts,
        "evidence": opportunity_dict.get("evidence", {}),
    }
