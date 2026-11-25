import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import main as cli

FIXTURES = Path(__file__).resolve().parents[1] / "sample_emails"


def fixture_text(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def prd_email() -> dict:
    subject, body = fixture_text("prd_hackathon_fields").split("\n\n", 1)
    return {"subject": subject, "body_text": body, "gmail_message_id": "synthetic-id"}


@pytest.mark.parametrize("explain", [False, True])
def test_extract_fields_cli_uses_no_store(monkeypatch, capsys, explain):
    from tests.gemini_mock import mock_gemini

    mock_gemini(monkeypatch)
    message = prd_email()
    body = message["body_text"] + "\nPrivate synthetic marker with no extraction label."
    raw = {
        "id": message["gmail_message_id"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": message["subject"]}],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "list_message_ids", Mock(return_value=[raw["id"]]))
    monkeypatch.setattr(cli, "get_message", Mock(return_value=raw))
    store_lookup = Mock(side_effect=AssertionError("Unexpected store lookup"))
    store_write = Mock(side_effect=AssertionError("Unexpected store write"))
    monkeypatch.setattr(cli, "is_processed", store_lookup)
    monkeypatch.setattr(cli, "mark_processed", store_write)
    args = ["extract-fields", "--max", "10"]
    if explain:
        args.append("--explain")
    assert cli.main(args) == 0
    store_lookup.assert_not_called()
    store_write.assert_not_called()
    output = capsys.readouterr().out
    assert "Private synthetic marker" not in output
    unit = json.loads(output)
    assert unit["opportunity"]["title"] == message["subject"]
    assert all(("span" in item) is explain for item in unit["evidence"].values())
    assert all(len(item.get("span", "")) <= 80 for item in unit["evidence"].values())


def test_extract_fields_cli_prints_one_unit(monkeypatch, capsys):
    from tests.gemini_mock import mock_gemini

    mock_gemini(monkeypatch)
    raw = {
        "id": "synthetic-attachments",
        "payload": {"parts": [
            {"filename": name, "mimeType": "application/pdf", "body": {"attachmentId": name}}
            for name in ("hackathon.pdf", "internship.pdf")
        ]},
    }
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "list_message_ids", Mock(return_value=[raw["id"]]))
    monkeypatch.setattr(cli, "get_message", Mock(return_value=raw))
    monkeypatch.setattr(cli, "download_attachment", Mock(return_value={
        "status": "downloaded", "file_type": "application/pdf", "storage_path": "synthetic.pdf",
    }))
    monkeypatch.setattr(cli, "extract_document", Mock(side_effect=[
        {"status": "success", "text": fixture_text("hackathon_field_conflict")},
        {"status": "success", "text": fixture_text("internship_fields")},
    ]))
    assert cli.main(["extract-fields"]) == 0
    units = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(unit["source"]["attachment_filename"], unit["opportunity"]["type"]) for unit in units] == [
        ("hackathon.pdf", "Hackathon"),
    ]
    assert [item["filename"] for item in units[0]["source"]["attachments"].values()] == [
        "hackathon.pdf", "internship.pdf",
    ]
