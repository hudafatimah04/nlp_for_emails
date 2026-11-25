"""Shared Gemini SDK mock for CLI tests."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from nlp import llm_extractor
from tests.test_llm_extractor import candidate, claim, ref


def mock_gemini(monkeypatch):
    """Install a synthetic SDK response while retaining real parsing and verification."""
    def generate_content(*, model, contents, config):
        request = json.loads(contents if isinstance(contents, str) else contents.parts[0].text)
        subject, body, *attachments = request["documents"]
        title = subject["lines"]["1"]
        if title == "Library hours":
            candidates = []
        else:
            if not title:
                source = attachments[0]
                line, title = next((line, text) for line, text in source["lines"].items() if text.strip())
            else:
                source, line = subject, "1"
            citation = ref(title, int(line), source["source_id"])
            category = {"Synthetic festival": "Event", "Synthetic internship": "Internship", "Campus event": "Internship"}.get(title, "Hackathon")
            candidates = [candidate(
                category, citation, claim("title", title, citation),
                sources=[doc["source_id"] for doc in [subject, body, *attachments]],
            )]
        return SimpleNamespace(candidates=[], text=json.dumps({"opportunities": candidates}))

    generate = Mock(side_effect=generate_content)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr(llm_extractor.genai, "Client", Mock(
        side_effect=lambda **kwargs: nullcontext(SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
    ))
    return generate
