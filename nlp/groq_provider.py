"""Send one strict-schema chat completion through the Groq SDK."""

import base64
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from io import BytesIO
from typing import Any

import groq
import pymupdf
from PIL import Image
from pydantic import BaseModel

from nlp.groq_schema import generation_model, internal_response
from nlp.inline_media import InlineMedia
from nlp.llm_errors import GroqExtractionError, GroqQuotaError
from nlp.llm_prompt import SYSTEM_PROMPT

DEFAULT_GROQ_MODEL = "qwen/qwen3.8-27b"
MAX_IMAGES = 3
IMAGE_TOKENS = 2048
FREE_TOKENS_PER_MINUTE = 8000
# Conservative local limits for inline data and PDF rasterization.
MAX_ENCODED_IMAGE_BYTES = 4 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_EDGE = 4096
DEFAULT_COMPLETION_TOKENS = 3072
MODEL_MAX_COMPLETION_TOKENS = 16_384
_TRANSIENT = {408, 500, 502, 503, 504}
logger = logging.getLogger(__name__)

GROQ_PROMPT = SYSTEM_PROMPT + """

For this request, use the supplied claims object with all 15 field arrays.
Consider every field, and use [] if no source supports it. Set fee to 0
when registration is explicitly free. Set team_based to false for explicit
solo participation. Keep prize money, stipends, fees, participant counts,
dates and academic eligibility separate. Only report min_year or max_year
when the source states an academic-year eligibility requirement.
Use separate entries for genuine conflicting values and cite each one.
Do not invent missing information.

For text and OCR, cite the original line and excerpt using its document ID.
For direct visual readings, use the submitted visual_id beside the image
or rendered PDF page instead of a source_id and page_number. If no visual
parts were submitted, use text evidence exclusively. Keep source_ids as the
original document IDs, including for documents cited through visual IDs.
"""


def _images(original: InlineMedia) -> Iterator[tuple[int | None, str, bytes]]:
    """Yield each original image or every rendered page in source order."""
    if original.page_count is None:
        with Image.open(BytesIO(original.data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS or max(image.size) > MAX_IMAGE_EDGE:
                raise GroqExtractionError("Groq image exceeds the local resolution limit. Processing is incomplete.")
        yield None, original.mime_type, original.data
        return
    try:
        with pymupdf.open(stream=original.data, filetype="pdf") as pdf:
            for number, page in enumerate(pdf, 1):
                rect = page.rect
                scale = min(2, 2000 / max(rect.width, rect.height))
                pixels = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB)
                yield number, "image/jpeg", pixels.tobytes("jpeg", jpg_quality=92)
    except (RuntimeError, ValueError, ZeroDivisionError):
        raise GroqExtractionError("Could not render a complete PDF for Groq. Processing is incomplete.") from None


def _request_body(
    model: str, content: str, media: dict[str, InlineMedia],
) -> tuple[dict[str, Any], int, type[BaseModel], dict[str, tuple[str, int | None]]]:
    image_count = sum(item.page_count or 1 for item in media.values())
    if image_count > MAX_IMAGES:
        raise GroqExtractionError("Groq allows at most three images or PDF pages per request. Processing is incomplete.")
    parts: list[dict[str, Any]] = [{"type": "text", "text": content}]
    visual_sources: dict[str, tuple[str, int | None]] = {}
    for source_id, original in media.items():
        for page, mime_type, data in _images(original):
            if 4 * ((len(data) + 2) // 3) > MAX_ENCODED_IMAGE_BYTES:
                raise GroqExtractionError("An encoded Groq image exceeds the local inline size limit. Processing is incomplete.")
            visual_id = f"visual_{len(visual_sources) + 1}"
            visual_sources[visual_id] = (source_id, page)
            label = f"Original attachment for source_id {json.dumps(source_id)}"
            if page is not None:
                label += f", PDF page {page}"
            label += f' (visual_id "{visual_id}")'
            parts.extend([
                {"type": "text", "text": label},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"}},
            ])
    schema = generation_model(content, visual_sources)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "campus_opportunities", "strict": True, "schema": _strict_schema(schema.model_json_schema())},
    }
    # Estimate for telemetry only. Groq enforces actual context and rate limits.
    text_bytes = len(GROQ_PROMPT.encode()) + len(json.dumps(response_format, separators=(",", ":")).encode())
    text_bytes += sum(len(part["text"].encode()) for part in parts if part["type"] == "text")
    input_tokens = (text_bytes * 2 + 4) // 5 + 384 + image_count * IMAGE_TOKENS
    try:
        completion_limit = int(os.getenv("GROQ_MAX_COMPLETION_TOKENS") or DEFAULT_COMPLETION_TOKENS)
        if not 1 <= completion_limit <= MODEL_MAX_COMPLETION_TOKENS:
            raise ValueError
    except ValueError:
        raise GroqExtractionError(
            f"GROQ_MAX_COMPLETION_TOKENS must be an integer from 1 to {MODEL_MAX_COMPLETION_TOKENS}.",
        ) from None
    body = {
        "model": model,
        "messages": [{"role": "system", "content": GROQ_PROMPT}, {"role": "user", "content": parts}],
        "response_format": response_format,
        "max_completion_tokens": completion_limit,
        "reasoning_effort": "none",
    }
    wire_size = len(json.dumps(body, ensure_ascii=True).encode())
    if wire_size > MAX_REQUEST_BYTES:
        raise GroqExtractionError("Encoded Groq request exceeds the local size limit. Processing is incomplete.")
    combined = input_tokens + completion_limit
    logger.log(logging.WARNING if combined > FREE_TOKENS_PER_MINUTE else logging.INFO,
               "provider=groq model=%s estimated_input_tokens=%d completion_token_limit=%d "
               "estimated_combined_tokens=%d free_tier_tokens_per_minute=%d estimate_is_advisory=true",
               model, input_tokens, completion_limit, combined, FREE_TOKENS_PER_MINUTE)
    return body, wire_size, schema, visual_sources


def _strict_schema(node: Any) -> Any:
    """Copy the schema, omit annotations, and express literals as enums."""
    if isinstance(node, list):
        return [_strict_schema(value) for value in node]
    if not isinstance(node, dict):
        return node

    adapted = {key: _strict_schema(value) for key, value in node.items()}
    if "const" in adapted:
        adapted["enum"] = [adapted.pop("const")]
    if isinstance(adapted.get("title"), str):
        adapted.pop("title")
    return adapted


def request_groq(
    model: str, content: str, media: dict[str, InlineMedia],
    timeout_seconds: int, max_attempts: int, before_attempt: Callable[[], None] | None,
) -> str:
    """Run a bounded Groq request without SDK retries or weaker JSON modes."""
    if model != DEFAULT_GROQ_MODEL:
        raise GroqExtractionError(f"The supported Groq model for strict vision extraction is {DEFAULT_GROQ_MODEL}.")
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise GroqExtractionError("Set GROQ_API_KEY in the environment or local .env file.")
    body, wire_size, generation_schema, visual_sources = _request_body(model, content, media)
    with groq.Groq(api_key=key, base_url="https://api.groq.com", timeout=timeout_seconds, max_retries=0,
                   http_client=groq.DefaultHttpxClient(follow_redirects=False)) as client:
        for attempt in range(1, max_attempts + 1):
            if before_attempt is not None:
                before_attempt()
            started = time.monotonic()
            retryable = False
            quota = False
            try:
                response = client.chat.completions.create(**body)
                choice = response.choices[0] if response.choices else None
                if choice and choice.finish_reason == "stop" and choice.message.content:
                    _report(model, attempt, started, wire_size, "response_received")
                    return internal_response(choice.message.content, generation_schema, visual_sources)
                status = "incomplete structured response"
            except groq.APIStatusError as error:
                status = f"HTTP {error.status_code}"
                details = error.body if isinstance(error.body, dict) else {}
                details = details.get("error", details)
                quota = error.status_code == 429 or (
                    isinstance(details, dict) and details.get("code") in {"insufficient_quota", "rate_limit_exceeded", "quota_exceeded"}
                )
                retryable = error.status_code in _TRANSIENT
            except groq.APITimeoutError:
                status, retryable = "request timed out", True
            except groq.APIConnectionError:
                status, retryable = "connection error", True
            except groq.APIError:
                status = "invalid SDK response"
            _report(model, attempt, started, wire_size, status)
            if quota:
                raise GroqQuotaError(f"Groq quota or rate limit reached ({status}).") from None
            if not retryable or attempt == max_attempts:
                raise GroqExtractionError(f"Groq request failed ({status}, attempt {attempt}).") from None
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("Groq request was called without a positive attempt limit.")


def _report(model: str, attempt: int, started: float, wire_size: int, status: str) -> None:
    logger.log(logging.INFO if status == "response_received" else logging.WARNING,
               "provider=groq model=%s attempt=%d duration_ms=%d request_bytes_estimate=%d status=%s",
               model, attempt, round((time.monotonic() - started) * 1000), wire_size, status)
