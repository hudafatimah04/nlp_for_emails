import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import main as cli
from nlp.eligibility_extractor import extract_eligibility
from nlp.extraction_pipeline import build_opportunities
from nlp.information_extractor import extract_opportunity_fields
from nlp.opportunity_classifier import classify_opportunity

FIXTURES = Path(__file__).resolve().parents[1] / "sample_emails"


def fixture_text(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def prd_email() -> dict:
    subject, body = fixture_text("prd_hackathon_fields").split("\n\n", 1)
    return {"subject": subject, "body_text": body, "gmail_message_id": "synthetic-id"}


def build(message: dict, attachments: list) -> list[dict]:
    classification = classify_opportunity(message.get("subject"), message.get("body_text"), attachments)
    return build_opportunities(message, classification, attachments)


@pytest.mark.parametrize(("text", "expected"), [
    ("2nd, 3rd and 4th year", (2, 4)),
    ("2nd to 4th year", (2, 4)),
    ("second year", (2, 2)),
    ("II year", (2, 2)),
    ("III & IV year", (3, 4)),
    ("pre-final and final year", (3, 4)),
    ("final year students", (4, 4)),
])
def test_academic_years(text, expected):
    result = extract_eligibility(text)
    assert (result["min_year"], result["max_year"]) == expected
    assert result["evidence"]["min_year"]["line_number"] == 1
    assert bool(result["warnings"]) == ("final" in text)


@pytest.mark.parametrize(("text", "expected"), [
    ("minimum CGPA of 7.0", 7.0),
    ("CGPA >= 7", 7.0),
    ("7.5 CGPA and above", 7.5),
    ("CGPA of 8 or above", 8.0),
    ("CGPA >= 11", None),
])
def test_cgpa_variants(text, expected):
    result = extract_eligibility(text)
    assert result["min_cgpa"] == expected
    assert bool(result["warnings"]) == (expected is None)


def test_unconverted_semester_and_percentage():
    result = extract_eligibility("6th semester students with 60% and above")
    assert result["min_year"] is result["max_year"] is result["min_cgpa"] is None
    assert any("Semester" in warning for warning in result["warnings"])
    assert any("Percentage" in warning for warning in result["warnings"])


def test_department_scope_and_alias_warnings():
    result = extract_eligibility("Eligible departments: cse, mechanical engineering, ECE, XYZ.")
    assert result["department_scope"] == "SPECIFIC"
    assert result["eligible_departments"] == ["CSE", "ME", "ECE"]
    assert result["warnings"] == ["Unrecognized department codes: XYZ."]
    assert extract_eligibility("Open to all departments.")["department_scope"] == "ALL"
    result = extract_eligibility("Open to all departments. Eligible branches: CSE.")
    assert result["department_scope"] is None
    assert result["eligible_departments"] == ["CSE"]
    assert result["warnings"]
    assert extract_eligibility("Please send it to me.")["department_scope"] is None


@pytest.mark.parametrize("value", [
    "15 December 2026", "15th Dec 2026", "December 15, 2026", "15/12/2026", "2026-12-15",
])
def test_deadline_formats(value):
    result = extract_opportunity_fields(f"Apply by {value}.", "Internship")
    assert result["deadline"] == "2026-12-15"
    assert result["start_date"] is result["end_date"] is None
    assert not result["warnings"]


@pytest.mark.parametrize(("value", "warning"), [
    ("15 December", "four-digit year"),
    ("05/06/2026", "ambiguous"),
    ("31 February 2026", "invalid"),
])
def test_deadline_not_guessed(value, warning):
    result = extract_opportunity_fields(f"Register before {value}.", "Hackathon")
    assert result["deadline"] is None
    assert warning in result["warnings"][0]


@pytest.mark.parametrize("value", [
    "20 December 2026 to 22 December 2026", "20-22 December 2026",
    "from 2026-12-20 to 2026-12-22", "20/12/2026–22/12/2026",
])
def test_event_ranges(value):
    result = extract_opportunity_fields(f"Event dates: {value}.", "Hackathon")
    assert (result["start_date"], result["end_date"]) == ("2026-12-20", "2026-12-22")
    assert not result["warnings"]


@pytest.mark.parametrize(("text", "expected_fee", "expected_team"), [
    ("Registration fee: Rs. 500\nTeam of 2-4.", 500, True),
    ("Entry fee INR 1,000.50\nTeams of up to 4.", 1000.5, True),
    ("Fee: ₹250\nSolo.", 250, False),
    ("No registration fee. Individual participation.", 0, False),
    ("Free of cost.", 0, None),
    ("Free", 0, None),
    ("Join this free hackathon.", 0, None),
    ("No registration fee waiver is available.", None, None),
    ("Stipend: INR 500", None, None),
])
def test_fee_and_participation(text, expected_fee, expected_team):
    result = extract_opportunity_fields(text, "Hackathon")
    assert result["fee"] == expected_fee
    assert result["team_based"] is expected_team


def test_labels_and_preferred_url():
    text = (
        "Event Name: Campus Hackathon\nHosted by Synthetic Computing Club.\n"
        "Mode: Hybrid\nMore information: https://example.invalid/info\n"
        "Register:\nhttps://example.invalid/entry"
    )
    result = extract_opportunity_fields(text, "Hackathon", {"source": "doc.pdf", "subject": "Ignored"})
    assert result["title"] == "Campus Hackathon"
    assert result["organizer"] == "Synthetic Computing Club"
    assert result["location"] == "Hybrid"
    assert result["registration_link"] == "https://example.invalid/entry"
    assert result["evidence"]["registration_link"]["line_number"] == 6
    assert result["evidence"]["organizer"]["source"] == "doc.pdf"
    result = extract_opportunity_fields(text + "\nApply: https://example.invalid/other", "Hackathon")
    assert result["registration_link"] is None and result["warnings"]


def test_prd_example_fields():
    unit, = build(prd_email(), [])
    assert unit["opportunity"] == {
        "type": "Hackathon", "title": "National Hackathon 2026",
        "description": "Registrations are now open for National Hackathon 2026.",
        "organizer": None, "deadline": "2026-12-15", "start_date": "2026-12-20",
        "end_date": "2026-12-22", "location": None, "registration_link": None,
        "fee": None, "team_based": None,
    }
    assert unit["eligibility"] == {
        "min_year": 2, "max_year": 4, "min_cgpa": 7.0, "department_scope": "SPECIFIC",
        "eligible_departments": ["CSE", "ISE", "ECE"],
    }
    assert unit["evidence"]["title"]["source"] == "email subject"
    assert unit["source"]["attachment_filename"] is None
    assert unit["processing"]["requires_review"]


def test_email_and_attachment_conflicts_leave_values_null():
    unit, = build(prd_email(), [("brochure.docx", fixture_text("hackathon_field_conflict"))])
    assert unit["opportunity"]["deadline"] is None
    assert unit["opportunity"]["title"] == "National Hackathon 2026"
    assert unit["opportunity"]["location"] == "Campus Lab"
    assert unit["processing"]["requires_review"]
    assert unit["processing"]["conflicts"] == [
        {"field": "deadline", "candidates": [
            {"value": "2026-12-18", "source": "brochure.docx"},
            {"value": "2026-12-15", "source": "email"},
        ]},
        {"field": "department_scope", "candidates": [
            {"value": "ALL", "source": "brochure.docx"},
            {"value": "SPECIFIC", "source": "email"},
        ]},
    ]
    assert "deadline" not in unit["evidence"]


def test_missing_fields_and_ignored_attachments():
    unit, = build({"subject": "Campus Hackathon", "body_text": "Teams can compete for a prize."}, [
        ("reading.pdf", "Technical notes about material conductivity.")
    ])
    assert unit["opportunity"]["deadline"] is unit["eligibility"]["min_cgpa"] is None
    assert {"deadline", "min_cgpa"} <= set(unit["processing"]["missing_fields"])
    assert unit["processing"]["requires_review"]
    assert "attachment reading.pdf ignored (not an opportunity document)" in unit["processing"]["warnings"]


def test_two_attachments_make_two_opportunities():
    message = {"body_text": "Please find attached the details."}
    units = build(message, [
        ("internship.docx", fixture_text("hackathon_field_conflict")),
        ("flyer.pdf", fixture_text("internship_fields")),
    ])
    assert [(unit["opportunity"]["type"], unit["source"]["attachment_filename"]) for unit in units] == [
        ("Hackathon", "internship.docx"), ("Internship", "flyer.pdf"),
    ]
    assert units[1]["opportunity"]["title"] == "Summer Internship"
    assert units[1]["opportunity"]["organizer"] == "Synthetic Research Lab"
    assert units[1]["opportunity"]["registration_link"] == "https://example.invalid/apply"


def test_same_category_attachments_are_not_combined_or_enriched_by_email():
    brochure = fixture_text("hackathon_field_conflict")
    units = build(prd_email(), [("same.pdf", brochure), ("same.pdf", brochure.replace("18 December", "19 December"))])
    assert [unit["opportunity"]["deadline"] for unit in units] == ["2026-12-18", "2026-12-19"]
    for unit in units:
        assert unit["eligibility"]["min_cgpa"] is None
        assert unit["processing"]["requires_review"]
        assert any("kept as separate opportunities" in reason for reason in unit["processing"]["review_reasons"])


def test_library_email_is_not_an_opportunity():
    subject, body = fixture_text("library").split("\n\n", 1)
    assert build({"subject": subject, "body_text": body}, []) == []


@pytest.mark.parametrize("explain", [False, True])
def test_extract_fields_cli_uses_no_store(monkeypatch, capsys, explain):
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
    assert unit["opportunity"]["deadline"] == "2026-12-15"
    assert all(("span" in item) is explain for item in unit["evidence"].values())
    assert all(len(item.get("span", "")) <= 80 for item in unit["evidence"].values())


def test_extract_fields_cli_prints_each_attachment_unit(monkeypatch, capsys):
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
        ("hackathon.pdf", "Hackathon"), ("internship.pdf", "Internship"),
    ]
