"""Retrieve inbox messages through the Gmail API."""

from collections.abc import Sequence
from time import sleep
from typing import Any

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

_RETRY_DELAYS = (1, 2, 4)
_TEMPORARY_STATUSES = {429, 500, 503}


def execute_request(request: HttpRequest) -> dict[str, Any]:
    """Execute a Gmail request with retry and backoff for temporary errors."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return request.execute()
        except RefreshError:
            raise RuntimeError("Gmail authorization expired. Reauthorize the monitored account.") from None
        except HttpError as error:
            status = error.resp.status
            if status in {401, 403}:
                raise RuntimeError(
                    f"Gmail denied access (HTTP {status}). Check account authorization and Gmail API access."
                ) from None
            if status not in _TEMPORARY_STATUSES or attempt == len(_RETRY_DELAYS):
                raise RuntimeError(f"Gmail request failed (HTTP {status}).") from None
        sleep(_RETRY_DELAYS[attempt])


def list_message_ids(
    service: Resource,
    allowed_senders: Sequence[str],
    max_results: int,
    query_extra: str | None = None,
) -> list[str]:
    """Return up to max_results inbox message IDs across Gmail result pages."""
    if max_results <= 0:
        return []

    query = " ".join(f"from:{sender}" for sender in allowed_senders)
    if query:
        query = "{" + query + "}"
    if query_extra:
        query = f"{query} {query_extra}".strip()

    messages = service.users().messages()
    params: dict[str, Any] = {"userId": "me", "labelIds": ["INBOX"]}
    if query:
        params["q"] = query

    ids: list[str] = []
    while len(ids) < max_results:
        remaining = max_results - len(ids)
        page = execute_request(messages.list(maxResults=min(500, remaining), **params))
        ids.extend(message["id"] for message in page.get("messages", [])[:remaining])
        page_token = page.get("nextPageToken")
        if not page_token:
            break
        params["pageToken"] = page_token
    return ids


def get_message(service: Resource, message_id: str) -> dict[str, Any]:
    """Fetch the full Gmail message payload."""
    return execute_request(service.users().messages().get(userId="me", id=message_id, format="full"))
