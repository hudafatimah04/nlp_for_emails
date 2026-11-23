"""Send a validated opportunity to a mock outbox or a backend REST API."""

import re
from pathlib import Path
from time import sleep
from typing import Any, TypedDict

import requests

from config import settings
from output.local_json import write_json_file

_RETRYABLE_STATUSES = {429, 500, 502, 503}
_BACKOFF = (1, 2)


class HandoffResult(TypedDict):
    accepted: bool
    mode: str
    reference: str | None
    error: str | None
    permanent_error: bool


def _result(mode: str, *, reference: str | None = None, error: str | None = None,
            permanent: bool = False) -> HandoffResult:
    return {
        "accepted": error is None,
        "mode": mode,
        "reference": reference,
        "error": error,
        "permanent_error": permanent,
    }


def _write_outbox(payload: dict[str, Any], key: str) -> HandoffResult:
    root = Path(settings.BACKEND_OUTBOX_DIR).expanduser()
    # This split is a placeholder. The backend must still decide whether to insert or hold review records.
    folder, other = ("review", "ready") if payload["processing"]["requires_review"] else ("ready", "review")
    filename = key.replace(":", "__") + ".json"
    path = root / folder / filename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_file(path, payload, reprocess=True)
        (root / other / filename).unlink(missing_ok=True)
    except (OSError, TypeError, ValueError):
        return _result("mock", error="outbox_write_failed")
    return _result("mock", reference=str(path))


def _post(payload: dict[str, Any], key: str) -> HandoffResult:
    if not settings.BACKEND_API_URL:
        return _result("http", error="backend_url_missing")
    headers = {"Content-Type": "application/json", "Idempotency-Key": key}
    if settings.BACKEND_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.BACKEND_API_TOKEN}"

    # Remote deduplication using Idempotency-Key is PENDING CONFIRMATION.
    for attempt in range(len(_BACKOFF) + 1):
        try:
            response = requests.post(
                settings.BACKEND_API_URL, json=payload, headers=headers,
                # Supply an explicit no-op auth handler so redirects and .netrc cannot add credentials.
                timeout=10, allow_redirects=False, auth=lambda request: request,
            )
        except requests.ConnectionError:
            failure = "backend_connection_failed"
        except requests.RequestException:
            return _result("http", error="backend_request_failed")
        else:
            status = response.status_code
            if 200 <= status < 300:
                # Use a local correlation reference. Do not return or log remote response text.
                return _result("http", reference=key)
            if 400 <= status < 500 and status != 429:
                return _result("http", error=f"backend_rejected_{status}", permanent=True)
            if status not in _RETRYABLE_STATUSES:
                return _result("http", error=f"backend_unexpected_status_{status}")
            failure = f"backend_retry_exhausted_{status}"
        if attempt < len(_BACKOFF):
            sleep(_BACKOFF[attempt])
    return _result("http", error=failure)


def send_opportunity(final_json: dict[str, Any], idempotency_key: str) -> HandoffResult:
    """Hand off one eligible record and return a result without remote content."""
    mode = settings.BACKEND_MODE
    if final_json["processing"]["status"] == "rejected":
        return _result(mode, error="rejected_not_sent")
    if not re.fullmatch(r"[A-Za-z0-9_-]+:[1-9][0-9]*", idempotency_key):
        return _result(mode, error="invalid_idempotency_key")
    if mode == "mock":
        return _write_outbox(final_json, idempotency_key)
    if mode == "http":
        return _post(final_json, idempotency_key)
    return _result(mode, error="invalid_backend_mode")
