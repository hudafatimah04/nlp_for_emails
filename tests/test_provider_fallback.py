"""Exercise automatic provider selection with offline SDK responses."""

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import errors

from nlp import llm_extractor as llm
from nlp.llm_errors import GroqQuotaError
from nlp.provider_budget import ProviderBudget
from pipeline.run_pipeline import format_local_record
from tests.test_llm_extractor import candidate, claim, client_for, ref

BODY = "Synthetic Hackathon\nDeadline: 2030-01-10\nDeadline: 2030-01-11"


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    opportunity = candidate("Hackathon", ref("Synthetic Hackathon"),
                            claim("title", "Synthetic Hackathon", ref("Synthetic Hackathon")))
    client = client_for(opportunity)
    monkeypatch.setattr(llm.genai, "Client", Mock(return_value=nullcontext(client)))

    def groq(model, content, media, timeout, attempts, before_attempt):
        assert attempts == 1
        before_attempt()
        return json.dumps({"opportunities": [opportunity]})

    groq_mock = Mock(side_effect=groq)
    monkeypatch.setattr(llm, "request_groq", groq_mock)
    return client.models.generate_content, groq_mock, opportunity


@pytest.mark.parametrize("response", ["opportunity", "other", "conflict"])
def test_valid_gemini_results_never_fall_back(providers, response, caplog):
    gemini, groq, opportunity = providers
    if response == "conflict":
        opportunity["claims"] += [
            claim("deadline", "2030-01-10", ref("Deadline: 2030-01-10", 2)),
            claim("deadline", "2030-01-11", ref("Deadline: 2030-01-11", 3)),
        ]
    gemini.return_value.text = json.dumps({"opportunities": [] if response == "other" else [opportunity]})
    with caplog.at_level("INFO"):
        records = llm.extract_opportunities(None, BODY, message_id="fake-msg-001")
    assert bool(records) == (response != "other")
    gemini.assert_called_once()
    groq.assert_not_called()
    assert "extraction_completed message_id='fake-msg-001' provider=gemini model=" in caplog.text
    if records:
        payload = format_local_record(records[0], {}, None)
        assert payload["opportunity"]["deadline"] is None
        assert payload["processing"]["status"] == ("needs_review" if response == "conflict" else "ready")


@pytest.mark.parametrize("failure", [503, 429, 413, "timeout", "schema"])
def test_eligible_gemini_failures_use_groq_once(providers, failure, caplog):
    gemini, groq, _ = providers
    if isinstance(failure, int):
        gemini.side_effect = errors.APIError(failure, {"error": {"message": "private synthetic details"}})
    elif failure == "timeout":
        gemini.side_effect = TimeoutError()
    else:
        gemini.return_value = SimpleNamespace(text='{"opportunities": [true]}', candidates=[])
    with caplog.at_level("INFO"):
        record, = llm.extract_opportunities(None, BODY)
    assert record["opportunity"]["type"] == "Hackathon"
    gemini.assert_called_once()
    groq.assert_called_once()
    assert "provider=groq model=" in caplog.text


def test_both_fail_then_both_cooldowns_expire(providers):
    gemini, groq, _ = providers
    good_response = groq.side_effect
    gemini.side_effect = errors.APIError(503, {})
    now = [100.0]
    budget = ProviderBudget(clock=lambda: now[0])

    def reject(*args):
        args[-1]()
        raise GroqQuotaError("Groq quota reached.")

    groq.side_effect = reject
    with pytest.raises(llm.AutoExtractionError, match="Groq quota reached"):
        llm.extract_opportunities(None, BODY, budget=budget)
    with pytest.raises(llm.AutoExtractionError, match="cooldown"):
        llm.extract_opportunities(None, BODY, budget=budget)
    gemini.assert_called_once()
    groq.assert_called_once()
    now[0] += 60
    groq.side_effect = good_response
    assert llm.extract_opportunities(None, BODY, budget=budget)
    assert gemini.call_count == groq.call_count == 2


def test_provider_caps_and_rolling_request_limits(providers, monkeypatch):
    gemini, groq, _ = providers
    monkeypatch.setenv("GEMINI_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("GEMINI_REQUESTS_PER_DAY", "2")
    monkeypatch.setenv("GROQ_REQUEST_LIMIT", "1")
    now = [100.0]
    budget = ProviderBudget(clock=lambda: now[0])
    assert llm.extract_opportunities(None, BODY, budget=budget)
    assert llm.extract_opportunities(None, BODY, budget=budget)
    with pytest.raises(llm.AutoExtractionError):
        llm.extract_opportunities(None, BODY, budget=budget)
    now[0] += 60
    assert llm.extract_opportunities(None, BODY, budget=budget)
    now[0] += 60
    with pytest.raises(llm.AutoExtractionError):
        llm.extract_opportunities(None, BODY, budget=budget)
    assert gemini.call_count == 2
    groq.assert_called_once()


def test_explicit_modes_stay_independent(providers):
    gemini, groq, _ = providers
    gemini.side_effect = errors.APIError(503, {})
    with pytest.raises(llm.GeminiUnavailableError):
        llm.extract_opportunities(None, BODY, provider="gemini")
    groq.assert_not_called()
    assert llm.extract_opportunities(None, BODY, provider="groq")
    gemini.assert_called_once()
    groq.assert_called_once()


def test_fallback_reuses_same_attachment_sources_and_original_media(providers, tmp_path):
    from tests.test_multimodal_gemini import image

    gemini, groq, _ = providers
    gemini.side_effect = errors.APIError(503, {})
    poster = image(tmp_path / "poster.png")
    attachment = llm.AttachmentText("poster.png", "Synthetic poster text", "poster",
                                    visual=llm.InlineFile(Path(poster), "image"))
    assert llm.extract_opportunities(None, BODY, attachments=[attachment])
    gemini_parts = gemini.call_args.kwargs["contents"].parts
    _, groq_content, media, *_ = groq.call_args.args
    assert gemini_parts[0].text == groq_content
    assert json.loads(groq_content)["documents"][-1]["lines"]["1"] == "Synthetic poster text"
    assert gemini_parts[-1].inline_data.data == media["poster"].data


def test_invalid_evidence_does_not_trigger_second_provider(providers):
    gemini, groq, opportunity = providers
    opportunity["category_evidence"] = ref("Unsupported claim")
    gemini.return_value.text = json.dumps({"opportunities": [opportunity]})
    with pytest.raises(llm.GeminiEvidenceError):
        llm.extract_opportunities(None, BODY)
    groq.assert_not_called()


def test_removed_runtime_modules_have_no_active_references():
    import ast

    root = Path(__file__).resolve().parents[1]
    names = {"opportunity_classifier", "information_extractor", "eligibility_extractor",
             "extraction_pipeline", "extraction_evidence"}
    for name in names:
        assert not (root / "nlp" / f"{name}.py").exists()
    paths = [root / "main.py"]
    for folder in ("nlp", "pipeline", "config", "tests"):
        paths.extend((root / folder).glob("*.py"))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in {f"nlp.{name}" for name in names}
                if node.module == "nlp":
                    assert not names.intersection(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                assert not {f"nlp.{name}" for name in names}.intersection(alias.name for alias in node.names)

def test_unset_mode_defaults_to_auto(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert ProviderBudget().mode == "auto"
