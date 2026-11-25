"""Sanitized language-model errors shared by the CLI and providers."""


class LLMExtractionError(RuntimeError):
    """An extraction attempt did not produce a usable result."""

    provider = "gemini"
    quota = False


class GroqExtractionError(LLMExtractionError):
    """Groq could not prepare or complete an extraction."""

    provider = "groq"


class GroqQuotaError(GroqExtractionError):
    """Groq rejected the request because of a rate or quota limit."""

    quota = True
