"""Exercise the official Groq SDK against an offline HTTP transport."""

import base64
import json
from unittest.mock import Mock

import httpx
import pytest
from groq import Groq

import main as cli
from config import settings
from nlp import groq_provider
from nlp.groq_schema import FIELD_TYPES
from nlp.llm_errors import GroqExtractionError, LLMExtractionError
from nlp.llm_extractor import _Response, extract_opportunities
from storage.processed_store import is_processed
from tests.test_document_processing import make_pdf
from tests.test_llm_extractor import candidate, claim, client_for, ref
from pipeline.run_pipeline import format_local_record
from tests.test_multimodal_gemini import attachment, image
from tests.test_run_pipeline import raw_email


def _claim_properties(schema):
    return schema["$defs"]["_Claim"]["properties"]


def typed_response(*claims, evidence=None, sources=None):
    opportunity = candidate(
        "Hackathon", evidence or ref("Synthetic"), sources=sources,
    )
    opportunity["claims"] = {field: [] for field in FIELD_TYPES}
    for item in claims:
        opportunity["claims"].setdefault(item["field"], []).append({
            "value": item["value"], "evidence": item["evidence"],
        })
    return json.dumps({"opportunities": [opportunity]})


@pytest.fixture
def offline_groq(monkeypatch):
    requests = []
    response = {"status": 200, "text": '{"opportunities":[]}', "finish": "stop"}

    def handle(request):
        requests.append(request)
        if response["status"] != 200:
            return httpx.Response(response["status"], json={
                "error": {"message": "Private synthetic content", "code": response.get("code")},
            })
        return httpx.Response(200, json={
            "id": "offline-response", "object": "chat.completion", "created": 1,
            "model": groq_provider.DEFAULT_GROQ_MODEL,
            "choices": [{"index": 0, "finish_reason": response["finish"],
                         "message": {"role": "assistant", "content": response["text"]}}],
        })

    def constructor(**kwargs):
        assert kwargs["max_retries"] == 0
        kwargs.pop("http_client").close()
        return Groq(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))

    monkeypatch.setattr(groq_provider.groq, "Groq", constructor)
    monkeypatch.setenv("GROQ_API_KEY", "offline-synthetic-key")
    monkeypatch.setenv("GROQ_MODEL", groq_provider.DEFAULT_GROQ_MODEL)
    monkeypatch.setenv("GROQ_MAX_COMPLETION_TOKENS", "")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    return requests, response


def test_text_only_and_strict_schema(offline_groq, monkeypatch):
    requests, _ = offline_groq
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
    assert extract_opportunities("Synthetic notice", "Library hours changed.") == []
    request, = requests
    assert request.url.path == "/openai/v1/chat/completions"
    assert request.extensions["timeout"]["read"] == 45
    payload = json.loads(request.content)
    assert payload["model"] == "qwen/qwen3.8-27b"
    assert payload["messages"][0]["content"] == groq_provider.GROQ_PROMPT
    user_parts = payload["messages"][1]["content"]
    assert [part["type"] for part in user_parts] == ["text"]
    assert json.loads(user_parts[0]["text"])["documents"][1]["lines"] == {"1": "Library hours changed."}
    fmt = payload["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    for item in [schema, *schema["$defs"].values()]:
        assert item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"])
    assert schema["properties"]["opportunities"]["maxItems"] == 1
    assert "VisualEvidence" not in schema["$defs"]
    claim_fields = schema["$defs"]["Claims"]
    assert len(claim_fields["required"]) == 15
    assert set(claim_fields["required"]) == set(FIELD_TYPES)
    assert schema["$defs"]["TextEvidence"]["properties"]["source_id"]["enum"] == ["email_subject", "email_body"]
    types = {}
    for field, array in claim_fields["properties"].items():
        assert array["type"] == "array"
        name = array["items"]["$ref"].split("/")[-1]
        types[field] = schema["$defs"][name]["properties"]["value"]
    assert types["title"]["type"] == "string"
    assert types["team_based"]["type"] == "boolean"
    assert types["max_year"]["type"] == "integer"
    assert types["fee"]["type"] == "number"
    assert types["eligible_departments"]["items"]["type"] == "string"
    assert _Response.model_json_schema()["$defs"]["_VisualReference"]["properties"]["kind"]["const"] == "visual"


def test_invalid_provider_and_missing_key_stop_before_sdk(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "unknown")
    with pytest.raises(LLMExtractionError, match="LLM_PROVIDER"):
        extract_opportunities(None, "Synthetic")
    monkeypatch.setenv("GROQ_API_KEY", "")
    with pytest.raises(GroqExtractionError, match="GROQ_API_KEY"):
        extract_opportunities(None, "Synthetic", provider="groq")


def test_explicit_gemini_cannot_fall_back_to_groq(offline_groq):
    requests, _ = offline_groq
    client = client_for()
    assert extract_opportunities(None, "Synthetic", provider="gemini", client=client) == []
    client.models.generate_content.assert_called_once()
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_json_schema == _Response.model_json_schema()
    assert "anyOf" in _claim_properties(config.response_json_schema)["field"]
    assert requests == []


def test_poster_and_pdf_use_one_request_and_keep_source(offline_groq, tmp_path, monkeypatch):
    requests, response = offline_groq
    poster = image(tmp_path / "poster.png")
    scan = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=2)
    response["text"] = typed_response(
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("deadline", "2031-02-20", {"kind": "visual", "visual_id": "visual_3", "span": "Deadline 20 February 2031"}),
        evidence=ref("Synthetic Hackathon"),
        sources=["email_body", "attachment_1", "attachment_2"],
    )
    record, = extract_opportunities(None, "Synthetic Hackathon", attachments=[
        attachment(poster, "poster OCR"), attachment(scan, "scan OCR", "pdf"),
    ])
    request, = requests
    payload = json.loads(request.content)
    assert payload["response_format"]["json_schema"]["strict"] is True
    schema = payload["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["VisualEvidence"]["properties"]["visual_id"]["enum"] == ["visual_1", "visual_2", "visual_3"]
    parts = payload["messages"][1]["content"]
    assert [part["text"] for part in parts[1::2]] == [
        'Original attachment for source_id "attachment_1" (visual_id "visual_1")',
        'Original attachment for source_id "attachment_2", PDF page 1 (visual_id "visual_2")',
        'Original attachment for source_id "attachment_2", PDF page 2 (visual_id "visual_3")',
    ]
    assert base64.b64decode(parts[2]["image_url"]["url"].split(",", 1)[1]) == poster.read_bytes()
    assert parts[4]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert record["evidence"]["deadline"]["page_number"] == 2
    assert record["evidence"]["deadline"]["line_number"] is None
    assert not record["processing"]["requires_review"]


def test_visual_evidence_for_unsent_page_is_rejected(offline_groq, tmp_path):
    requests, response = offline_groq
    source = attachment(image(tmp_path / "poster.png"), "Poster text")
    response["text"] = typed_response(
        evidence={"kind": "visual", "visual_id": "visual_2", "span": "Synthetic"},
        sources=["attachment_1"],
    )
    with pytest.raises(GroqExtractionError, match="invalid structured output"):
        extract_opportunities("Synthetic", None, attachments=[source])
    assert len(requests) == 1


def test_empty_arrays_and_conflicts_survive_conversion(offline_groq):
    _, response = offline_groq
    body = (
        "---------- Forwarded message ---------\n"
        "From: Example\nTo: list@example.org\n\n"
        "Synthetic Hackathon\nDeadline: 2031-04-20\n"
        "Correction: Deadline is 2031-04-21\nRegistration is free."
    )
    response["text"] = typed_response(
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon", 5)),
        claim("deadline", "2031-04-20", ref("Deadline: 2031-04-20", 6)),
        claim("deadline", "2031-04-21", ref("Correction: Deadline is 2031-04-21", 7)),
        claim("fee", 0, ref("Registration is free.", 8)),
        evidence=ref("Synthetic Hackathon", 5),
    )
    record, = extract_opportunities(None, body)
    payload = format_local_record(record, {}, None)
    assert payload["schema_version"] == "1.0"
    assert payload["opportunity"]["fee"] == 0
    assert payload["opportunity"]["organizer"] is None
    assert payload["opportunity"]["deadline"] is None
    assert payload["evidence"]["title"]["line_number"] == 5
    assert payload["processing"]["requires_review"]
    conflict, = payload["processing"]["conflicts"]
    assert [(claim["value"], claim["line_number"]) for claim in conflict["candidates"]] == [
        ("2031-04-20", 6), ("2031-04-21", 7),
    ]


def test_estimate_over_quota_warns_but_still_sends_complete_input(offline_groq, caplog, monkeypatch):
    requests, _ = offline_groq
    monkeypatch.setenv("GROQ_MAX_COMPLETION_TOKENS", "2300")
    body = "Synthetic email details " * 1500
    assert extract_opportunities(None, body) == []
    request, = requests
    request_json = json.loads(request.content)
    assert request_json["max_completion_tokens"] == 2300
    content = request_json["messages"][1]["content"][0]["text"]
    assert json.loads(content)["documents"][1]["lines"]["1"] == body.rstrip()
    assert "estimate_is_advisory=true" in caplog.text


@pytest.mark.parametrize("value", ["0", "16385", "invalid"])
def test_invalid_completion_token_limit_stops_before_request(offline_groq, monkeypatch, value):
    requests, _ = offline_groq
    monkeypatch.setenv("GROQ_MAX_COMPLETION_TOKENS", value)
    with pytest.raises(GroqExtractionError, match="GROQ_MAX_COMPLETION_TOKENS"):
        extract_opportunities(None, "Synthetic")
    assert not requests


@pytest.mark.parametrize("limit", ["count", "size", "resolution"])
def test_limits_stop_before_request(offline_groq, tmp_path, monkeypatch, limit):
    requests, _ = offline_groq
    poster = image(tmp_path / "poster.png")
    attachments = [attachment(poster)]
    text = "Synthetic"
    if limit == "count":
        attachments = [attachment(make_pdf(tmp_path / "scan.pdf", selectable=False, scans=4), kind="pdf")]
    elif limit == "size":
        monkeypatch.setattr(groq_provider, "MAX_ENCODED_IMAGE_BYTES", 4)
    else:
        monkeypatch.setattr(groq_provider, "MAX_IMAGE_PIXELS", 4)
    with pytest.raises(GroqExtractionError, match="Processing is incomplete"):
        extract_opportunities(None, text, attachments=attachments)
    assert not requests


@pytest.mark.parametrize("text, finish, message", [
    ("{}", "stop", "invalid structured output"),
    (typed_response(claim("invalid_field", "value", ref("Synthetic"))), "stop", "invalid structured output"),
    (typed_response(claim("title", True, ref("Synthetic"))), "stop", "invalid structured output"),
    (typed_response(claim("max_year", [">1000"], ref("Synthetic"))), "stop", "invalid structured output"),
    (typed_response(evidence={"kind": "visual", "visual_id": "visual_1", "span": "Synthetic"}),
     "stop", "invalid structured output"),
    (typed_response(evidence=ref("Synthetic", source="not_sent")), "stop", "invalid structured output"),
    ('{"opportunities":[]}', "length", "incomplete structured response"),
    (typed_response(evidence=ref("Made-up evidence")), "stop", "does not match"),
])
def test_invalid_groq_output_fails_closed(offline_groq, text, finish, message):
    requests, response = offline_groq
    response.update(text=text, finish=finish)
    with pytest.raises(GroqExtractionError, match=message):
        extract_opportunities(None, "Synthetic")
    assert len(requests) == 1


def test_single_selected_groq_dry_run_does_not_poll_or_write(offline_groq, tmp_path, monkeypatch, capsys):
    requests, response = offline_groq
    text = "Synthetic Hackathon"
    response["text"] = typed_response(claim("title", text, ref(text)), evidence=ref(text))
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(tmp_path / "local.db"))
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "get_message", Mock(return_value=raw_email("fake-001", body=text)))
    monkeypatch.setattr(cli, "list_message_ids", Mock(side_effect=AssertionError("Unexpected inbox poll")))
    assert cli.main([
        "run", "--message-id", "fake-001", "--dry-run", "--reprocess",
        "--llm-limit", "1", "--llm-attempts", "1", "--out-dir", str(tmp_path / "output"),
    ]) == 0
    assert len(requests) == 1
    cli.get_message.assert_called_once_with(cli.get_gmail_service.return_value, "fake-001")
    cli.list_message_ids.assert_not_called()
    assert not list(tmp_path.iterdir())
    assert "would_handoff" in capsys.readouterr().out


@pytest.mark.parametrize("status, limit, expected", [(429, 3, 1), (503, 2, 2), (400, 3, 1)])
def test_cli_groq_http_failures_are_bounded_and_retryable(
    offline_groq, tmp_path, monkeypatch, capsys, status, limit, expected,
):
    requests, response = offline_groq
    response["status"] = status
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(tmp_path / "local.db"))
    monkeypatch.setattr(groq_provider.time, "sleep", Mock())
    monkeypatch.setattr(cli, "get_gmail_service", Mock())
    monkeypatch.setattr(cli, "get_message", Mock(side_effect=lambda _, mid: raw_email(mid, subject="Synthetic notice")))
    monkeypatch.setattr(cli, "list_message_ids", Mock(return_value=["fake-001", "fake-002"]))
    # Use a single email for non-quota failures to test the per-email and command caps.
    selection = [] if status == 429 else ["--message-id", "fake-001"]
    assert cli.main(["run", "--dry-run", "--llm-limit", str(limit), "--llm-attempts", "3", *selection]) == 1
    assert len(requests) == expected
    assert not is_processed("fake-001", None) and not is_processed("fake-002", None)
    assert "Private synthetic content" not in capsys.readouterr().out
    if selection:
        cli.list_message_ids.assert_not_called()
    cli.get_message.assert_called_once()

def test_groq_receives_cleaned_text_and_keeps_original_evidence_line(offline_groq):
    requests, response = offline_groq
    response["text"] = typed_response(evidence=ref("Synthetic Hackathon", 7))
    body = (
        "---------- Forwarded message ---------\n"
        "From: Organizer <organizer@example.org>\n"
        "Date: Monday, 1 June 2028\n"
        "To: group@example.org\n\n"
        "> **Synthetic Hackathon**\n"
        "> 7 CGPA required.\n"
    )
    record, = extract_opportunities("Synthetic forward", body)
    payload = json.loads(requests[0].content)
    lines = json.loads(payload["messages"][1]["content"][0]["text"])["documents"][1]["lines"]
    assert lines == {
        "2": "From: Organizer <organizer@example.org>",
        "6": "Synthetic Hackathon",
        "7": "> 7 CGPA required.",
    }
    assert record["evidence"]["type"]["line_number"] == 6
