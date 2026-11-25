"""Shared evidence-backed extraction with configurable LLM providers."""

import json
import logging
import os
import re
import time
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Literal, get_args

import httpx2
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config.departments import DEPARTMENT_ALIASES
from nlp.email_input import clean_email_body, clean_email_line
from nlp.groq_provider import DEFAULT_GROQ_MODEL, request_groq
from nlp.inline_media import MAX_REQUEST_BYTES, InlineFile, InlineMedia, read_inline_file
from nlp.llm_errors import GroqExtractionError, LLMExtractionError
from nlp.llm_prompt import SYSTEM_PROMPT
from nlp.provider_budget import ProviderBudget, provider_mode
from validation.text_safety import has_invalid_controls

DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_INPUT_CHARACTERS = 200_000
DEFAULT_TIMEOUT_SECONDS = 60
_TRANSIENT_CODES = {500, 502, 503, 504}
_FEE_SUBJECT = r"(?:registrations?|applications?|entry|admission|participation)"
_FREE_FEE = re.compile(
    rf"\b(?:free\s+{_FEE_SUBJECT}|no\s+{_FEE_SUBJECT}\s+fees?|"
    rf"{_FEE_SUBJECT}(?:\s+(?:fee|cost))?\s*(?:is|are|:)?\s*(?:(?:completely|totally)\s+)?free)\b",
    re.IGNORECASE,
)
_FEE_AMOUNT = re.compile(
    rf"\b{_FEE_SUBJECT}\s+(?:fee|cost)\s*(?:is|are|:)?\s*"
    r"(?:₹|\$|€|£|INR\s*|Rs\.?\s*)?(\d[\d,]*(?:\.\d+)?)(?![\w,-])",
    re.IGNORECASE,
)
_THINKING_LEVELS = {
    "gemini-3.5-flash-lite": types.ThinkingLevel.MINIMAL,
    "gemini-3.8-flash": types.ThinkingLevel.LOW,
}
logger = logging.getLogger(__name__)

Category = Literal["Hackathon", "Internship", "Event"]
OpportunityField = Literal[
    "title", "description", "organizer", "deadline", "start_date", "end_date",
    "location", "registration_link", "fee", "team_based",
]
EligibilityField = Literal["min_year", "max_year", "min_cgpa", "department_scope", "eligible_departments"]
Value = str | int | float | bool | list[str]
_FIELD_TYPES = {
    **dict.fromkeys(get_args(OpportunityField), (str,)),
    "fee": (int, float), "team_based": (bool,), "min_year": (int,), "max_year": (int,),
    "min_cgpa": (int, float), "department_scope": (str,), "eligible_departments": (list,),
}


class GeminiExtractionError(LLMExtractionError):
    """Extraction failed and produced no usable classification."""


class GeminiUnavailableError(GeminiExtractionError):
    """Gemini could not provide a response due to a temporary service failure."""


class GeminiInputError(GeminiExtractionError):
    """Configuration or input prevents a request."""


class GeminiUnsupportedInputError(GeminiInputError):
    """Gemini cannot accept the request as prepared."""


class AutoExtractionError(LLMExtractionError):
    """No configured provider could complete automatic extraction."""

    provider = "auto"


class GeminiResponseError(GeminiExtractionError):
    """The model response is incomplete or fails schema checks."""


class GeminiQuotaError(GeminiExtractionError):
    """Gemini reported a quota or rate limit."""

    quota = True


class GeminiEvidenceError(GeminiResponseError):
    """A claim cites evidence absent from the original input."""


@dataclass(frozen=True)
class AttachmentText:
    """An attachment whose text was already extracted."""

    filename: str
    text: str | None
    source_id: str | None = None
    visual: InlineFile | None = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class _Reference(_StrictModel):
    source_id: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    span: str = Field(min_length=1, max_length=500)


class _VisualReference(_StrictModel):
    kind: Literal["visual"]
    source_id: str = Field(min_length=1)
    page_number: int | None = Field(ge=1)
    span: str = Field(min_length=1, max_length=500)


class _Claim(_StrictModel):
    field: OpportunityField | EligibilityField
    value: Value
    evidence: _Reference | _VisualReference


class _Candidate(_StrictModel):
    category: Category
    category_evidence: _Reference | _VisualReference
    source_ids: list[str] = Field(min_length=1)
    claims: list[_Claim]


class _Response(_StrictModel):
    opportunities: list[_Candidate] = Field(max_length=1)


def _sources(
    subject: str | None, body_text: str | None, attachments: Sequence[AttachmentText],
) -> dict[str, tuple[str, list[str]]]:
    sources = {
        "email_subject": ("email subject", (subject or "").split("\n")),
        "email_body": ("email", (body_text or "").split("\n")),
    }
    for index, attachment in enumerate(attachments, 1):
        source_id = attachment.source_id or f"attachment_{index}"
        if source_id in sources:
            raise GeminiInputError("Document source identifiers must be unique.")
        sources[source_id] = (attachment.filename, (attachment.text or "").split("\n"))
    return sources


def _verify(
    reference: _Reference | _VisualReference,
    sources: dict[str, tuple[str, list[str]]],
    associated: set[str],
    media: dict[str, InlineMedia],
    omitted_body_lines: set[int],
) -> dict[str, Any]:
    source_id = reference.source_id
    if source_id not in associated or source_id not in sources:
        raise GeminiEvidenceError("Evidence refers to an unassociated document.")
    label, lines = sources[source_id]
    if isinstance(reference, _VisualReference):
        original = media.get(source_id)
        page = reference.page_number
        if original is None or (
            (page is not None) if original.page_count is None else (page is None or page > original.page_count)
        ):
            raise GeminiEvidenceError("Visual evidence refers to a missing original or invalid page.")
        if not reference.span.strip():
            raise GeminiEvidenceError("Visual evidence requires a readable excerpt.")
        return {"source": label, **reference.model_dump(), "line_number": None, "verified": False}
    index = reference.line_number - 1
    span = " ".join(reference.span.split())
    if source_id == "email_body":
        lines = [clean_email_line(line) for line in lines]
        span = " ".join(" ".join(clean_email_line(line) for line in reference.span.split("\n")).split())
    omitted = omitted_body_lines if source_id == "email_body" else set()
    if not span:
        raise GeminiEvidenceError("Evidence excerpt is empty.")
    # Try the displayed wording first, then tolerate typography differences
    # without changing any alphanumeric characters or URL content.
    for comparison_lines, comparison_span in (
        (lines, span),
        ([_comparison_text(line) for line in lines], _comparison_text(span)),
    ):
        if not comparison_span:
            continue
        if index < len(lines) and index + 1 not in omitted:
            first_line = " ".join(comparison_lines[index].split())
            stop = min((number - 1 for number in omitted if number > index + 1), default=len(lines))
            nearby = " ".join(" ".join(
                comparison_lines[index:min(stop, index + len(comparison_span))]
            ).split())
            position = nearby.find(comparison_span)
            if 0 <= position < len(first_line):
                return {"source": label, **reference.model_dump(), "span": span}
        matches = _quote_lines(comparison_lines, comparison_span, omitted)
        if matches:
            actual_line = min(matches, key=lambda line: (abs(line - reference.line_number), line))
            result = {
                "source": label, "source_id": source_id, "line_number": actual_line, "span": span,
                "citation_corrected": True, "reported_line_number": reference.line_number,
            }
            if len(matches) > 1:
                result["matching_line_numbers"] = matches
            return result
    raise GeminiEvidenceError("Evidence text does not match the cited input line or any visible line in that source.")


_TYPOGRAPHY = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
})
_QUOTE_URL = re.compile(r"(https?://[^\s<>]+)", re.IGNORECASE)


def _comparison_text(text: str) -> str:
    """Normalize typographic equivalents outside URLs for fallback matching."""
    text = clean_email_line(text)
    return "".join(
        part if index % 2 else part.translate(_TYPOGRAPHY)
        for index, part in enumerate(_QUOTE_URL.split(text))
    )


def _quote_lines(lines: Sequence[str], span: str, omitted: set[int]) -> list[int]:
    """Find distinct original starting lines without searching across omissions."""
    found: set[int] = set()
    boundaries = [0, *sorted(omitted), len(lines) + 1]
    for start, stop in zip(boundaries, boundaries[1:]):
        texts, offsets, line_numbers = [], [], []
        offset = 0
        for number in range(start + 1, stop):
            text = " ".join(lines[number - 1].split())
            if text:
                texts.append(text)
                offsets.append(offset)
                line_numbers.append(number)
                offset += len(text) + 1
        normalized = " ".join(texts)
        position = normalized.find(span)
        while position >= 0:
            found.add(line_numbers[bisect_right(offsets, position) - 1])
            position = normalized.find(span, position + 1)
    return sorted(found)


def _request(
    client: genai.Client, model: str, content: str | types.Content, max_attempts: int,
    before_attempt: Callable[[], None] | None,
    config: types.GenerateContentConfig, request_bytes: int,
) -> _Response:
    for attempt in range(max_attempts):
        if before_attempt is not None:
            before_attempt()
        started = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=content, config=config)
            _report_attempt(model, attempt, started, request_bytes, "response_received")
            break
        except errors.APIError as exc:
            detail = f"HTTP {exc.code}" if isinstance(exc.code, int) else "API error"
            _report_attempt(model, attempt, started, request_bytes, detail)
            if (
                exc.code == 429 or exc.status == "RESOURCE_EXHAUSTED"
                or (exc.code == 403 and any(
                    term in str(exc).casefold() for term in ("quota", "ratelimit", "rate limit")
                ))
            ):
                raise GeminiQuotaError(f"Gemini quota or rate limit reached. ({detail})") from None
            if exc.code == 413:
                raise GeminiUnsupportedInputError("Gemini rejected the request size (HTTP 413).") from None
            retryable = exc.code in _TRANSIENT_CODES
        except (httpx2.TimeoutException, TimeoutError):
            detail, retryable = "request timed out", True
            _report_attempt(model, attempt, started, request_bytes, detail)
        except (httpx2.TransportError, ConnectionError):
            detail, retryable = "connection error", True
            _report_attempt(model, attempt, started, request_bytes, detail)
        if not retryable or attempt + 1 == max_attempts:
            error = GeminiUnavailableError if retryable else GeminiExtractionError
            raise error(f"Gemini request failed ({detail}, attempt {attempt + 1}).") from None
        time.sleep(2 ** attempt)

    if any(candidate.finish_reason not in (None, types.FinishReason.STOP) for candidate in response.candidates or []):
        raise GeminiResponseError("Gemini stopped before completing the structured response.")
    if not response.text:
        raise GeminiResponseError("Gemini returned no structured response.")
    return _parse_response(response.text, "gemini")


def _parse_response(text: str, provider: str) -> _Response:
    try:
        return _Response.model_validate_json(text)
    except ValidationError:
        error = GroqExtractionError if provider == "groq" else GeminiResponseError
        label = "Groq" if provider == "groq" else "Gemini"
        raise error(f"{label} returned invalid structured output.") from None


def _report_attempt(model: str, attempt: int, started: float, request_bytes: int, status: str) -> None:
    level = logging.INFO if status == "response_received" else logging.WARNING
    logger.log(level, "provider=gemini model=%r attempt=%d duration_ms=%d request_bytes_estimate=%d status=%s",
               model, attempt + 1, round((time.monotonic() - started) * 1000), request_bytes, status)


def _value_key(field: str, value: Value) -> Any:
    if field == "eligible_departments":
        return frozenset(value)
    if isinstance(value, str):
        return " ".join(value.casefold().split()) if field in {"title", "organizer", "location"} else value
    return value


def _record(
    candidate: _Candidate, sources: dict[str, tuple[str, list[str]]],
    metadata: dict[str, str | None], media: dict[str, InlineMedia], omitted_body_lines: set[int],
    on_rejected_claim: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    associated = set(candidate.source_ids)
    warnings = []
    if not associated.issubset(sources):
        warnings.append("The model associated an unknown document with the opportunity.")
    def rejected(field: str, value: Any, reference: _Reference | _VisualReference, error: Exception) -> None:
        if on_rejected_claim is not None:
            on_rejected_claim({
                "field": field, "value": value, "evidence": reference.model_dump(),
                "reason": str(error),
            })

    try:
        evidence = {"type": _verify(candidate.category_evidence, sources, associated, media, omitted_body_lines)}
    except GeminiEvidenceError as error:
        rejected("type", candidate.category, candidate.category_evidence, error)
        raise
    claims: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in candidate.claims:
        field, value = claim.field, claim.value
        if type(value) not in _FIELD_TYPES[field] or not _valid_claim_value(field, value):
            error = GeminiResponseError("Gemini returned an invalid field value for the extraction schema.")
            rejected(field, value, claim.evidence, error)
            raise error
        try:
            strings = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            if any(has_invalid_controls(text) for text in strings):
                raise GeminiEvidenceError("The extracted value contains invalid control characters.")
            citation = _verify(claim.evidence, sources, associated, media, omitted_body_lines)
            if field == "registration_link" and value not in citation["span"]:
                raise GeminiEvidenceError("The registration link is absent from its cited text.")
            if field == "fee" and not _fee_supported(value, citation, sources):
                raise GeminiEvidenceError("The cited material does not explicitly support the registration or application fee.")
        except GeminiEvidenceError as error:
            rejected(field, value, claim.evidence, error)
            warnings.append(f"Evidence rejected for {field}. {error}")
            continue
        item = {"value": value, **citation}
        if item not in claims[field]:
            claims[field].append(item)

    values: dict[str, Any] = dict.fromkeys(_FIELD_TYPES)
    conflicts = []
    for field, candidates in claims.items():
        if len({_value_key(field, item["value"]) for item in candidates}) > 1:
            conflicts.append({"field": field, "candidates": candidates})
        else:
            first = next((item for item in candidates if item.get("kind") != "visual"), candidates[0])
            values[field] = first["value"]
            evidence[field] = {key: value for key, value in first.items() if key != "value"}
    return {
        "opportunity": {"type": candidate.category, **{field: values[field] for field in get_args(OpportunityField)}},
        "eligibility": {field: values[field] for field in get_args(EligibilityField)},
        "source": {**metadata, "documents": [
            {"source_id": key, "source": label, "filename": label if key not in {"email_subject", "email_body"} else None}
            for key, (label, _) in sources.items() if key in associated
        ]},
        "processing": {
            "classification": candidate.category, "requires_review": False, "review_reasons": [],
            "missing_fields": [field for field, value in values.items() if value is None],
            "conflicts": conflicts, "warnings": list(dict.fromkeys(warnings)),
        },
        "evidence": evidence,
    }


def _valid_claim_value(field: str, value: Value) -> bool:
    if field == "department_scope":
        return value in ("ALL", "SPECIFIC")
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return all(code.strip() for code in value)
    return True


def _fee_supported(value: int | float, citation: dict[str, Any], sources: dict[str, tuple[str, list[str]]]) -> bool:
    """Check a proposed fee against fee-specific wording, never infer a value."""
    span = citation["span"].replace("*", "")
    contexts = [span]
    if citation.get("kind") != "visual":
        line = sources[citation["source_id"]][1][citation["line_number"] - 1].replace("*", "")
        if span in line and (span.casefold().startswith(("free", "completely free")) or re.fullmatch(r"[₹$€£]?\s*[\d,.]+", span)):
            contexts.append(line)
    return any(
        (value == 0 and _FREE_FEE.search(context)) or
        any(float(match.replace(",", "")) == value for match in _FEE_AMOUNT.findall(context))
        for context in contexts
    )


def extract_opportunities(
    subject: str | None,
    body_text: str | None,
    *,
    sender: str | None = None,
    message_id: str | None = None,
    received_at: str | None = None,
    attachments: Sequence[AttachmentText] = (),
    model: str | None = None,
    provider: str | None = None,
    client: genai.Client | None = None,
    max_attempts: int = 1,
    before_attempt: Callable[[], None] | None = None,
    on_rejected_claim: Callable[[dict[str, Any]], None] | None = None,
    budget: ProviderBudget | None = None,
) -> list[dict[str, Any]]:
    """Return zero or one evidence-backed record, or raise an extraction error."""
    if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
        raise GeminiInputError("Gemini max_attempts must be an integer from 1 to 3.")
    sources = _sources(subject, body_text, attachments)
    cleaned_body, omitted_body_lines = clean_email_body(sources["email_body"][1], subject)
    load_dotenv()
    selected_provider = provider if provider is not None else provider_mode()
    if selected_provider not in {"auto", "gemini", "groq"}:
        raise LLMExtractionError("LLM_PROVIDER must be auto, gemini or groq.")
    budget = budget if budget is not None else ProviderBudget(mode=selected_provider)
    input_error = GroqExtractionError if selected_provider == "groq" else GeminiInputError
    if selected_provider == "groq" and client is not None:
        raise GroqExtractionError("The Gemini client argument cannot be used with Groq.")
    media: dict[str, InlineMedia] = {}
    media_bytes = 0
    for number, attachment in enumerate(attachments, 1):
        if attachment.visual is None:
            continue
        source_id = attachment.source_id or f"attachment_{number}"
        try:
            media[source_id] = original = read_inline_file(attachment.visual)
        except ValueError as error:
            raise input_error(f"Attachment {number}: {error}. Processing is incomplete.") from None
        media_bytes += original.encoded_size
        if media_bytes > MAX_REQUEST_BYTES:
            raise input_error("Encoded attachments exceed the request size limit. Processing is incomplete.")
    metadata = {"sender_email": sender, "received_at": received_at, "subject": subject}
    content = json.dumps({
        "sender": sender, "received_at": received_at, "department_aliases": DEPARTMENT_ALIASES,
        "documents": [
            {
                "source_id": key, "label": label,
                "lines": {
                    number: line for number, line in enumerate(cleaned_body if key == "email_body" else lines, 1)
                    if key != "email_body" or (line and number not in omitted_body_lines)
                },
                **({"inline_media": media[key].mime_type, "pdf_pages": media[key].page_count} if key in media else {}),
            }
            for key, (label, lines) in sources.items()
        ],
    }, ensure_ascii=False)
    if len(content) > MAX_INPUT_CHARACTERS:
        raise input_error("The complete document collection exceeds the input size limit.")
    def run(selected: str) -> list[dict[str, Any]]:
        attempts = min(max_attempts, budget.capacity(selected))
        if not attempts:
            error = GroqExtractionError if selected == "groq" else GeminiExtractionError
            raise error(f"{selected} request budget or cooldown prevents this attempt.")
        timeout = _provider_timeout(selected)

        def reserve() -> None:
            if before_attempt is not None:
                before_attempt()
            budget.consume(selected)

        if selected == "gemini":
            selected_model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
            response = _request_gemini(selected_model, content, media, media_bytes, timeout, client, attempts, reserve)
        else:
            selected_model = model if selected_provider == "groq" else None
            selected_model = selected_model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL
            text = request_groq(selected_model, content, media, timeout, attempts, reserve)
            response = _parse_response(text, "groq")
        try:
            records = [_record(item, sources, metadata, media, omitted_body_lines, on_rejected_claim)
                       for item in response.opportunities]
        except GeminiExtractionError as error:
            if selected == "groq":
                raise GroqExtractionError(str(error)) from None
            raise
        logger.info(
            "extraction_completed message_id=%r provider=%s model=%r opportunities=%d",
            message_id, selected, selected_model, len(records),
        )
        return records

    if selected_provider != "auto":
        return run(selected_provider)

    failures = []
    if budget.available("gemini"):
        try:
            return run("gemini")
        except GeminiEvidenceError:
            # A structured classification with unsupported evidence stays an evidence failure.
            raise
        except (GeminiUnavailableError, GeminiQuotaError, GeminiResponseError, GeminiUnsupportedInputError) as error:
            failures.append(str(error))
            if isinstance(error, (GeminiUnavailableError, GeminiQuotaError)):
                budget.cool_down("gemini")
    else:
        failures.append("Gemini request budget or cooldown prevents this attempt.")
    if budget.available("groq"):
        try:
            return run("groq")
        except GroqExtractionError as error:
            if error.quota:
                budget.cool_down("groq")
            failures.append(str(error))
    else:
        failures.append("Groq request budget or cooldown prevents this attempt.")
    raise AutoExtractionError("Automatic extraction failed. " + " ".join(failures))


def _provider_timeout(provider: str) -> int:
    """Resolve the shared timeout or the Gemini-specific compatibility setting."""
    setting = os.getenv("LLM_TIMEOUT_SECONDS")
    if not setting and provider == "gemini":
        setting = os.getenv("GEMINI_TIMEOUT_SECONDS")
    try:
        timeout = int(setting or DEFAULT_TIMEOUT_SECONDS)
        if timeout > 0:
            return timeout
    except ValueError:
        pass
    error = GroqExtractionError if provider == "groq" else GeminiInputError
    raise error("LLM timeout must be a positive integer.")


def _request_gemini(
    model: str | None, content: str, media: dict[str, InlineMedia], media_bytes: int,
    timeout_seconds: int, client: genai.Client | None, max_attempts: int,
    before_attempt: Callable[[], None] | None,
) -> _Response:
    selected_model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
    thinking_level = _THINKING_LEVELS.get(selected_model.removeprefix("models/"))
    http_options = types.HttpOptions(
        timeout=timeout_seconds * 1000, retry_options=types.HttpRetryOptions(attempts=1),
        client_args={"follow_redirects": False},
    )
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT, response_mime_type="application/json",
        response_json_schema=_Response.model_json_schema(),
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level) if thinking_level is not None else None,
        http_options=http_options,
    )
    parts = []
    for source_id, original in media.items():
        parts.extend([
            types.Part.from_text(text=f"Original attachment for source_id {json.dumps(source_id)}"),
            types.Part.from_bytes(data=original.data, mime_type=original.mime_type),
        ])
    # Account for JSON escaping, base64 padding and a conservative SDK envelope allowance.
    request_bytes = (
        media_bytes + len(json.dumps(content, ensure_ascii=True))
        + len(json.dumps(config.model_dump(mode="json", exclude_none=True), ensure_ascii=True))
        + sum(len(json.dumps(part.text, ensure_ascii=True)) + 512 for part in parts)
        + 4096
    )
    if request_bytes > MAX_REQUEST_BYTES:
        raise GeminiUnsupportedInputError("Encoded email and attachments exceed the Gemini request size limit. Processing is incomplete.")
    request = types.Content(role="user", parts=[types.Part.from_text(text=content), *parts]) if parts else content
    if client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise GeminiUnsupportedInputError("Set GEMINI_API_KEY in the environment or local .env file.")
        context = genai.Client(api_key=key, http_options=http_options)
    else:
        context = nullcontext(client)
    with context as active_client:
        return _request(
            active_client, selected_model, request, max_attempts, before_attempt,
            config, request_bytes,
        )
