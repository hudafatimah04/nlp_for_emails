"""Check multimodal requests and evidence without making external calls."""

import base64
import json
from unittest.mock import Mock

import httpx2
import pymupdf
import pytest
from google.genai import Client, types
from PIL import Image

import main as cli
from nlp import inline_media
from nlp.llm_extractor import AttachmentText, GeminiEvidenceError, GeminiExtractionError, GeminiInputError, extract_opportunities
from pipeline.run_pipeline import format_local_record
from tests.test_document_processing import make_pdf
from tests.test_llm_extractor import candidate, claim, client_for, ref


def image(path):
    Image.new("RGB", (10, 10), "white").save(path)
    return path


def visual(source_id, span, page=None):
    return {"kind": "visual", "source_id": source_id, "span": span, "page_number": page}


def attachment(path, text=None, kind="image"):
    return AttachmentText(path.name, text, visual=inline_media.InlineFile(path, kind))


def test_mixed_media_in_one_serialized_sdk_request(tmp_path, monkeypatch):
    poster = image(tmp_path / "poster.jpeg")
    second = image(tmp_path / "details.png")
    pdf = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1)
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "75")
    received = []

    def reply(request):
        received.append((json.loads(request.content), request.headers["x-server-timeout"]))
        return httpx2.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": '{"opportunities": []}'}]}, "finishReason": "STOP",
        }]})

    transport = httpx2.MockTransport(reply)
    with Client(api_key="offline-stub", http_options=types.HttpOptions(client_args={"transport": transport})) as client:
        assert extract_opportunities("Poster email", "Complete body.\nSecond line.", attachments=[
            attachment(poster, "Poster OCR"), attachment(second, "Details OCR"), attachment(pdf, "PDF OCR", "pdf"),
        ], client=client) == []

    (payload, timeout), = received
    assert timeout == "75"
    parts = payload["contents"][0]["parts"]
    from_email = json.loads(parts[0]["text"])
    assert from_email["documents"][0]["lines"] == {"1": "Poster email"}
    assert from_email["documents"][1]["lines"] == {"1": "Complete body.", "2": "Second line."}
    assert [document["lines"]["1"] for document in from_email["documents"][2:]] == ["Poster OCR", "Details OCR", "PDF OCR"]
    assert [(part["inlineData"]["mime_type"], base64.urlsafe_b64decode(part["inlineData"]["data"])) for part in parts[2::2]] == [
        ("image/jpeg", poster.read_bytes()), ("image/png", second.read_bytes()), ("application/pdf", pdf.read_bytes()),
    ]
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert payload["generationConfig"]["responseJsonSchema"]
    assert "transcription mistake" in payload["systemInstruction"]["parts"][0]["text"]


def test_visual_value_can_correct_ocr_without_faking_line_evidence(tmp_path):
    poster = attachment(image(tmp_path / "poster.jpeg"), "Round O1 on 2Oth Sept")
    record, = extract_opportunities("Synthetic Hackathon", None, attachments=[poster], client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon", source="email_subject"),
        claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon", source="email_subject")),
        claim("start_date", "2030-09-20", visual("attachment_1", "Round 01 on 20th Sept 2030")),
        sources=["email_subject", "attachment_1"],
    )))
    assert record["opportunity"]["start_date"] == "2030-09-20"
    assert record["processing"]["conflicts"] == []
    assert not record["processing"]["requires_review"]
    evidence = record["evidence"]["start_date"]
    assert evidence["kind"] == "visual" and evidence["verified"] is False and evidence["line_number"] is None
    payload = format_local_record(record, {}, None)
    assert payload["processing"]["status"] == "ready"
    assert payload["schema_version"] == "1.0"
    assert payload["evidence"]["start_date"] == {"source": "poster.jpeg", "line_number": None}


def test_distinct_email_and_pdf_claims_preserve_a_real_conflict(tmp_path):
    pdf = attachment(make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1), None, "pdf")
    record, = extract_opportunities(None, "Synthetic Hackathon\nDeadline: 2030-09-21", attachments=[pdf],
                                    client=client_for(candidate(
        "Hackathon", ref("Synthetic Hackathon"), claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")),
        claim("deadline", "2030-09-21", ref("Deadline: 2030-09-21", 2)),
        claim("deadline", "2030-09-20", visual("attachment_1", "Deadline: 2030-09-20", 1)),
        sources=["email_body", "attachment_1"],
    )))
    assert record["opportunity"]["deadline"] is None
    conflict, = record["processing"]["conflicts"]
    assert conflict["field"] == "deadline"
    assert [item["value"] for item in conflict["candidates"]] == ["2030-09-21", "2030-09-20"]
    assert conflict["candidates"][1]["page_number"] == 1
    payload = format_local_record(record, {}, None)
    projected = payload["processing"]["conflicts"][0]["candidates"][1]
    assert projected["line_number"] is None and "page_number" not in projected


@pytest.mark.parametrize(("has_original", "page"), [(False, None), (True, None), (True, 2)])
def test_invalid_visual_reference_is_rejected(tmp_path, has_original, page):
    path = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1)
    item = attachment(path, kind="pdf") if has_original else AttachmentText("scan.pdf", "Local text")
    client = client_for(candidate("Hackathon", visual("attachment_1", "Claim", page), sources=["attachment_1"]))
    with pytest.raises(GeminiEvidenceError, match="missing original or invalid page"):
        extract_opportunities(None, None, attachments=[item], client=client)


@pytest.mark.parametrize(("limit", "message"), [
    ("file", "inline file limit"), ("pages", "page inline limit"),
    ("request", "Encoded email and attachments"), ("format", "unsupported image format"),
])
def test_inline_limits_block_request(tmp_path, monkeypatch, limit, message):
    path = image(tmp_path / "poster.jpeg")
    item = attachment(path)
    if limit == "file":
        monkeypatch.setattr(inline_media, "MAX_FILE_BYTES", 8)
    elif limit == "pages":
        path = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=2)
        item = attachment(path, kind="pdf")
        monkeypatch.setattr(inline_media, "MAX_PDF_PAGES", 1)
    elif limit == "request":
        monkeypatch.setattr("nlp.llm_extractor.MAX_REQUEST_BYTES", 5000)
    else:
        path.write_bytes(b"not an image")
    client = client_for()
    with pytest.raises(GeminiInputError, match=message):
        extract_opportunities(None, "Synthetic", attachments=[item], client=client)
    client.models.generate_content.assert_not_called()


def test_sdk_retries_cannot_bypass_command_budget(caplog):
    received = Mock(return_value=httpx2.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}}))
    options = types.HttpOptions(
        retry_options=types.HttpRetryOptions(attempts=3),
        client_args={"transport": httpx2.MockTransport(received)},
    )
    budget = cli.ProviderBudget(1, 1)
    with Client(api_key="offline-stub", http_options=options) as client:
        with pytest.raises(GeminiExtractionError, match="HTTP 503"):
            extract_opportunities(None, "Private synthetic body", client=client, budget=budget)
    received.assert_called_once()
    assert budget.remaining == 0
    assert "status=HTTP 503" in caplog.text
    assert "duration_ms=" in caplog.text and "request_bytes_estimate=" in caplog.text
    assert "Private synthetic body" not in caplog.text


def test_pipeline_selects_original_image_and_scan_and_keeps_text_pdf_local(tmp_path, monkeypatch):
    poster = image(tmp_path / "poster.jpeg")
    scan = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1)
    letter = make_pdf(tmp_path / "letter.pdf")
    files = [(poster, "image/jpeg"), (scan, "application/pdf"), (letter, "application/pdf")]
    monkeypatch.setattr("document_processing.document_extractor.ocr_pdf_pages", Mock(
        return_value={"pages": [{"page_number": 1, "text": "Scan OCR"}]},
    ))
    monkeypatch.setattr("document_processing.document_extractor.ocr_image", Mock(
        return_value={"text": "Poster OCR", "status": "success"},
    ))
    monkeypatch.setattr(cli, "download_attachment", lambda *args: {
        **args[-1], "status": "downloaded", "storage_path": str(tmp_path / args[-1]["filename"]),
    })
    message = {"attachments": [{"filename": path.name, "file_type": kind} for path, kind in files]}
    attachments, _, incomplete = cli._attachment_inputs(Mock(), "fake-msg-001", message)
    assert [item.visual.path if item.visual else None for item in attachments] == [poster, scan, None]
    assert [item.text for item in attachments[:2]] == ["Poster OCR", "Scan OCR"]
    assert not incomplete


def test_layout_heavy_pdf_with_selectable_text_keeps_original(tmp_path):
    from document_processing.document_extractor import extract_document

    path = tmp_path / "layout.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        page.insert_text((10, 20), "Synthetic selectable paragraph with enough characters.")
        page.insert_image(pymupdf.Rect(10, 30, 190, 190), filename=str(image(tmp_path / "diagram.png")))
        document.save(path)
    extraction = extract_document(path, "application/pdf")
    assert extraction["method"] == "pdf_text"
    assert extraction["inline_pdf"] is True
