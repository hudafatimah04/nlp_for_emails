"""Build opportunity units and reconcile independently extracted fields."""

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from nlp.eligibility_extractor import extract_eligibility
from nlp.extraction_evidence import Evidence
from nlp.information_extractor import extract_opportunity_fields

_OPPORTUNITY_TYPES = {"Hackathon", "Internship"}


def _extract(
    text: str | None, category: str, source: str, subject: str | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Evidence], list[str]]:
    opportunity = extract_opportunity_fields(text, category, {"source": source, "subject": subject})
    eligibility = extract_eligibility(text, source=source)
    evidence = {**opportunity.pop("evidence"), **eligibility.pop("evidence")}
    warnings = [*opportunity.pop("warnings"), *eligibility.pop("warnings")]
    return opportunity, eligibility, evidence, warnings


def _merge_fields(
    primary: dict[str, Any], secondary: dict[str, Any],
    evidence: dict[str, Evidence], secondary_evidence: dict[str, Evidence],
    conflicts: list[dict[str, Any]],
) -> None:
    for field, incoming in secondary.items():
        current = primary[field]
        if incoming is None or (field == "eligible_departments" and incoming == []):
            continue
        if current is None or (field == "eligible_departments" and current == []):
            primary[field] = incoming
            evidence[field] = secondary_evidence[field]
            continue
        equal = set(current) == set(incoming) if field == "eligible_departments" else current == incoming
        if not equal:
            conflicts.append({
                "field": field,
                "candidates": [
                    {"value": current, "source": evidence[field]["source"]},
                    {"value": incoming, "source": secondary_evidence[field]["source"]},
                ],
            })
            primary[field] = None
            evidence.pop(field, None)


def build_opportunities(
    parsed_email: Mapping[str, Any],
    classification_result: Mapping[str, Any],
    document_texts: Sequence[str | tuple[str | None, str | None]],
) -> list[dict[str, Any]]:
    """Pair attachment texts with classifier results by position, preserving duplicate filenames."""
    documents = classification_result["documents"]
    email_category = documents[0]["classification"]
    subject = parsed_email.get("subject")
    attachment_units = []
    ignored = []
    for document, attachment in zip(documents[1:], document_texts, strict=True):
        filename, text = (document["source"], attachment) if isinstance(attachment, str) else attachment
        category = document["classification"]
        if category in _OPPORTUNITY_TYPES:
            attachment_units.append((filename, text, category))
        else:
            ignored.append(f"attachment {filename or '(unnamed)'} ignored (not an opportunity document)")

    counts = Counter(category for _, _, category in attachment_units)
    units = [(filename, text, category, True) for filename, text, category in attachment_units]
    if not units and email_category in _OPPORTUNITY_TYPES:
        units.append((None, parsed_email.get("body_text"), email_category, False))

    opportunities = []
    for filename, text, category, is_attachment in units:
        document_source = (filename or "unnamed attachment") if is_attachment else "email"
        opportunity, eligibility, evidence, warnings = _extract(
            text, category, document_source, None if is_attachment else subject
        )
        review_reasons = list(classification_result.get("review_reasons", []))
        conflicts: list[dict[str, Any]] = []
        if is_attachment and category == email_category and counts[category] == 1:
            email_fields, email_eligibility, email_evidence, email_warnings = _extract(
                parsed_email.get("body_text"), category, "email", subject
            )
            _merge_fields(opportunity, email_fields, evidence, email_evidence, conflicts)
            _merge_fields(eligibility, email_eligibility, evidence, email_evidence, conflicts)
            warnings.extend(email_warnings)
        if counts[category] > 1:
            review_reasons.append(f"Multiple {category} attachments were kept as separate opportunities.")

        missing = [
            field for fields in (opportunity, eligibility) for field, value in fields.items()
            if value is None
        ]
        if missing:
            review_reasons.append(f"Missing fields: {', '.join(missing)}.")
        if conflicts:
            review_reasons.append("Email and attachment values conflict.")
        if warnings:
            review_reasons.append("Extraction warnings require review.")
        source = {
            key: parsed_email.get(key)
            for key in ("gmail_message_id", "message_id_header", "sender_email", "subject")
        }
        opportunities.append({
            "opportunity": {**opportunity, "type": category},
            "eligibility": eligibility,
            "source": {**source, "attachment_filename": filename},
            "processing": {
                "classification": category,
                "requires_review": bool(review_reasons),
                "review_reasons": list(dict.fromkeys(review_reasons)),
                "missing_fields": missing,
                "conflicts": conflicts,
                "warnings": list(dict.fromkeys([*warnings, *ignored])),
            },
            "evidence": evidence,
        })
    return opportunities
