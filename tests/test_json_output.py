import base64
import json
from datetime import date
from unittest.mock import Mock

import pytest

import main as cli
from output.json_formatter import format_opportunity
from tests.test_extraction import fixture_text
from validation.data_validator import validate_opportunity


def test_formatter_keeps_same_sections_and_null_keys_for_rejected_records():
    parsed = {"gmail_message_id": "synthetic-id", "body_text": "Synthetic body.", "received_at": "2027-01-01T00:00:00Z"}
    source = {"filename": "example.pdf", "file_type": "application/pdf",
              "storage_path": "attachments/example.pdf", "extraction_status": "success"}
    record = {
        "opportunity": {"type": "Hackathon", "title": "Synthetic competition", "fee": 0, "team_based": None},
        "eligibility": {"department_scope": "SPECIFIC", "eligible_departments": ["CSE", "ECE"]},
        "evidence": {"title": {"source": "example.pdf", "line_number": 1, "span": "Synthetic evidence preview"}},
    }
    valid = format_opportunity(validate_opportunity(record, date(2027, 1, 1)), parsed, source)
    record["opportunity"]["title"] = None
    record["opportunity"]["fee"] = None
    rejected = format_opportunity(validate_opportunity(record, date(2027, 1, 1)), parsed, source)
    assert set(valid) == set(rejected) == {"schema_version", "opportunity", "eligibility", "source", "processing", "evidence"}
    assert valid["schema_version"] == "1.0"
    for section in ("opportunity", "eligibility", "source", "processing"):
        assert valid[section].keys() == rejected[section].keys()
    assert set(valid["source"]) == {
        "gmail_message_id", "message_id_header", "sender_email", "subject", "received_at",
        "attachment_filename", "attachment_file_type", "attachment_storage_path", "attachment_extraction_status",
    }
    assert valid["source"]["sender_email"] is None
    assert valid["source"]["attachment_extraction_status"] == "success"
    assert valid["opportunity"]["fee"] == 0
    assert valid["opportunity"]["team_based"] is None
    assert rejected["opportunity"]["fee"] is None
    assert rejected["processing"]["status"] == "rejected"
    assert valid["eligibility"]["eligible_departments"] == ["CSE", "ECE"]
    assert valid["evidence"]["title"] == {"source": "example.pdf", "line_number": 1}
    assert "email_body" not in valid["source"]
    assert json.loads(json.dumps(rejected, allow_nan=False)) == rejected


@pytest.fixture
def synthetic_inbox(monkeypatch):
    marker = "Private synthetic body marker."
    raw = {
        "id": "synthetic-attachments",
        "internalDate": "1798797600000",
        "payload": {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(marker.encode()).decode()}},
            *[
                {"filename": name, "mimeType": "application/pdf", "body": {"attachmentId": name}}
                for name in ("hackathon.pdf", "internship.pdf")
            ],
        ]},
    }
    body_lookup = Mock(return_value=raw)
    paths = {"hackathon.pdf": fixture_text("hackathon_field_conflict"), "internship.pdf": fixture_text("internship_fields")}
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "list_message_ids", Mock(return_value=[raw["id"]]))
    monkeypatch.setattr(cli, "get_message", body_lookup)
    monkeypatch.setattr(cli, "download_attachment", Mock(side_effect=lambda service, message_id, attachment: {
        "status": "downloaded", "file_type": "application/pdf", "storage_path": attachment["filename"],
    }))
    monkeypatch.setattr(cli, "extract_document", Mock(side_effect=lambda path, file_type: {
        "status": "success", "text": paths[path],
    }))
    for name in ("is_processed", "mark_processed"):
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError("Processed store must remain unused")))
    return marker


def test_build_json_writes_two_files_and_opt_in_body(synthetic_inbox, tmp_path, capsys):
    args = ["build-json", "--out-dir", str(tmp_path)]
    assert cli.main(args) == 0
    summaries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    files = sorted(tmp_path.glob("*.json"))
    assert [file.name for file in files] == ["synthetic-attachments__1.json", "synthetic-attachments__2.json"]
    payloads = [json.loads(file.read_text(encoding="utf-8")) for file in files]
    assert [item["source"]["attachment_filename"] for item in payloads] == ["hackathon.pdf", "internship.pdf"]
    assert {item["source"]["gmail_message_id"] for item in payloads} == {"synthetic-attachments"}
    assert all(item["source"]["received_at"] for item in payloads)
    assert all(item["source"]["attachment_extraction_status"] == "success" for item in payloads)
    assert all(item["source"]["attachment_file_type"] == "application/pdf" for item in payloads)
    assert all("email_body" not in item["source"] for item in payloads)
    assert len(summaries) == 2 and all(summary["written"] for summary in summaries)
    assert synthetic_inbox not in json.dumps(payloads + summaries)

    assert cli.main([*args, "--include-body"]) == 0
    assert all(not json.loads(line)["written"] for line in capsys.readouterr().out.splitlines())
    assert cli.main([*args, "--include-body", "--reprocess"]) == 0
    captured = capsys.readouterr()
    assert synthetic_inbox not in captured.out
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        assert payload["source"]["email_body"] == synthetic_inbox
        assert "attachment_text" not in payload["source"]
    cli.is_processed.assert_not_called()
    cli.mark_processed.assert_not_called()


def test_build_json_still_writes_rejected_records(synthetic_inbox, tmp_path, monkeypatch, capsys):
    extract = cli.build_opportunities

    def without_title(*args):
        records = extract(*args)
        records[0]["opportunity"]["title"] = None
        return records

    monkeypatch.setattr(cli, "build_opportunities", without_title)
    assert cli.main(["build-json", "--out-dir", str(tmp_path)]) == 0
    payload = json.loads((tmp_path / "synthetic-attachments__1.json").read_text(encoding="utf-8"))
    assert payload["processing"]["status"] == "rejected"
    assert payload["opportunity"]["title"] is None
    assert any(issue["code"] == "missing_required" for issue in payload["processing"]["errors"])
    assert json.loads(capsys.readouterr().out.splitlines()[0])["status"] == "rejected"
