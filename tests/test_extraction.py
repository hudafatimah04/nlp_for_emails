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

@pytest.fixture
def labelled_document() -> str:
    return (
        "Synthetic Hackathon\n"
        "Organizer: InnovateX Foundation\n"
        "Event Start Date: 10 January 2027\n"
        "Event End Date: 12 January 2027\n"
        "Registration Opens: 1 December 2026\n"
        "Registration Deadline: 20 December 2026\n"
        "Minimum CGPA required: 7.0.\n"
        "Participation is team-based.\n"
        "Maximum team size: 4 students."
    )


def test_labelled_document_fields_and_evidence(labelled_document):
    opportunity = extract_opportunity_fields(labelled_document, "Hackathon", {"source": "synthetic.pdf"})
    eligibility = extract_eligibility(labelled_document, source="synthetic.pdf")
    expected = {
        "organizer": ("InnovateX Foundation", 2),
        "start_date": ("2027-01-10", 3),
        "end_date": ("2027-01-12", 4),
        "deadline": ("2026-12-20", 6),
        "team_based": (True, 8),
    }
    for field, (value, line) in expected.items():
        assert opportunity[field] == value
        assert opportunity["evidence"][field]["line_number"] == line
        assert opportunity["evidence"][field]["source"] == "synthetic.pdf"
    assert eligibility["min_cgpa"] == 7.0
    assert eligibility["evidence"]["min_cgpa"]["line_number"] == 7
    assert not opportunity["warnings"] and not eligibility["warnings"]


@pytest.mark.parametrize("label", [
    "Organizer:", "Organiser:", "Organized by", "Organised by", "Presented by", "Company Name:",
])
def test_organizer_labels_keep_the_whole_line(label):
    result = extract_opportunity_fields(f"Event flyer\n{label} InnovateX Labs, Inc. (Campus Unit);", "Hackathon")
    assert result["organizer"] == "InnovateX Labs, Inc. (Campus Unit)"
    assert result["evidence"]["organizer"]["line_number"] == 2


@pytest.mark.parametrize(("field", "label"), [
    ("start_date", "Event Start Date:"), ("start_date", "Start Date:"),
    ("start_date", "Starts on"), ("start_date", "Event Date:"),
    ("start_date", "Commencement Date:"), ("start_date", "Internship Start:"),
    ("end_date", "Event End Date:"), ("end_date", "End Date:"),
    ("end_date", "Ends on"), ("end_date", "Internship End:"),
])
def test_single_date_labels(field, label):
    result = extract_opportunity_fields(f"Event flyer\n{label} 10 January 2027", "Hackathon")
    assert result[field] == "2027-01-10"
    assert result["evidence"][field]["line_number"] == 2
    assert not result["warnings"]


@pytest.mark.parametrize("opening", [
    "Registration Opens: 1 December 2026",
    "Registration Start Date: 1 December 2026",
    "Registration starts on 1 December 2026",
    "Applications open: 1 December 2026 to 15 December 2026",
    "Registration opens:\n1-15 December 2026",
])
def test_opening_dates_do_not_become_event_dates_or_deadlines(opening):
    result = extract_opportunity_fields(opening, "Hackathon")
    assert result["start_date"] is result["end_date"] is result["deadline"] is None
    assert not result["warnings"]


def test_opening_and_deadline_on_same_line():
    result = extract_opportunity_fields(
        "Registration opens 1 December 2026 and registration deadline: 20 December 2026.", "Hackathon"
    )
    assert result["deadline"] == "2026-12-20"
    assert result["start_date"] is None
    assert not result["warnings"]


def test_labelled_date_range_is_still_recognized():
    result = extract_opportunity_fields("Event Date: 10-12 January 2027", "Hackathon")
    assert (result["start_date"], result["end_date"]) == ("2027-01-10", "2027-01-12")
    assert not result["warnings"]


@pytest.mark.parametrize(("field", "label"), [
    ("start_date", "Start Date"), ("end_date", "End Date"), ("deadline", "Registration Deadline"),
])
def test_conflicting_date_labels_preserve_candidates_in_warning(field, label):
    result = extract_opportunity_fields(
        f"{label}: 10 January 2027\n{label}: 12 January 2027\n", "Hackathon"
    )
    assert result[field] is None
    assert field not in result["evidence"]
    assert result["warnings"] == [
        f"Multiple candidates found for {field}: 2027-01-10 (line 1), 2027-01-12 (line 2)."
    ]


@pytest.mark.parametrize(("text", "expected"), [
    ("Minimum CGPA required: 7.0.", 7.0),
    ("CGPA required: 7", 7.0),
    ("Minimum CGPA: 7.0", 7.0),
    ("CGPA criteria: 7.5", 7.5),
    ("CGPA of at least 7", 7.0),
    ("CGPA not less than 7", 7.0),
    ("Minimum CGPA required: 70%", None),
    ("CGPA criteria: 11", None),
])
def test_new_cgpa_labels_and_limits(text, expected):
    result = extract_eligibility(f"Synthetic eligibility\n{text}")
    assert result["min_cgpa"] == expected
    if expected is not None:
        assert result["evidence"]["min_cgpa"]["line_number"] == 2
        assert not result["warnings"]
    else:
        assert result["warnings"]


@pytest.mark.parametrize(("text", "expected"), [
    ("Participation is team-based.", True), ("This is team based.", True),
    ("Teams of 3 may apply.", True), ("Maximum team size: 4 students.", True),
    ("Form a team.", True), ("Participate in teams.", True),
    ("Individual participation.", False), ("Solo.", False), ("On an individual basis.", False),
])
def test_team_wording(text, expected):
    result = extract_opportunity_fields(f"Event flyer\n{text}", "Hackathon")
    assert result["team_based"] is expected
    assert result["evidence"]["team_based"]["line_number"] == 2


def test_conflicting_team_wording():
    result = extract_opportunity_fields("Participation is team-based.\nIndividual participation permitted.", "Hackathon")
    assert result["team_based"] is None
    assert result["warnings"] == ["Multiple candidates found for team_based."]


@pytest.mark.parametrize("source", ["subject", "document"])
def test_title_ocr_cleanup_preserves_meaningful_date_suffix(source):
    noisy = "Fw: FWD: re: Campus Internship (May -July'27) |Al @ @"
    text, context = ("", {"subject": noisy}) if source == "subject" else (noisy, {})
    result = extract_opportunity_fields(text, "Internship", context)
    assert result["title"] == "Campus Internship (May -July'27)"
    assert result["evidence"]["title"]["line_number"] == 1


def test_labelled_title_takes_precedence_over_sample_banner():
    labelled = extract_opportunity_fields(
        "TEST SAMPLE — Untitled flyer\nEvent Name: Campus Hackathon (May -July'27)", "Hackathon"
    )
    assert labelled["title"] == "Campus Hackathon (May -July'27)"
    assert labelled["evidence"]["title"]["line_number"] == 2
    unlabelled = extract_opportunity_fields("TEST SAMPLE — Campus AI", "Hackathon")
    assert unlabelled["title"] == "TEST SAMPLE — Campus AI"
