"""Mock Gemini while exercising the standalone extractor."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors, types

from nlp.llm_extractor import (
    AttachmentText, GeminiEvidenceError, GeminiExtractionError, GeminiInputError, GeminiResponseError,
    extract_opportunities,
)


def ref(span, line=1, source="email_body"):
    return {"source_id": source, "line_number": line, "span": span}


def claim(field, value, citation):
    return {"field": field, "value": value, "evidence": citation}


def candidate(category, citation, *claims, sources=None):
    return {
        "category": category, "category_evidence": citation,
        "source_ids": sources or ["email_body"], "claims": list(claims),
    }


def client_for(*candidates):
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[], text=json.dumps({"opportunities": candidates}),
    )
    return client


def test_solo_hackathon():
    body = "Synthetic AI Hackathon\nCompete solo."
    result, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref("Synthetic AI Hackathon"),
        claim("title", "Synthetic AI Hackathon", ref("Synthetic AI Hackathon")),
        claim("team_based", False, ref("Compete solo.", 2)),
    )))
    assert result["opportunity"]["type"] == "Hackathon"
    assert result["opportunity"]["team_based"] is False
    assert result["eligibility"]["min_cgpa"] is None
    assert not result["processing"]["requires_review"]


def test_festival_is_internal_event():
    body = "Register for Synthetic Festival: music, art and hackathons."
    result, = extract_opportunities(None, body, client=client_for(candidate("Event", ref(body))))
    assert result["opportunity"]["type"] == "Event"


def test_related_attachments_are_sent_together_and_retain_sources():
    body = "Synthetic Hackathon"
    attachments = [
        AttachmentText("poster.pdf", "Registration\n is free.", "poster"),
        AttachmentText("details.docx", "Minimum CGPA: 7.0", "details"),
    ]
    client = client_for(candidate(
        "Hackathon", ref(body), claim("title", body, ref(body)),
        claim("fee", 0, ref("Registration is free.", 1, "poster")),
        claim("min_cgpa", 7.0, ref("Minimum CGPA: 7.0", 1, "details")),
        sources=["email_body", "poster", "details"],
    ))
    result, = extract_opportunities(None, body, attachments=attachments, client=client)
    assert result["opportunity"]["fee"] == 0
    assert result["eligibility"]["min_cgpa"] == 7.0
    assert [doc["filename"] for doc in result["source"]["documents"]] == [None, "poster.pdf", "details.docx"]
    assert result["evidence"]["fee"]["line_number"] == 1
    client.models.generate_content.assert_called_once()
    request = client.models.generate_content.call_args.kwargs
    assert json.loads(request["contents"])["documents"][2]["lines"] == {"1": "Registration", "2": " is free."}
    assert request["config"].response_json_schema


def test_response_with_multiple_opportunities_is_rejected():
    body = "Synthetic Hackathon\nSynthetic Internship"
    client = client_for(candidate("Hackathon", ref("Synthetic Hackathon")),
                        candidate("Internship", ref("Synthetic Internship", 2)))
    with pytest.raises(GeminiResponseError):
        extract_opportunities(None, body, client=client)


def test_conflicting_deadlines_preserve_both_sources():
    body = "Synthetic Hackathon\nRegistration deadline: 2030-02-15"
    attachment = AttachmentText("notice.pdf", "Registration deadline: 2030-02-16")
    record, = extract_opportunities(None, body, attachments=[attachment], client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"),
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("deadline", "2030-02-15", ref("Registration deadline: 2030-02-15", 2)),
        claim("deadline", "2030-02-16", ref("Registration deadline: 2030-02-16", 1, "attachment_1")),
        sources=["email_body", "attachment_1"],
    )))
    assert record["opportunity"]["deadline"] is None
    from validation.data_validator import validate_opportunity

    assert validate_opportunity(record)["requires_review"]
    conflict, = record["processing"]["conflicts"]
    assert conflict["field"] == "deadline"
    assert [(item["value"], item["source"], item["line_number"]) for item in conflict["candidates"]] == [
        ("2030-02-15", "email", 2), ("2030-02-16", "notice.pdf", 1),
    ]


def test_stipend_and_missing_optionals_leave_nulls():
    body = "Synthetic Internship\nStipend: 1000 per month."
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Internship", ref("Synthetic Internship"), claim("title", "Synthetic Internship", ref("Synthetic Internship")),
    )))
    assert record["opportunity"]["fee"] is None
    assert record["opportunity"]["type"] == "Internship"
    assert record["opportunity"]["team_based"] is None
    assert all(value is None for value in record["eligibility"].values())
    assert not record["processing"]["requires_review"]


def test_routine_notice_returns_no_records():
    assert extract_opportunities("Library hours", "Closes at 5.", client=client_for()) == []


@pytest.mark.parametrize("code,attempts", [(503, 3), (403, 1)])
def test_api_failure_is_bounded_and_sanitized(monkeypatch, code, attempts):
    sleep = Mock()
    monkeypatch.setattr("nlp.llm_extractor.time.sleep", sleep)
    client = client_for()
    client.models.generate_content.side_effect = errors.APIError(code, {"error": {"message": "private"}})
    with pytest.raises(GeminiExtractionError, match=f"HTTP {code}, attempt {attempts}") as exc:
        extract_opportunities(None, "Synthetic", client=client, max_attempts=3)
    assert "private" not in str(exc.value)
    assert sleep.call_count == attempts - 1
    assert client.models.generate_content.call_count == attempts


def test_timeout_can_recover(monkeypatch):
    monkeypatch.setattr("nlp.llm_extractor.time.sleep", Mock())
    client = client_for()
    client.models.generate_content.side_effect = [TimeoutError(), client.models.generate_content.return_value]
    assert extract_opportunities(None, "Synthetic", client=client, max_attempts=2) == []
    assert client.models.generate_content.call_count == 2


def test_single_attempt_stops_on_rate_limit(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("nlp.llm_extractor.time.sleep", sleep)
    client = client_for()
    client.models.generate_content.side_effect = errors.APIError(429, {"error": {"message": "rate limit"}})
    with pytest.raises(GeminiExtractionError, match="Gemini quota or rate limit reached"):
        extract_opportunities(None, "Synthetic", client=client, max_attempts=1)
    client.models.generate_content.assert_called_once()
    sleep.assert_not_called()


@pytest.mark.parametrize("citation", [
    ref("Synthetic Hackathon", source="invented"),
    ref("Made-up evidence"),
])
def test_invalid_evidence_is_rejected(citation):
    with pytest.raises(GeminiEvidenceError):
        extract_opportunities(None, "Other line\n\nSynthetic Hackathon",
                              client=client_for(candidate("Hackathon", citation)))


def test_fabricated_registration_url_is_rejected():
    body = "Synthetic Hackathon"
    record, = extract_opportunities(None, body, client=client_for(candidate(
        "Hackathon", ref(body), claim("registration_link", "https://example.org/invented", ref(body)),
    )))
    assert record["opportunity"]["registration_link"] is None
    assert "registration link is absent" in record["processing"]["warnings"][0]


@pytest.mark.parametrize("response_text,finish_reason", [
    ("{}", types.FinishReason.STOP),
    ("", types.FinishReason.STOP),
    ('{"opportunities":[]}', types.FinishReason.MAX_TOKENS),
])
def test_unusable_responses_are_not_other(response_text, finish_reason):
    client = client_for()
    client.models.generate_content.return_value = SimpleNamespace(
        text=response_text, candidates=[SimpleNamespace(finish_reason=finish_reason)],
    )
    with pytest.raises(GeminiResponseError):
        extract_opportunities(None, "Synthetic", client=client)


def test_input_limit_does_not_truncate(monkeypatch):
    monkeypatch.setattr("nlp.llm_extractor.MAX_INPUT_CHARACTERS", 100)
    client = client_for()
    with pytest.raises(GeminiInputError):
        extract_opportunities(None, "Synthetic " * 20, client=client)
    client.models.generate_content.assert_not_called()


def test_boolean_is_not_accepted_as_a_fee():
    body = "Synthetic Hackathon"
    with pytest.raises(GeminiResponseError):
        extract_opportunities(None, body, client=client_for(candidate(
            "Hackathon", ref(body), claim("fee", False, ref(body)),
        )))


@pytest.mark.parametrize(("override", "model", "level"), [
    (None, "gemini-3.5-flash-lite", "MINIMAL"),
    ("gemini-3.8-flash", "gemini-3.8-flash", "LOW"),
    ("models/gemini-3.8-flash", "models/gemini-3.8-flash", "LOW"),
    ("unrecognized-model", "unrecognized-model", None),
])
def test_config_sends_only_model_compatible_thinking(monkeypatch, override, model, level):
    # An empty inherited setting keeps the local .env out of this test.
    monkeypatch.setenv("GEMINI_MODEL", override or "")
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "60")
    client = client_for()
    assert extract_opportunities("Synthetic", "Routine notice", client=client) == []
    request = client.models.generate_content.call_args.kwargs
    config = request["config"]
    assert request["model"] == model
    assert config.temperature is None and config.max_output_tokens is None
    assert (config.thinking_config.thinking_level if config.thinking_config else None) == level
    assert config.http_options.timeout == 60_000
    assert config.http_options.retry_options.attempts == 1
