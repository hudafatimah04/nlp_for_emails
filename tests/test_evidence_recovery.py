"""Verify citation recovery and claim-level rejection without network calls."""

import pytest

from nlp.llm_extractor import AttachmentText, GeminiEvidenceError, extract_opportunities
from pipeline.run_pipeline import format_local_record
from tests.test_llm_extractor import candidate, claim, client_for, ref


@pytest.mark.parametrize(("reported_line", "corrected"), [(2, False), (3, True)])
def test_exact_first_and_unique_multiline_recovery(reported_line, corrected):
    body = "Synthetic notice\r\nSynthetic   AI\r\n hackathon\r\n"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic AI hackathon", reported_line),
    )))
    evidence = record["evidence"]["type"]
    expected = {"source": "email", "source_id": "email_body", "line_number": 2, "span": "Synthetic AI hackathon"}
    if corrected:
        expected.update(citation_corrected=True, reported_line_number=3)
    assert evidence == expected


def test_an_exact_cited_occurrence_wins_without_global_search():
    body = "Synthetic Hackathon\nSynthetic Hackathon"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon", 2),
    )))
    assert record["evidence"]["type"]["line_number"] == 2
    assert "citation_corrected" not in record["evidence"]["type"]


def test_repeated_fabricated_and_unknown_optional_evidence_are_handled_independently():
    body = "Synthetic Hackathon\nHosted by Example Club\nRegistration is free\nRegistration is free"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"),
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("organizer", "Example Club", ref("Hosted by Example Club", 1)),
        claim("fee", 0, ref("Registration is free", 1)),
        claim("location", "Made-up City", ref("Made-up City")),
        claim("description", "Unrelated description", ref("Synthetic Hackathon", source="unknown")),
        sources=["email_body", "unknown"],
    )))
    payload = format_local_record(record, {}, None)
    assert payload["opportunity"]["organizer"] == "Example Club"
    assert payload["evidence"]["organizer"] == {"source": "email", "line_number": 2}
    assert payload["opportunity"]["title"] == "Synthetic Hackathon"
    assert payload["opportunity"]["fee"] == 0
    assert record["evidence"]["fee"]["matching_line_numbers"] == [3, 4]
    missing = {"location", "description"}
    assert all(payload["opportunity"][field] is None for field in missing)
    assert missing <= set(payload["processing"]["missing_fields"])
    assert payload["processing"]["status"] == "ready"
    messages = [warning["message"] for warning in payload["processing"]["warnings"]]
    assert any("location" in message and "does not match" in message for message in messages)
    assert any("description" in message and "unassociated document" in message for message in messages)


def test_invalid_title_fails_required_validation():
    body = "Synthetic Hackathon"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref(body), claim("title", "Invented title", ref("Invented title")),
    )))
    payload = format_local_record(record, {}, None)
    assert payload["processing"]["status"] == "rejected"
    assert any(issue["field"] == "title" and issue["code"] == "missing_required"
               for issue in payload["processing"]["errors"])


@pytest.mark.parametrize(("citation", "error"), [
    (ref("Elsewhere"), "does not match"),
    (ref("Synthetic Hackathon", source="missing"), "unassociated document"),
])
def test_invalid_category_evidence_is_fatal(citation, error):
    with pytest.raises(GeminiEvidenceError, match=error):
        extract_opportunities(
            None, "Synthetic Hackathon\nSynthetic Hackathon\nApply now",
            attachments=[AttachmentText("poster.txt", "Elsewhere")],
            client=client_for(candidate("Hackathon", citation, sources=["email_body", "attachment_1", "missing"])),
        )


def test_cleaned_multiline_quote_recovers_original_start():
    record, = extract_opportunities(None, "> Synthetic AI\n> hackathon", client=client_for(candidate(
        "Hackathon", ref("Synthetic AI hackathon", 2),
    )))
    assert record["evidence"]["type"]["line_number"] == 1


def test_cleaning_does_not_make_a_changed_quote_match():
    with pytest.raises(GeminiEvidenceError, match="does not match"):
        extract_opportunities(None, "> Synthetic AI\n> hackathon", client=client_for(candidate(
            "Hackathon", ref("Synthetic robotics hackathon", 2),
        )))


def test_recovered_citations_keep_conflict_candidates_and_private_diagnostics():
    body = "Synthetic Hackathon\nDeadline: 2030-01-10\nDeadline: 2030-01-11"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"), claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("deadline", "2030-01-10", ref("Deadline: 2030-01-10", 3)),
        claim("deadline", "2030-01-11", ref("Deadline: 2030-01-11", 3)),
    )))
    internal = record["processing"]["conflicts"][0]["candidates"]
    assert internal[0]["citation_corrected"] is True
    payload = format_local_record(record, {}, None)
    assert payload["opportunity"]["deadline"] is None
    candidates = payload["processing"]["conflicts"][0]["candidates"]
    assert [(item["value"], item["line_number"]) for item in candidates] == [
        ("2030-01-10", 2), ("2030-01-11", 3),
    ]
    assert all("citation_corrected" not in item and "reported_line_number" not in item for item in candidates)


@pytest.mark.parametrize(("quoted", "value", "expected"), [
    ("Stipend: \x7f15,000 per month", 0, None),
    ("Prize pool: ₹10,000", 0, None),
    ("Prize pool: ₹10,000", 10_000, None),
    ("Registration is completely FREE", 0, 0),
    ("Free registration", 0, 0),
    ("Registration fee: ₹0", 0, 0),
    ("Registration fee: ₹499", 499, 499),
    ("Registration fee: ₹499", 0, None),
])
def test_evidence_must_support_proposed_fee(quoted, value, expected):
    body = f"Synthetic Hackathon\n{quoted}"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"),
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("fee", value, ref(quoted, 2)),
    )))
    payload = format_local_record(record, {}, None)
    assert payload["opportunity"]["fee"] == expected
    assert payload["processing"]["status"] == "ready"
    assert bool(payload["processing"]["warnings"]) == (expected is None)


def test_invalid_controls_drop_claim_without_rewriting_valid_alternative():
    body = "Synthetic Hackathon\nSynthetic Hackathon Challenge"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"),
        claim("title", "Synthetic\x01 Challenge", ref("Synthetic Hackathon Challenge", 2)),
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
    )))
    payload = format_local_record(record, {}, None)
    assert payload["opportunity"]["title"] == "Synthetic Hackathon"
    assert payload["processing"]["status"] == "ready"
    assert "invalid control characters" in payload["processing"]["warnings"][0]["message"]

def test_repeated_category_recovers_nearest_line_and_preserves_all_locations():
    record, = extract_opportunities(None, "Synthetic Hackathon\nRegister\nSynthetic Hackathon", client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon", 2),
    )))
    assert record["evidence"]["type"]["line_number"] == 1
    assert record["evidence"]["type"]["matching_line_numbers"] == [1, 3]
    assert format_local_record(record, {}, None)["evidence"]["type"] == {"source": "email", "line_number": 1}


def test_typography_normalization_keeps_punctuation_in_urls_exact():
    body = 'Synthetic Hackathon\n**Teams of 2\u20134**\nRegister: https://example.org/a\u2013b'
    diagnostics = []
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"),
        claim("team_based", True, ref("Teams of 2-4", 2)),
        claim("registration_link", "https://example.org/a-b", ref("https://example.org/a-b", 3)),
    )), on_rejected_claim=diagnostics.append)
    assert record["opportunity"]["team_based"] is True
    assert record["opportunity"]["registration_link"] is None
    assert diagnostics == [{
        "field": "registration_link", "value": "https://example.org/a-b",
        "evidence": {"source_id": "email_body", "span": "https://example.org/a-b", "line_number": 3},
        "reason": "Evidence text does not match the cited input line or any visible line in that source.",
    }]


def test_rejected_category_diagnostic_is_available_before_failure():
    diagnostics = []
    with pytest.raises(GeminiEvidenceError, match="does not match"):
        extract_opportunities(None, "Synthetic notice", client=client_for(candidate(
            "Hackathon", ref("Unsupported category", 7),
        )), on_rejected_claim=diagnostics.append)
    assert diagnostics[0]["field"] == "type"
    assert diagnostics[0]["value"] == "Hackathon"
    assert diagnostics[0]["evidence"]["line_number"] == 7
