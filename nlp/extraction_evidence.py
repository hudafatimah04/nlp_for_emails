"""Shared location and evidence handling for deterministic field extraction."""

import re
from typing import TypedDict


class Evidence(TypedDict):
    source: str
    line_number: int
    span: str


def evidence_at(text: str, start: int, end: int, source: str) -> Evidence:
    """Locate a match using one-based input lines and a bounded text span."""
    return {
        "source": source,
        "line_number": text.count("\n", 0, start) + 1,
        "span": re.sub(r"\s+", " ", text[start:end]).strip()[:80],
    }
