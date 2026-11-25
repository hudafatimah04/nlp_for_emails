import base64
import json
import shutil
from pathlib import Path
from unittest.mock import Mock
from zipfile import ZipFile

import pymupdf
import pytest
import pytesseract
from docx import Document
from googleapiclient.errors import HttpError
from httplib2 import Response

import main as cli
from config import settings
from document_processing.docx_extractor import extract_docx_text
from document_processing.document_extractor import extract_document
from document_processing.ocr_processor import ocr_pdf_pages
from document_processing.pdf_extractor import extract_pdf_text
from gmail_integration import email_reader
from gmail_integration.attachment_downloader import download_attachment
from gmail_integration.email_parser import parse_message

SELECTABLE = "Selectable text appears on the first document page."
SCANNED = "Campus Opportunities"


def make_pdf(path: Path, *, selectable: bool = True, scans: int = 0) -> Path:
    with pymupdf.open() as document:
        if selectable:
            document.new_page().insert_text((50, 80), SELECTABLE)
        for _ in range(scans):
            with pymupdf.open() as image_source:
                page = image_source.new_page(width=380, height=120)
                page.insert_text((30, 70), SCANNED, fontsize=26)
                pixmap = page.get_pixmap(dpi=180)
                target = document.new_page(width=380, height=120)
                target.insert_image(target.rect, pixmap=pixmap)
        document.save(path)
    return path


def test_selectable_pdf(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "text.pdf")
    ocr = Mock()
    monkeypatch.setattr("document_processing.document_extractor.ocr_pdf_pages", ocr)
    pdf = extract_pdf_text(path)
    assert pdf["pages"] == [{
        "page_number": 1, "text": SELECTABLE, "has_text": True,
        "char_count": len(SELECTABLE.replace(" ", "")),
    }]
    assert extract_document(path, "application/pdf") == {
        "text": SELECTABLE, "method": "pdf_text", "pages_ocr_count": 0,
        "status": "success", "warnings": [],
    }
    ocr.assert_not_called()


def test_image_only_pdf_detection(tmp_path):
    path = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1)
    assert extract_pdf_text(path)["pages"] == [
        {"page_number": 1, "text": None, "char_count": 0, "has_text": False}
    ]


@pytest.mark.parametrize(("length", "has_text"), [(29, False), (30, True)])
def test_pdf_threshold_excludes_whitespace(tmp_path, length, has_text):
    path = tmp_path / "threshold.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 80), " ".join("a" * length))
        document.save(path)
    assert extract_pdf_text(path)["pages"][0]["has_text"] is has_text


def test_docx_paragraphs_and_tables(tmp_path):
    path = tmp_path / "table.docx"
    document = Document()
    document.add_paragraph("Before")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Role"
    table.cell(0, 1).text = "Department"
    table.cell(1, 0).text = "Intern"
    table.cell(1, 1).text = "CSE"
    document.add_paragraph("After")
    document.save(path)
    text = "Before\nRole | Department\nIntern | CSE\nAfter"
    assert extract_docx_text(path) == {"text": text, "status": "success"}
    assert extract_document(path, ".docx")["method"] == "docx"


def test_corrupted_files(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"invalid")
    pdf = extract_pdf_text(path)
    assert pdf["status"] == "failed" and pdf["error"]
    assert extract_document(path, "pdf")["status"] == "failed"
    assert extract_document(path, "docx")["status"] == "failed"
    assert extract_document(path, "application/zip")["status"] == "failed"


def test_password_protected_pdf(tmp_path):
    path = tmp_path / "locked.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="fake-pass")
    result = extract_document(path, "pdf")
    assert result["status"] == "failed"
    assert "password-protected" in result["warnings"][0]


@pytest.mark.parametrize(("texts", "status", "method", "expected_count"), [
    (["OCR page two", "OCR page three"], "success", "mixed", 2),
    (["OCR page two", None], "partial", "mixed", 1),
    ([None, None], "partial", "mixed", 0),
])
def test_mixed_pdf(tmp_path, monkeypatch, texts, status, method, expected_count):
    path = make_pdf(tmp_path / "mixed.pdf", scans=2)
    ocr = Mock(return_value={"pages": [
        {"page_number": number, "text": text} for number, text in enumerate(texts, 2)
    ]})
    monkeypatch.setattr("document_processing.document_extractor.ocr_pdf_pages", ocr)
    result = extract_document(path, "pdf")
    ocr.assert_called_once_with(path, [2, 3])
    assert result["status"] == status
    assert result["method"] == method
    assert result["pages_ocr_count"] == expected_count
    assert result["text"] == "\n\n".join([SELECTABLE, *(text for text in texts if text)])
    assert bool(result["warnings"]) is (status == "partial")


def test_ocr_failure_is_reported(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1)
    monkeypatch.setattr(settings, "TESSERACT_CMD", "missing-tesseract")
    run = Mock(side_effect=pytesseract.TesseractNotFoundError())
    monkeypatch.setattr(pytesseract, "image_to_string", run)
    result = extract_document(path, "pdf")
    assert result["status"] == "failed"
    assert result["method"] == "ocr"
    assert "TESSERACT_CMD" in result["warnings"][0]
    assert run.call_args.args[0].info["dpi"] == (425, 425)
    assert run.call_args.args[0].mode == "L"


def test_ocr_continues_after_page_failure(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "two.pdf", selectable=False, scans=2)
    monkeypatch.setattr(pytesseract, "image_to_string", Mock(side_effect=[RuntimeError(), "Second page"]))
    result = ocr_pdf_pages(path, [1, 2])
    assert result["status"] == "partial"
    assert result["pages"][0]["error"]
    assert result["pages"][1]["text"] == "Second page"


def test_real_tesseract_on_image_only_pdf(tmp_path, monkeypatch):
    installed_on_windows = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    executable = settings.TESSERACT_CMD or shutil.which("tesseract")
    if not executable and installed_on_windows.is_file():
        executable = str(installed_on_windows)
    if not executable or not shutil.which(executable):
        pytest.skip("Tesseract executable is unavailable for the real OCR test.")
    monkeypatch.setattr(settings, "TESSERACT_CMD", executable)
    result = extract_document(make_pdf(tmp_path / "scan.pdf", selectable=False, scans=1), "pdf")
    assert result["status"] == "success", result["warnings"]
    assert result["method"] == "ocr"
    assert result["pages_ocr_count"] == 1
    assert SCANNED in result["text"]


def test_download_sanitizes_filename_and_preserves_duplicates(tmp_path, monkeypatch):
    service = Mock()
    get = service.users.return_value.messages.return_value.attachments.return_value.get
    request = get.return_value
    request.execute.side_effect = [
        HttpError(Response({"status": "503"}), b"{}"), {"data": "-_8"}, {"data": "eQ"}
    ]
    sleep = Mock()
    monkeypatch.setattr(email_reader, "sleep", sleep)
    monkeypatch.setattr(settings, "ATTACHMENT_DIR", str(tmp_path))
    meta = {
        "filename": "../bad\\x?.pdf", "mime_type": "application/pdf",
        "gmail_attachment_id": "part", "is_supported": True,
    }
    first = download_attachment(service, "message", meta)
    second = download_attachment(service, "message", meta)
    first_path, second_path = Path(first["storage_path"]), Path(second["storage_path"])
    assert first["status"] == second["status"] == "downloaded"
    assert first["filename"] == meta["filename"]
    assert first["size"] == 2
    assert first_path.parent == second_path.parent == tmp_path / "message"
    assert not any(character in first_path.name for character in "/\\?")
    assert ".." not in first_path.name
    assert second_path.name == first_path.stem + "_1.pdf"
    assert first_path.read_bytes() == b"\xfb\xff"
    assert second_path.read_bytes() == b"y"
    sleep.assert_called_once_with(1)
    assert get.call_args.kwargs == {"userId": "me", "messageId": "message", "id": "part"}


def test_unsupported_and_invalid_message_path(tmp_path):
    service = Mock()
    assert download_attachment(service, "message", {
        "filename": "archive.zip", "is_supported": False,
    }, tmp_path)["status"] == "unsupported"
    assert download_attachment(service, "../escape", {"is_supported": True}, tmp_path)["status"] == "failed"
    service.users.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("show_text", [False, True])
def test_extract_cli_with_inline_pdf_and_unsupported_attachment(tmp_path, monkeypatch, capsys, show_text):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_textbox((20, 20, 550, 500), (SELECTABLE + " ") * 8)
        document.save(source)
    raw = {"payload": {
        "headers": [{"name": "Subject", "value": "Test subject"}],
        "parts": [
            {"filename": "test.pdf", "mimeType": "application/octet-stream", "body": {
                "data": base64.urlsafe_b64encode(source.read_bytes()).decode(),
            }},
            {"filename": "notes.txt", "mimeType": "text/plain", "body": {}},
        ],
    }}
    assert "data" not in parse_message(raw)["attachments"][0]
    service = Mock()
    monkeypatch.setattr(cli, "get_gmail_service", Mock(return_value=service))
    list_ids = Mock(return_value=["message"])
    monkeypatch.setattr(cli, "list_message_ids", list_ids)
    monkeypatch.setattr(cli, "get_message", Mock(return_value=raw))
    monkeypatch.setattr(settings, "ALLOWED_SENDERS", ["allowed@example.com"])
    monkeypatch.setattr(settings, "ATTACHMENT_DIR", str(tmp_path))
    args = ["extract", "--max", "3"]
    if show_text:
        args.append("--show-text")
    assert cli.main(args) == 0
    list_ids.assert_called_once_with(service, ["allowed@example.com"], 3)
    service.users.assert_not_called()
    supported, unsupported = map(json.loads, capsys.readouterr().out.splitlines())
    assert supported["subject"] == "Test subject"
    assert supported["filename"] == "test.pdf"
    assert supported["method"] == "pdf_text"
    assert supported["status"] == "success"
    assert supported["extracted_character_count"] > 200
    assert ("text_preview" in supported) is show_text
    if show_text:
        assert len(supported["text_preview"]) == 200
    assert unsupported["status"] == "unsupported"


@pytest.mark.parametrize("extension", ["jpeg", "png"])
def test_tesseract_reads_an_image_attachment(tmp_path, monkeypatch, extension):
    bundled_path = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    executable = settings.TESSERACT_CMD or shutil.which("tesseract")
    if not executable and bundled_path.is_file():
        executable = str(bundled_path)
    if not executable or not shutil.which(executable):
        pytest.skip("Tesseract executable is unavailable for the image OCR test.")
    monkeypatch.setattr(settings, "TESSERACT_CMD", executable)
    image = tmp_path / f"poster.{extension}"
    with pymupdf.open() as document:
        page = document.new_page(width=380, height=120)
        page.insert_text((30, 70), SCANNED, fontsize=26)
        page.get_pixmap(dpi=220).save(image)
    result = extract_document(image, f"image/{extension}")
    assert result["status"] == "success"
    assert result["method"] == "ocr"
    assert SCANNED in result["text"]
    assert result["pages_ocr_count"] == 1


def test_zip_combines_supported_members(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf")
    docx = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("Registration Deadline: 20 December 2027")
    doc.save(docx)
    archive = tmp_path / "details.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.write(pdf, "poster/flyer.pdf")
        zip_file.write(docx, "details.docx")
        zip_file.writestr("program.exe", b"not a document")
    result = extract_document(archive, "application/zip")
    assert result["status"] == "success"
    assert SELECTABLE in result["text"]
    assert "Registration Deadline: 20 December 2027" in result["text"]
    assert not (tmp_path / "poster").exists()


def test_zip_skips_unsafe_and_nested_members(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("../escape.pdf", b"not a document")
        zip_file.writestr("nested.zip", b"not opened")
    result = extract_document(archive, "application/zip")
    assert result["status"] == "failed"
    assert result["text"] is None
    assert any("unsafe archive member" in warning for warning in result["warnings"])
