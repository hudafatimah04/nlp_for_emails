"""Prepare forwarded email text while preserving original source line numbers."""

import re
from collections.abc import Iterator, Sequence

_FORWARDED = re.compile(r"(?:-{2,}\s*forwarded message\s*-{2,}|begin forwarded message:)", re.IGNORECASE)
_HEADER = re.compile(r"^(from|date|sent|subject|to|cc|bcc):[ \t]*(.*)$", re.IGNORECASE)
_RECIPIENTS = {"to", "cc", "bcc"}
_ADDRESS = r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+"
_MAILBOX = rf"(?:{_ADDRESS}|(?:[\w .,&\u2019'()/-]+)?<\s*{_ADDRESS}\s*>)"
_ADDRESS_LIST = re.compile(rf"{_MAILBOX}(?:\s*,\s*{_MAILBOX})*")


_QUOTE = re.compile(r"^\s*(?:>[ \t]+|>(?=>))+")
_URL = re.compile(r"(https?://[^\s<>]+)", re.IGNORECASE)
_EMPHASIS = re.compile(r"(?<![\w*/])(?P<marker>\*{1,3})(?P<text>[^*\n]+?)(?P=marker)(?![\w*])")
_FORWARD_PREFIX = re.compile(r"^(?:(?:fwd?|re):\s*)+", re.IGNORECASE)
_AUTO_FOOTERS = {
    "sent from my iphone",
    "sent from my ipad",
    "get outlook for ios",
    "get outlook for android",
    "please consider the environment before printing this email.",
}


def clean_email_line(line: str) -> str:
    """Remove presentation-only markers without rewriting URLs or punctuation."""
    line = line.strip()
    # A leading greater-than operator before a number can express eligibility.
    if not re.match(r"^>[ \t]*[0-9]", line):
        line = _QUOTE.sub("", line).strip()
    if not line.strip("> "):
        return ""
    return "".join(
        part if index % 2 else _EMPHASIS.sub(r"\g<text>", part)
        for index, part in enumerate(_URL.split(line))
    )


def _forwarded_headers(
    lines: Sequence[str],
) -> Iterator[tuple[int, list[tuple[str, int, int, str]]]]:
    """Yield recognized separators and their field spans as zero-based indexes."""
    for marker, line in enumerate(lines):
        if not _FORWARDED.fullmatch(line):
            continue
        first = marker + 1
        while first < len(lines) and not lines[first]:
            first += 1
        if first >= len(lines) or not _HEADER.match(lines[first]):
            continue
        end = first
        while end < len(lines) and lines[end] and not _FORWARDED.fullmatch(lines[end]):
            end += 1
        headers = [(index, match) for index in range(first, end) if (match := _HEADER.match(lines[index]))]
        if not any(match[1].lower() in {"from", "subject", "date", "sent"} for _, match in headers):
            continue
        fields = []
        for position, (index, match) in enumerate(headers):
            stop = headers[position + 1][0] if position + 1 < len(headers) else end
            value = " ".join((match[2].strip(), *lines[index + 1:stop])).strip()
            fields.append((match[1].lower(), index, stop, value))
        yield marker, fields


def _is_recipient_list(value: str) -> bool:
    # Joining Gmail's wrapped lines may leave whitespace just inside angle brackets.
    return _ADDRESS_LIST.fullmatch(value) is not None


def forwarded_recipient_lines(lines: Sequence[str]) -> set[int]:
    """Return original one-based recipient lines safe to omit from the prompt."""
    return {
        number
        for _, fields in _forwarded_headers([clean_email_line(line) for line in lines])
        for name, index, stop, value in fields
        if name in _RECIPIENTS and _is_recipient_list(value)
        for number in range(index + 1, stop + 1)
    }


def clean_email_body(lines: Sequence[str], subject: str | None) -> tuple[list[str], set[int]]:
    """Return a line-aligned prompt view and excluded one-based routing lines."""
    cleaned = [clean_email_line(line) for line in lines]
    omitted: set[int] = set()
    seen_subjects = {_subject_key(subject or "")}
    for marker, fields in _forwarded_headers(cleaned):
        omitted.add(marker + 1)
        for name, index, stop, value in fields:
            if name in _RECIPIENTS and _is_recipient_list(value):
                omitted.update(range(index + 1, stop + 1))
            elif name in {"date", "sent"}:
                # Only drop a recognizable single-line header. A continuation
                # might be a faculty comment when the forward lacks a blank line.
                if re.search(r"\b[0-9]{4}\b", _HEADER.match(cleaned[index])[2]):
                    omitted.add(index + 1)
            elif name == "subject":
                key = _subject_key(value)
                if key and key in seen_subjects:
                    omitted.update(range(index + 1, stop + 1))
                seen_subjects.add(key)
    for number, line in enumerate(cleaned, 1):
        if line.casefold() in _AUTO_FOOTERS or line == "--":
            omitted.add(number)
    return cleaned, omitted


def _subject_key(subject: str) -> str:
    """Compare forwarding subjects without changing meaningful subject text."""
    return " ".join(_FORWARD_PREFIX.sub("", subject.strip()).casefold().split())
