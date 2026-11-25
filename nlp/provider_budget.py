"""Command-scoped provider budgets, rate guards, and cooldowns."""

import os
import time
from collections import deque
from collections.abc import Callable

from nlp.llm_errors import LLMExtractionError

PROVIDERS = ("gemini", "groq")


def _limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
        if value > 0:
            return value
    except ValueError:
        pass
    raise LLMExtractionError(f"{name} must be a positive integer.")


def provider_mode() -> str:
    """Read the explicit mode or use the production default."""
    return os.getenv("LLM_PROVIDER") or "auto"


class ProviderBudget:
    """Track actual attempts for each provider across all polls of a command."""

    def __init__(
        self, total: int | None = None, max_attempts: int = 1, *,
        mode: str | None = None, clock: Callable[[], float] = time.monotonic,
    ):
        self.mode = mode or provider_mode()
        if self.mode not in (*PROVIDERS, "auto"):
            raise LLMExtractionError("LLM_PROVIDER must be auto, gemini or groq.")
        self.max_attempts = max_attempts
        self._total = total
        self._clock = clock
        self._cooldown = _limit("LLM_PROVIDER_COOLDOWN_SECONDS", 60)
        active = PROVIDERS if self.mode == "auto" else (self.mode,)
        self._used = dict.fromkeys(active, 0)
        self._caps = {provider: _limit(f"{provider.upper()}_REQUEST_LIMIT", 3) for provider in active}
        self._minute = {
            provider: _limit(f"{provider.upper()}_REQUESTS_PER_MINUTE", 3 if provider == "gemini" else 30)
            for provider in active
        }
        self._day = {
            provider: _limit(f"{provider.upper()}_REQUESTS_PER_DAY", 20 if provider == "gemini" else 1000)
            for provider in active
        }
        self._until = dict.fromkeys(active, 0.0)
        self._requests: dict[str, deque[float]] = {provider: deque() for provider in active}

    @property
    def remaining(self) -> int:
        """Count unused command capacity, including providers temporarily cooling down."""
        providers = PROVIDERS if self.mode == "auto" else (self.mode,)
        capacity = sum(self._caps[provider] - self._used[provider] for provider in providers)
        return capacity if self._total is None else min(capacity, self._total - sum(self._used.values()))

    def capacity(self, provider: str) -> int:
        """Count currently usable attempts within all local limits."""
        if provider not in self._requests:
            return 0
        now = self._clock()
        requests = self._requests[provider]
        while requests and requests[0] <= now - 86400:
            requests.popleft()
        if now < self._until[provider]:
            return 0
        return max(0, min(
            self.remaining,
            self._caps[provider] - self._used[provider],
            self._day[provider] - len(requests),
            self._minute[provider] - sum(stamp > now - 60 for stamp in requests),
        ))

    def available(self, provider: str) -> bool:
        """Return whether the provider can make another request immediately."""
        return self.capacity(provider) > 0

    def consume(self, provider: str) -> None:
        """Reserve one request immediately before calling the SDK."""
        if not self.available(provider):
            raise LLMExtractionError(f"{provider} request budget or cooldown prevents this attempt.")
        self._used[provider] += 1
        self._requests[provider].append(self._clock())

    def cool_down(self, provider: str) -> None:
        """Temporarily suppress calls to an unavailable provider."""
        self._until[provider] = self._clock() + self._cooldown
