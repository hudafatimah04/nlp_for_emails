import base64
import json
from unittest.mock import Mock, call

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

import main as cli
from gmail_integration import authentication, email_reader
from gmail_integration.email_parser import parse_message


def text_part(text: str, mime_type: str = "text/plain") -> dict:
    return {
        "mimeType": mime_type,
        "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")},
    }


def http_error(status: int) -> HttpError:
    return HttpError(Response({"status": str(status)}), b"{}")


def test_plain_text_and_sender():
    payload = text_part("Hello, café!")
    payload["headers"] = [
        {"name": "fRoM", "value": "Name <Addr@X.COM>"},
        {"name": "Subject", "value": "Hello"},
        {"name": "Message-ID", "value": "<original@example.com>"},
    ]
    assert parse_message({"id": "gmail-id", "internalDate": "1234", "payload": payload}) == {
        "gmail_message_id": "gmail-id",
        "message_id_header": "<original@example.com>",
        "sender_email": "addr@x.com",
        "subject": "Hello",
        "body_text": "Hello, café!",
        "received_at": "1970-01-01T00:00:01.234000+00:00",
        "attachments": [],
    }


def test_html_only():
    payload = text_part(
        "<head><title>Ignore</title></head><p>Hello&nbsp; world</p><script>ignore()</script><p>Apply now.</p>",
        "text/html",
    )
    assert parse_message({"payload": payload})["body_text"] == "Hello world Apply now."


@pytest.mark.parametrize(
    ("filename", "mime_type", "supported"),
    [
        ("brochure.PDF", "application/octet-stream", True),
        ("handout", "application/pdf", True),
        ("guide.DOCX", "application/octet-stream", True),
        ("guide", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", True),
        ("archive.zip", "application/zip", True),
        ("poster.jpeg", "image/jpeg", True),
        ("poster.png", "application/octet-stream", True),
        ("legacy.doc", "application/msword", False),
    ],
)
def test_nested_multipart_and_attachment(filename, mime_type, supported):
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [text_part("<p>HTML</p>", "text/html"), text_part("Plain")],
            },
            {
                "mimeType": mime_type,
                "filename": filename,
                "body": {"attachmentId": "part-id", "size": 42},
            },
            {**text_part("Attached text"), "filename": "notes.txt"},
        ],
    }
    parsed = parse_message({"payload": payload})
    assert parsed["body_text"] == "Plain"
    assert parsed["attachments"] == [
        {
            "filename": filename,
            "mime_type": mime_type,
            "size": 42,
            "gmail_attachment_id": "part-id",
            "is_supported": supported,
        },
        {
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size": None,
            "gmail_attachment_id": None,
            "is_supported": False,
        },
    ]


def test_empty_body_and_missing_headers():
    assert parse_message({"payload": text_part("")}) == {
        "gmail_message_id": None,
        "message_id_header": None,
        "sender_email": None,
        "subject": None,
        "body_text": None,
        "received_at": None,
        "attachments": [],
    }


@pytest.mark.parametrize("subject", ["Café 招待", "=?UTF-8?Q?Caf=C3=A9_=E6=8B=9B=E5=BE=85?="])
def test_non_ascii_subject(subject):
    payload = {"headers": [{"name": "Subject", "value": subject}]}
    assert parse_message({"payload": payload})["subject"] == "Café 招待"


def test_list_paginates_and_uses_gmail_sender_group():
    service = Mock()
    messages = service.users.return_value.messages.return_value
    messages.list.return_value.execute.side_effect = [
        {"messages": [{"id": "first"}], "nextPageToken": "next"},
        {"messages": [{"id": "second"}], "nextPageToken": "unused"},
    ]
    query = "{from:a@example.com from:b@example.com} is:unread"
    assert email_reader.list_message_ids(
        service, ["a@example.com", "b@example.com"], 2, "is:unread"
    ) == ["first", "second"]
    assert messages.list.call_args_list == [
        call(userId="me", labelIds=["INBOX"], maxResults=2, q=query),
        call(userId="me", labelIds=["INBOX"], maxResults=1, q=query, pageToken="next"),
    ]


def test_list_without_allowlist():
    service = Mock()
    messages = service.users.return_value.messages.return_value
    messages.list.return_value.execute.return_value = {}
    assert email_reader.list_message_ids(service, [], 1000) == []
    messages.list.assert_called_once_with(userId="me", labelIds=["INBOX"], maxResults=500)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_get_retries_temporary_error(monkeypatch, status):
    service = Mock()
    messages = service.users.return_value.messages.return_value
    execute = messages.get.return_value.execute
    execute.side_effect = [http_error(status), {"id": "message-id"}]
    sleep = Mock()
    monkeypatch.setattr(email_reader, "sleep", sleep)

    assert email_reader.get_message(service, "message-id") == {"id": "message-id"}
    messages.get.assert_called_once_with(userId="me", id="message-id", format="full")
    assert execute.call_count == 2
    sleep.assert_called_once_with(1)


@pytest.mark.parametrize("status", [401, 403, 503])
def test_get_fails_with_safe_error(monkeypatch, status):
    service = Mock()
    execute = service.users.return_value.messages.return_value.get.return_value.execute
    execute.side_effect = http_error(status)
    sleep = Mock()
    monkeypatch.setattr(email_reader, "sleep", sleep)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        email_reader.get_message(service, "message-id")
    assert sleep.call_args_list == ([call(1), call(2), call(4)] if status == 503 else [])


@pytest.mark.parametrize("state", ["missing", "valid", "expired", "revoked", "broad"])
def test_oauth_token_lifecycle(monkeypatch, tmp_path, state):
    client = tmp_path / "credentials.json"
    client.write_text("{}")
    token = tmp_path / "token.json"
    monkeypatch.setattr(authentication.settings, "GMAIL_CREDENTIALS_PATH", str(client))
    monkeypatch.setattr(authentication.settings, "GMAIL_TOKEN_PATH", str(token))

    readonly = ("https://www.googleapis.com/auth/gmail.readonly",)
    cached = Mock(scopes=readonly, granted_scopes=None, refresh_token="fake")
    cached.valid = state in {"valid", "broad"}
    cached.to_json.return_value = '{"source": "refreshed"}'
    if state == "revoked":
        cached.refresh.side_effect = RefreshError("fake")
    else:
        def refresh(_request):
            cached.valid = True
        cached.refresh.side_effect = refresh
    if state == "broad":
        cached.scopes = ("https://mail.google.com/",)
    if state != "missing":
        token.write_text('{"source": "cached"}')

    monkeypatch.setattr(authentication.Credentials, "from_authorized_user_file", Mock(return_value=cached))
    request = Mock()
    monkeypatch.setattr(authentication, "Request", Mock(return_value=request))
    new = Mock(granted_scopes=readonly)
    new.to_json.return_value = '{"source": "browser"}'
    flow = Mock()
    flow.run_local_server.return_value = new
    make_flow = Mock(return_value=flow)
    monkeypatch.setattr(authentication.InstalledAppFlow, "from_client_secrets_file", make_flow)
    build = Mock()
    monkeypatch.setattr(authentication, "build", build)

    assert authentication.get_gmail_service() is build.return_value
    if state in {"expired", "revoked"}:
        cached.refresh.assert_called_once_with(request)
    else:
        cached.refresh.assert_not_called()
    if state in {"missing", "revoked", "broad"}:
        make_flow.assert_called_once_with(str(client), scopes=readonly)
        flow.run_local_server.assert_called_once_with(port=0, authorization_prompt_message="")
        build.assert_called_once_with("gmail", "v1", credentials=new)
        assert json.loads(token.read_text())["source"] == "browser"
    else:
        make_flow.assert_not_called()
        build.assert_called_once_with("gmail", "v1", credentials=cached)
        assert json.loads(token.read_text())["source"] == ("cached" if state == "valid" else "refreshed")


def test_missing_oauth_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(authentication.settings, "GMAIL_CREDENTIALS_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="credentials file is missing"):
        authentication.get_gmail_service()


@pytest.mark.parametrize("show_body", [False, True])
def test_cli_body_preview_is_opt_in(monkeypatch, capsys, show_body):
    service = Mock()
    list_ids = Mock(return_value=["id"])
    monkeypatch.setattr(cli, "get_gmail_service", Mock(return_value=service))
    monkeypatch.setattr(cli, "list_message_ids", list_ids)
    monkeypatch.setattr(cli.settings, "ALLOWED_SENDERS", ["sender@example.com"])
    monkeypatch.setattr(cli, "get_message", Mock(return_value={"id": "id", "payload": text_part("x" * 550)}))

    args = ["fetch", "--max", "3"]
    if show_body:
        args.append("--show-body")
    assert cli.main(args) == 0
    list_ids.assert_called_once_with(service, ["sender@example.com"], 3)
    summary = json.loads(capsys.readouterr().out)
    assert summary["gmail_message_id"] == "id"
    assert summary["body_character_count"] == 550
    assert "body_text" not in summary
    assert ("body_preview" in summary) is show_body
    if show_body:
        assert summary["body_preview"] == "x" * 500
