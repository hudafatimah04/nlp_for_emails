"""Prevent tests from making unmocked LLM requests."""

import pytest

from nlp import groq_provider, llm_extractor


@pytest.fixture(autouse=True)
def block_live_llms(monkeypatch):
    def unexpected_client(*args, **kwargs):
        pytest.fail("Cloud LLM SDK must be mocked in automated tests.")

    monkeypatch.setattr(llm_extractor.genai, "Client", unexpected_client)
    monkeypatch.setattr(groq_provider.groq, "Groq", unexpected_client)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "")
