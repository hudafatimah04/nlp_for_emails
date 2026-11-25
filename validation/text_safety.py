"""Identify unsafe control characters without rewriting extracted text."""

from unicodedata import category


def has_invalid_controls(value: str) -> bool:
    """Allow ordinary text line breaks and tabs, and reject other controls."""
    return any(char not in "\r\n\t" and category(char) == "Cc" for char in value)
