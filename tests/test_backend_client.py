import json
from unittest.mock import Mock

import pytest
import requests

from config import settings
from integration import backend_client


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_MODE", "mock")
    monkeypatch.setattr(settings, "BACKEND_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(settings, "BACKEND_API_URL", "https://example.invalid/opportunities")
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", None)
    post = Mock()
    delay = Mock()
    monkeypatch.setattr(backend_client.requests, "post", post)
    monkeypatch.setattr(backend_client, "sleep", delay)
    return tmp_path / "outbox", post, delay


def payload(status="ready", review=False):
    return {"processing": {"status": status, "requires_review": review}, "opportunity": {"title": "Example event"}}


@pytest.mark.parametrize(("requires_review", "folder"), [(False, "ready"), (True, "review")])
def test_mock_writes_to_the_expected_bucket(backend, requires_review, folder):
    outbox, post, _ = backend
    record = payload("needs_review" if requires_review else "ready", requires_review)
    result = backend_client.send_opportunity(record, "synthetic-message:1")
    expected = outbox / folder / "synthetic-message__1.json"
    assert result == {
        "accepted": True, "mode": "mock", "reference": str(expected),
        "error": None, "permanent_error": False,
    }
    assert json.loads(expected.read_text(encoding="utf-8")) == record
    post.assert_not_called()


def test_mock_replaces_same_key_even_if_review_bucket_changes(backend):
    outbox, _, _ = backend
    key = "synthetic-message:1"
    backend_client.send_opportunity(payload(), key)
    changed = payload()
    changed["opportunity"]["title"] = "Updated synthetic title"
    backend_client.send_opportunity(changed, key)
    files = list(outbox.rglob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == changed
    backend_client.send_opportunity(payload("needs_review", True), key)
    assert [path.parent.name for path in outbox.rglob("*.json")] == ["review"]


@pytest.mark.parametrize("mode", ["mock", "http"])
def test_rejected_never_writes_or_sends(backend, monkeypatch, mode):
    outbox, post, _ = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", mode)
    assert backend_client.send_opportunity(payload("rejected", True), "synthetic-message:1") == {
        "accepted": False, "mode": mode, "reference": None,
        "error": "rejected_not_sent", "permanent_error": False,
    }
    post.assert_not_called()
    assert not outbox.exists()


@pytest.mark.parametrize("token", [None, "synthetic-token-value"])
def test_http_success_and_headers(backend, monkeypatch, token):
    _, post, delay = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", token)
    post.return_value = Mock(status_code=201)
    record = payload()
    result = backend_client.send_opportunity(record, "synthetic-message:7")
    assert result["accepted"] and result["reference"] == "synthetic-message:7"
    request = post.call_args
    assert request.args == (settings.BACKEND_API_URL,)
    assert request.kwargs["timeout"] == 10
    assert request.kwargs["allow_redirects"] is False
    assert request.kwargs["json"] == record
    expected_headers = {"Idempotency-Key": "synthetic-message:7", "Content-Type": "application/json"}
    if token:
        expected_headers["Authorization"] = f"Bearer {token}"
    assert request.kwargs["headers"] == expected_headers
    delay.assert_not_called()


@pytest.mark.parametrize("initial", [500, 502, 503, 429, requests.ConnectionError("Synthetic failure")])
def test_http_retries_transient_failures(backend, monkeypatch, initial):
    _, post, delay = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    first = Mock(status_code=initial) if isinstance(initial, int) else initial
    post.side_effect = [first, Mock(status_code=200)]
    assert backend_client.send_opportunity(payload(), "synthetic-message:1")["accepted"]
    assert post.call_count == 2
    delay.assert_called_once_with(1)
    assert {call.kwargs["headers"]["Idempotency-Key"] for call in post.call_args_list} == {"synthetic-message:1"}


def test_http_exhaustion_does_not_disclose_sensitive_response(backend, monkeypatch, capsys, caplog):
    _, post, delay = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    token = "synthetic-private-token"
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", token)
    post.return_value = Mock(status_code=500, text=f"Failure with {token}")
    result = backend_client.send_opportunity(payload(), "synthetic-message:1")
    assert result == {
        "accepted": False, "mode": "http", "reference": None,
        "error": "backend_retry_exhausted_500", "permanent_error": False,
    }
    assert post.call_count == 3
    assert [call.args[0] for call in delay.call_args_list] == [1, 2]
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err + caplog.text + json.dumps(result)


def test_http_connection_exception_does_not_disclose_token(backend, monkeypatch, capsys):
    _, post, _ = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    token = "synthetic-private-token"
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", token)
    post.side_effect = requests.ConnectionError(f"Synthetic failure with {token}")
    result = backend_client.send_opportunity(payload(), "synthetic-message:1")
    assert result["error"] == "backend_connection_failed"
    assert post.call_count == 3
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err + json.dumps(result)


@pytest.mark.parametrize("status", [400, 422])
def test_http_permanent_failures_are_not_retried(backend, monkeypatch, status):
    _, post, delay = backend
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    post.return_value = Mock(status_code=status)
    result = backend_client.send_opportunity(payload(), "synthetic-message:1")
    assert not result["accepted"] and result["permanent_error"]
    assert result["error"] == f"backend_rejected_{status}"
    post.assert_called_once()
    delay.assert_not_called()
