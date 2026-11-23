import base64
import json
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

import main as cli
from config import settings
from integration import backend_client
from pipeline.run_pipeline import PipelineServices, process_email
from storage.processed_store import is_delivered, is_processed, mark_processed
from tests.test_extraction import fixture_text


def raw_email(message_id, *, subject="", body="", attachments=()):
    return {
        "id": message_id,
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "Subject", "value": subject}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}},
                *[
                    {"filename": name, "mimeType": "application/pdf", "body": {"attachmentId": identifier}}
                    for name, identifier in attachments
                ],
            ],
        },
    }


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(tmp_path / "local.db"))
    monkeypatch.setattr(settings, "BACKEND_OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setattr(settings, "BACKEND_API_URL", "https://example.invalid/opportunities")
    monkeypatch.setattr(settings, "BACKEND_MODE", "mock")
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", None)
    monkeypatch.setattr(settings, "ATTACHMENT_DIR", str(tmp_path / "attachments"))
    monkeypatch.setattr(settings, "ALLOWED_SENDERS", [])
    docs = {
        "hack-doc": fixture_text("hackathon_field_conflict"),
        "intern-doc": fixture_text("internship_fields"),
    }
    messages = {
        "synthetic-email": raw_email(
            "synthetic-email", body="Synthetic body marker.",
            attachments=[("z-internship.pdf", "intern-doc"), ("a-hackathon.pdf", "hack-doc")],
        ),
    }
    fetch = Mock(side_effect=lambda service, message_id: messages[message_id])
    poll = Mock(side_effect=lambda *args: list(messages))
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "list_message_ids", poll)
    monkeypatch.setattr(cli, "get_message", fetch)
    monkeypatch.setattr(cli, "download_attachment", Mock(side_effect=lambda _, __, attachment: {
        "filename": attachment["filename"], "status": "downloaded", "file_type": "application/pdf",
        "storage_path": str(tmp_path / attachment["gmail_attachment_id"]),
    }))
    monkeypatch.setattr(cli, "extract_document", Mock(side_effect=lambda path, file_type: {
        "status": "success", "text": docs[path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]],
    }))
    post = Mock(side_effect=AssertionError("Live HTTP must not be used"))
    monkeypatch.setattr(backend_client.requests, "post", post)
    monkeypatch.setattr(backend_client, "sleep", Mock())
    return messages, fetch, poll, post


def run_args(tmp_path):
    return ["run", "--out-dir", str(tmp_path / "json")]


def read_tally(capsys):
    return json.loads(capsys.readouterr().out.splitlines()[-1])["tally"]


def test_one_opportunity_processed_and_second_run_skips(inbox, tmp_path, capsys):
    messages, fetch, _, _ = inbox
    messages["synthetic-email"] = raw_email(
        "synthetic-email", subject="Campus hackathon", body="Registration deadline: 25 January 2027."
    )
    assert cli.main(run_args(tmp_path)) == 0
    first = read_tally(capsys)
    assert first["processed"] == first["opportunities_handed_off"] == 1
    assert is_processed("synthetic-email", None)
    assert is_delivered("synthetic-email:1")
    with closing(sqlite3.connect(settings.PROCESSED_DB_PATH)) as connection:
        assert connection.execute("SELECT status FROM processed_emails").fetchone() == ("processed",)
        assert connection.execute("SELECT mode FROM handoff_ledger").fetchone() == ("mock",)
    assert cli.main(run_args(tmp_path)) == 0
    assert read_tally(capsys)["skipped_as_duplicates"] == 1
    fetch.assert_called_once()


def test_library_email_is_marked_not_an_opportunity(inbox, tmp_path, capsys):
    messages, _, _, _ = inbox
    messages["synthetic-email"] = raw_email("synthetic-email", subject="Library hours", body="Open until noon.")
    assert cli.main(run_args(tmp_path)) == 0
    assert read_tally(capsys)["not_an_opportunity"] == 1
    assert not (tmp_path / "json").exists()
    assert not is_delivered("synthetic-email:1")
    with closing(sqlite3.connect(settings.PROCESSED_DB_PATH)) as connection:
        assert connection.execute("SELECT status FROM processed_emails").fetchone() == ("not_an_opportunity",)


def test_attachment_order_and_reprocess_keys_are_stable(inbox, tmp_path, capsys):
    messages, _, _, _ = inbox
    # Identical filenames force the deterministic secondary key to decide order.
    for part in messages["synthetic-email"]["payload"]["parts"][1:]:
        part["filename"] = "same.pdf"
    assert cli.main(run_args(tmp_path)) == 0
    assert read_tally(capsys)["opportunities_handed_off"] == 2
    files = sorted((tmp_path / "json").glob("*.json"))
    first = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert [doc["opportunity"]["type"] for doc in first] == ["Hackathon", "Internship"]
    assert {doc["source"]["gmail_message_id"] for doc in first} == {"synthetic-email"}
    assert {doc["source"]["attachment_filename"] for doc in first} == {"same.pdf"}
    with closing(sqlite3.connect(settings.PROCESSED_DB_PATH)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processed_emails").fetchone() == (1,)
        assert connection.execute("SELECT idempotency_key FROM handoff_ledger ORDER BY idempotency_key").fetchall() == [
            ("synthetic-email:1",), ("synthetic-email:2",),
        ]

    parts = messages["synthetic-email"]["payload"]["parts"]
    parts[1:] = reversed(parts[1:])
    assert cli.main([*run_args(tmp_path), "--reprocess"]) == 0
    second = read_tally(capsys)
    assert second["opportunities_already_delivered"] == 2
    assert second["opportunities_handed_off"] == 0
    assert [json.loads(path.read_text(encoding="utf-8")) for path in files] == first


def test_partial_handoff_retries_only_undelivered_key(inbox, tmp_path, monkeypatch, capsys):
    _, _, _, post = inbox
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    post.side_effect = [Mock(status_code=200), *[Mock(status_code=500) for _ in range(3)]]
    args = run_args(tmp_path)
    assert cli.main(args) == 1
    failed = read_tally(capsys)
    assert failed["errors"] == failed["retryable_errors"] == 1
    assert failed["opportunities_handed_off"] == 1
    assert not is_processed("synthetic-email", None)
    assert is_delivered("synthetic-email:1") and not is_delivered("synthetic-email:2")
    assert [call.kwargs["headers"]["Idempotency-Key"] for call in post.call_args_list] == [
        "synthetic-email:1", *["synthetic-email:2"] * 3,
    ]
    post.reset_mock(side_effect=True)
    post.return_value = Mock(status_code=200)
    assert cli.main(args) == 0
    retried = read_tally(capsys)
    assert retried["opportunities_already_delivered"] == retried["opportunities_handed_off"] == 1
    assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "synthetic-email:2"
    post.assert_called_once()
    assert is_processed("synthetic-email", None)
    assert is_delivered("synthetic-email:2")


def test_backend_400_does_not_block_other_emails(inbox, tmp_path, monkeypatch, capsys):
    messages, _, _, post = inbox
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    monkeypatch.setattr(settings, "BACKEND_API_TOKEN", "synthetic-private-token")
    for message_id in ("synthetic-email", "another-email"):
        messages[message_id] = raw_email(
            message_id, subject="Campus hackathon", body="Registration deadline: 25 January 2027."
        )
    post.side_effect = [Mock(status_code=400, text=settings.BACKEND_API_TOKEN), Mock(status_code=200)]
    assert cli.main(run_args(tmp_path)) == 1
    output = capsys.readouterr().out
    lines = [json.loads(line) for line in output.splitlines()]
    assert lines[0]["outcome"] == "error"
    assert lines[1]["permanent_error"]
    assert lines[-1]["tally"]["permanent_errors"] == 1
    assert lines[-1]["tally"]["processed"] == 1
    assert settings.BACKEND_API_TOKEN not in output
    assert not is_processed("synthetic-email", None)
    assert is_processed("another-email", None)


def test_failed_explicit_reprocess_clears_old_skip_marker(inbox, tmp_path, monkeypatch, capsys):
    messages, _, _, post = inbox
    monkeypatch.setattr(settings, "BACKEND_MODE", "http")
    messages["synthetic-email"] = raw_email(
        "synthetic-email", subject="Campus hackathon", body="Registration deadline: 25 January 2027."
    )
    mark_processed("synthetic-email", None, "Hackathon")
    post.side_effect = [Mock(status_code=422), Mock(status_code=200)]
    args = run_args(tmp_path)
    assert cli.main([*args, "--reprocess"]) == 1
    assert read_tally(capsys)["permanent_errors"] == 1
    assert not is_processed("synthetic-email", None)
    assert cli.main(args) == 0
    assert read_tally(capsys)["opportunities_handed_off"] == 1
    assert is_processed("synthetic-email", None)


def test_rejected_record_is_saved_but_not_handed_off(inbox, tmp_path, monkeypatch, capsys):
    build = cli.build_opportunities

    def reject_one(*args):
        records = build(*args)
        records[0]["opportunity"]["title"] = None
        return records

    monkeypatch.setattr(cli, "build_opportunities", reject_one)
    assert cli.main(run_args(tmp_path)) == 0
    tally = read_tally(capsys)
    assert tally["rejected_not_sent"] == tally["opportunities_handed_off"] == tally["processed"] == 1
    rejected = tmp_path / "json" / "rejected" / "synthetic-email__1.json"
    assert json.loads(rejected.read_text(encoding="utf-8"))["processing"]["status"] == "rejected"
    assert not is_delivered("synthetic-email:1")
    assert is_delivered("synthetic-email:2")
    assert is_processed("synthetic-email", None)
    assert len(list((tmp_path / "outbox").rglob("*.json"))) == 1


def test_dry_run_reports_plans_without_json_outbox_or_database(inbox, tmp_path, capsys):
    assert cli.main([*run_args(tmp_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    summaries = [json.loads(line) for line in output.splitlines()]
    assert summaries[0]["dry_run"]
    assert [line["action"] for line in summaries[1:-1]] == ["would_handoff", "would_handoff"]
    assert summaries[-1]["tally"]["opportunities_handed_off"] == 0
    assert "Synthetic body marker." not in output
    for directory in ("json", "outbox", "local.db"):
        assert not (tmp_path / directory).exists()


def test_run_include_body_is_explicit_and_console_stays_private(inbox, tmp_path, capsys):
    assert cli.main([*run_args(tmp_path), "--include-body"]) == 0
    assert "Synthetic body marker." not in capsys.readouterr().out
    payload = json.loads((tmp_path / "json" / "synthetic-email__1.json").read_text(encoding="utf-8"))
    assert payload["source"]["email_body"] == "Synthetic body marker."
    assert "attachment_text" not in payload["source"]


def test_watch_polls_again_and_handles_interrupt(inbox, tmp_path, monkeypatch, capsys):
    _, _, poll, _ = inbox
    monkeypatch.setattr(settings, "POLL_INTERVAL_SECONDS", 30)
    delay = Mock(side_effect=[None, KeyboardInterrupt()])
    monkeypatch.setattr(cli, "sleep", delay)
    assert cli.main([*run_args(tmp_path), "--watch", "--interval", "4"]) == 0
    captured = capsys.readouterr()
    assert "Run stopped." in captured.out
    assert not captured.err
    assert poll.call_count == 2
    assert [call.args[0] for call in delay.call_args_list] == [4, 4]


def test_fetch_exception_is_safe_and_other_email_still_runs(inbox, tmp_path, capsys):
    messages, fetch, _, _ = inbox
    messages["another-email"] = raw_email("another-email", subject="Library hours")
    fetch.side_effect = [RuntimeError("Synthetic private exception content"), messages["another-email"]]
    assert cli.main(run_args(tmp_path)) == 1
    captured = capsys.readouterr()
    assert "Synthetic private exception content" not in captured.out + captured.err
    assert json.loads(captured.out.splitlines()[-1])["tally"]["not_an_opportunity"] == 1


def test_process_email_handles_exceptions_without_leaking_content(tmp_path):
    services = PipelineServices(
        build_units=Mock(side_effect=ValueError("Synthetic private exception content")),
        out_dir=tmp_path, check_processed=Mock(return_value=False), save_processed=Mock(),
    )
    result = process_email({"gmail_message_id": "synthetic-email"}, services)
    assert result["outcome"] == "error"
    assert result["error_type"] == "ValueError"
    assert result["retryable_error"]
    assert "Synthetic private exception content" not in json.dumps(result)
    services.save_processed.assert_not_called()


def test_incomplete_attachment_processing_defers_handoff(inbox, tmp_path, monkeypatch, capsys):
    extract = cli.extract_document

    def partial(path, file_type):
        result = extract(path, file_type)
        if "intern-doc" in path:
            result["status"] = "partial"
        return result

    monkeypatch.setattr(cli, "extract_document", partial)
    assert cli.main(run_args(tmp_path)) == 1
    tally = read_tally(capsys)
    assert tally["retryable_errors"] == 1
    assert tally["opportunities_handed_off"] == 0
    assert not is_processed("synthetic-email", None)
    assert not is_delivered("synthetic-email:1")
