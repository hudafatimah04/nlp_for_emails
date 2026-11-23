import json
from unittest.mock import Mock

import pytest

import main as cli
from config import settings
from storage.processed_store import is_processed


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(tmp_path / "processed.db"))
    monkeypatch.setattr(settings, "ALLOWED_SENDERS", ["synthetic-allowlist-entry"])
    service = Mock()
    monkeypatch.setattr(cli, "get_gmail_service", Mock(return_value=service))
    monkeypatch.setattr(cli, "list_message_ids", Mock(return_value=["first"]))
    raw = {"payload": {"headers": [
        {"name": "Subject", "value": "Campus event"},
        {"name": "Message-ID", "value": "<synthetic-id>"},
    ], "parts": [
        {"filename": "sample.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "part"}}
    ]}}
    monkeypatch.setattr(cli, "get_message", Mock(return_value=raw))
    monkeypatch.setattr(cli, "download_attachment", Mock(return_value={
        "status": "downloaded", "storage_path": "sample.pdf", "file_type": "application/pdf",
    }))
    monkeypatch.setattr(cli, "extract_document", Mock(return_value={
        "status": "success", "text": "Summer internship with a stipend. Apply by Friday. Synthetic private marker.",
    }))
    return service


def test_classify_cli_skips_either_id_and_can_reprocess(inbox, capsys):
    args = ["classify", "--max", "4"]
    assert cli.main(args) == 0
    cli.list_message_ids.assert_called_once_with(inbox, settings.ALLOWED_SENDERS, 4)
    output = capsys.readouterr().out
    assert json.loads(output)["classification"] == "Internship"
    assert "Synthetic private marker" not in output
    assert is_processed("first", None)

    cli.get_message.reset_mock()
    assert cli.main(args) == 0
    cli.get_message.assert_not_called()
    assert capsys.readouterr().out.strip() == "skipped (already processed)"

    cli.list_message_ids.return_value = ["second"]
    assert cli.main(args) == 0
    cli.get_message.assert_called_once_with(inbox, "second")
    assert capsys.readouterr().out.strip() == "skipped (already processed)"
    cli.download_attachment.assert_called_once()

    assert cli.main([*args, "--reprocess"]) == 0
    assert json.loads(capsys.readouterr().out)["gmail_message_id"] == "second"
    assert is_processed("second", None)


def test_dry_run_does_not_create_database(inbox, tmp_path, capsys):
    assert cli.main(["classify", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["classification"] == "Internship"
    assert not (tmp_path / "processed.db").exists()


def test_explain_preserves_filenames_and_diagnostics_without_document_text(inbox, capsys):
    cli.get_message.return_value["payload"]["parts"].append({
        "filename": "archive.zip", "mimeType": "application/zip", "body": {},
    })
    cli.download_attachment.side_effect = [
        cli.download_attachment.return_value, {"status": "unsupported"},
    ]
    assert cli.main(["classify", "--explain", "--dry-run"]) == 0
    output = capsys.readouterr().out
    email, attachment, unsupported = json.loads(output)["documents"]
    assert email["source"] == "email"
    assert attachment["source"] == "sample.pdf"
    assert attachment["category_scores"]["Internship"] == attachment["score"]
    assert attachment["matched_terms"]
    assert unsupported["source"] == "archive.zip"
    assert unsupported["classification"] == "Other"
    assert unsupported["rejection_reasons"]["Internship"] == "no_category_term"
    assert "Synthetic private marker" not in output


@pytest.mark.parametrize("failure", ["partial", "failed", "unexpected"])
def test_errors_leave_unprocessed_messages_retryable(inbox, capsys, failure):
    cli.list_message_ids.return_value = ["broken", "another"]
    cli.get_message.side_effect = [
        cli.get_message.return_value,
        {"payload": {"headers": [{"name": "Subject", "value": "Campus hackathon"}]}},
    ]
    if failure == "unexpected":
        cli.extract_document.side_effect = RuntimeError("Synthetic private error text")
    elif failure == "failed":
        cli.download_attachment.return_value = {"status": "failed"}
    else:
        cli.extract_document.return_value = {"status": "partial", "text": "Summer internship"}
    assert cli.main(["classify"]) == 1
    assert not is_processed("broken", None)
    assert is_processed("another", None)
    captured = capsys.readouterr()
    assert "Synthetic private error text" not in captured.err
    if failure != "unexpected":
        assert json.loads(captured.out.splitlines()[0])["requires_review"]
