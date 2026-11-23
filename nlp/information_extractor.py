"""Extract opportunity fields from labelled text and explicit statements."""

import re
from collections.abc import Mapping
from datetime import date
from math import isfinite
from typing import Any, NamedTuple

from nlp.extraction_evidence import Evidence, evidence_at

_FIELDS = (
    "title", "description", "organizer", "deadline", "start_date", "end_date",
    "location", "registration_link", "fee", "team_based",
)
_TITLE = re.compile(r"^[ \t]*(?:event name|role|position|title)[ \t]*:[ \t]*(?P<value>[^\r\n]+)", re.I | re.M)
_ORGANIZER = re.compile(
    r"(?:^[ \t]*(?:company|organization)[ \t]*:[ \t]*|\b(?:organi[sz]ed|hosted|conducted)\s+by[ \t:]+)"
    r"(?P<value>[^\r\n.;]+(?:\.(?=\w)[^\r\n.;]*)*)", re.I | re.M,
)
_LOCATION = re.compile(
    r"^[ \t]*(?:(?:venue|location)[ \t]*:[ \t]*(?P<value>[^\r\n]+)|"
    r"mode[ \t]*:[ \t]*(?P<mode>online|offline|hybrid)\b)", re.I | re.M,
)
_MONTHS = {
    name: number for number, names in enumerate(
        ("jan january", "feb february", "mar march", "apr april", "may", "jun june",
         "jul july", "aug august", "sep sept september", "oct october", "nov november", "dec december"), 1
    ) for name in names.split()
}
_MONTH = r"(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_DATE = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{4}})?"
    rf"|{_DAY}[ \t]+{_MONTH}(?:[ \t]+\d{{4}})?"
    rf"|{_MONTH}[ \t]+{_DAY}(?:,?[ \t]+\d{{4}})?)"
)
_DEADLINE = re.compile(
    r"\b(?:registration deadline|application deadline|deadline|apply by|"
    r"last date to (?:apply|register)|register before)\b"
    rf"[ \t:]*(?:is[ \t]+|on[ \t]+)?(?:[-–][ \t]*)?(?:\r?\n[ \t]*)?(?P<date>{_DATE})(?!\w)",
    re.I,
)
_RANGE = re.compile(
    rf"(?<!\w)(?:from[ \t]+)?(?P<start>{_DATE})[ \t]*(?:to\b|through\b|[-–—])[ \t]*"
    rf"(?P<end>{_DATE})(?!\w)", re.I,
)
_SHORT_RANGE = re.compile(
    rf"(?<![\w/-])(?P<first>{_DAY})[ \t]*[-–—][ \t]*(?P<last>{_DAY})[ \t]+"
    rf"(?P<month>{_MONTH})(?:[ \t]+(?P<year>\d{{4}}))?(?!\w)", re.I,
)
_CURRENCY = r"(?:₹|INR\b|Rs\.?(?!\w))"
_AMOUNT = r"\d+(?:,\d+)*(?:\.\d{1,2})?(?![\d,]|\.\d)"
_FEE_LABEL = r"(?:(?:registration|entry|participation|event|application)[ \t]+)?fee"
_PAID = re.compile(
    rf"\b{_FEE_LABEL}\b[ \t:]*(?:is[ \t]+|of[ \t]+)?{_CURRENCY}[ \t]*(?P<value>{_AMOUNT})"
    rf"|{_CURRENCY}[ \t]*(?P<before>{_AMOUNT})[ \t]+(?:as[ \t]+)?(?:{_FEE_LABEL})\b", re.I,
)
_FREE = re.compile(
    r"\b(?:no[ \t]+(?:(?:registration|entry|participation|event|application)[ \t]+)?fee(?![ \t]+waiver)"
    r"|free[ \t]+of[ \t]+cost|(?:registration|entry|participation)[ \t]+(?:is[ \t]+)?free"
    r"|free[ \t]+(?:registration|entry|participation|hackathon))\b"
    rf"|\b{_FEE_LABEL}\b[ \t:]*(?:is[ \t]+)?free\b|^[ \t]*free[ \t.!]*$", re.I | re.M,
)
_TEAM = re.compile(
    r"\b(?P<team>teams?[ \t]+of[ \t]+(?:up[ \t]+to[ \t]+)?\d+(?:[ \t]*[-–][ \t]*\d+)?)"
    r"|\b(?P<individual>individual[ \t]+participation|solo)\b", re.I,
)
_URL = re.compile(r"(?<!\w)(?:https?://|www\.)[^\s<>\"'{}\[\]]+", re.I)
_LINK_CUE = re.compile(r"\b(?:register|registration|apply|application)\b", re.I)
_SENTENCES = re.compile(r"[^\r\n]+?(?:[.!?](?=\s|$)|(?=\r?$))", re.M)
_RELEVANT = re.compile(
    r"\b(?:hackathons?|internships?|interns?|event|competition|applications?|registration|"
    r"participat\w*|program(?:me)?|opportunity|workshop)\b", re.I,
)
_METADATA_LINE = re.compile(
    r"^\s*(?:please (?:find|see|refer)|eligible|eligibility|venue|location|mode|deadline|"
    r"registration deadline|apply by|last date|event name|role|position|title|company|organization)\b",
    re.I,
)


class _Candidate(NamedTuple):
    value: Any
    evidence: Evidence


def _candidate(text: str, match: re.Match[str], source: str, value: Any) -> _Candidate:
    return _Candidate(value, evidence_at(text, *match.span(), source))


def _resolve(candidates: list[_Candidate], field: str, fields: dict[str, Any],
             evidence: dict[str, Evidence], warnings: list[str]) -> None:
    if not candidates:
        return
    first = candidates[0]
    if any(candidate.value != first.value for candidate in candidates[1:]):
        warnings.append(f"Multiple candidates found for {field}.")
        return
    fields[field], evidence[field] = first


def _parse_date(raw: str) -> tuple[str | None, str | None]:
    raw = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", raw.casefold())
    numbers = [int(value) for value in re.findall(r"\d+", raw)]
    if not re.search(r"\b\d{4}\b", raw):
        return None, "a date has no explicit four-digit year"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            year, month, day = numbers
        elif re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", raw):
            day, month, year = numbers
            if day <= 12 and month <= 12:
                return None, "the numeric date is ambiguous"
        else:
            day, year = numbers
            month_name = re.search(r"[a-z]+", raw)
            if month_name is None:
                return None, "the date format is unsupported"
            month = _MONTHS[month_name[0]]
        return date(year, month, day).isoformat(), None
    except (ValueError, KeyError, IndexError):
        return None, "the calendar date is invalid"


def _dates(text: str, source: str, fields: dict[str, Any], evidence: dict[str, Evidence],
           warnings: list[str]) -> None:
    candidates: dict[str, list[_Candidate]] = {name: [] for name in ("deadline", "start_date", "end_date")}
    uncertain = set()

    def add(field: str, raw: str, match: re.Match[str]) -> None:
        result, error = _parse_date(raw)
        if error:
            warning = f"Could not extract {field}: {error}."
            if warning not in warnings:
                warnings.append(warning)
            uncertain.add(field)
        else:
            candidates[field].append(_candidate(text, match, source, result))

    for match in _DEADLINE.finditer(text):
        add("deadline", match["date"], match)

    for match in _RANGE.finditer(text):
        add("start_date", match["start"], match)
        add("end_date", match["end"], match)
    for match in _SHORT_RANGE.finditer(text):
        tail = f"{match['month']} {match['year'] or ''}".rstrip()
        add("start_date", f"{match['first']} {tail}", match)
        add("end_date", f"{match['last']} {tail}", match)

    for field, values in candidates.items():
        if field not in uncertain:
            _resolve(values, field, fields, evidence, warnings)


def _title(text: str, context: Mapping[str, Any], source: str) -> _Candidate | None:
    labelled = _TITLE.search(text)
    if labelled and labelled["value"].strip():
        return _candidate(text, labelled, source, labelled["value"].strip())
    subject = context.get("subject")
    if subject:
        cleaned = re.sub(r"^(?:(?:fwd?|re)\s*:\s*)+", "", subject.strip(), flags=re.I)
        cleaned = re.sub(
            r"\s*[-–—|:]\s*(?:registrations?|applications?)\s+(?:(?:are|now)\s+)*open[.!]?\s*$",
            "", cleaned, flags=re.I,
        ).strip()
        if cleaned:
            return _Candidate(cleaned, evidence_at(subject, 0, len(subject), "email subject"))
    for match in re.finditer(r"^[ \t]*(?P<line>[^\r\n]+)", text, re.M):
        line = match["line"].strip()
        if line and match.start() < 300 and not _METADATA_LINE.match(line) and not _URL.match(line):
            return _candidate(text, match, source, line)
        if line:
            break
    return None


def _description(text: str, category: str, source: str) -> _Candidate | None:
    relevant = []
    for match in _SENTENCES.finditer(text):
        value = match[0].strip()
        if not value or not re.search(r"[.!?]$", value) or _METADATA_LINE.match(value):
            continue
        if not (_RELEVANT.search(value) or re.search(rf"\b{re.escape(category)}\b", value, re.I)):
            if not relevant:
                continue
            break
        relevant.append(match)
        if len(relevant) == 2:
            break
    if not relevant:
        return None
    value = " ".join(match[0].strip() for match in relevant)[:500].rstrip()
    return _candidate(text, relevant[0], source, value)


def _registration_links(text: str, source: str) -> list[_Candidate]:
    links = []
    preferred = []
    for match in _URL.finditer(text):
        url = match[0].rstrip(".,;:!?)")
        candidate = _candidate(text, match, source, url)
        links.append(candidate)
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line_end = len(text) if line_end == -1 else line_end
        previous_start = text.rfind("\n", 0, max(0, line_start - 1)) + 1
        before_url = text[line_start:match.start()]
        previous_label = text[previous_start:max(0, line_start - 1)]
        nearby = before_url + " " + text[match.end():line_end]
        if _LINK_CUE.search(nearby) or (not before_url.strip() and _LINK_CUE.search(previous_label)):
            preferred.append(candidate)
    return preferred or links


def extract_opportunity_fields(
    text: str | None, category: str, context: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return explicit opportunity fields, evidence, and extraction warnings."""
    text = text or ""
    context = context or {}
    source = context.get("source") or "document"
    fields = dict.fromkeys(_FIELDS)
    evidence: dict[str, Evidence] = {}
    warnings: list[str] = []
    for field, candidate in (("title", _title(text, context, source)),
                             ("description", _description(text, category, source))):
        if candidate:
            fields[field], evidence[field] = candidate

    _dates(text, source, fields, evidence, warnings)
    organizers = [_candidate(text, match, source, match["value"].strip())
                  for match in _ORGANIZER.finditer(text) if match["value"].strip()]
    locations = [_candidate(text, match, source, (match["value"] or match["mode"]).strip())
                 for match in _LOCATION.finditer(text) if (match["value"] or match["mode"]).strip()]
    fees = []
    for match in _PAID.finditer(text):
        amount = float((match["value"] or match["before"]).replace(",", ""))
        if isfinite(amount):
            fees.append(_candidate(text, match, source, amount))
        else:
            warnings.append("A registration fee could not be represented as a number.")
    fees.extend(_candidate(text, match, source, 0) for match in _FREE.finditer(text))
    team = [_candidate(text, match, source, bool(match["team"])) for match in _TEAM.finditer(text)]
    for field, candidates in (
        ("organizer", organizers), ("location", locations), ("fee", fees),
        ("team_based", team), ("registration_link", _registration_links(text, source)),
    ):
        _resolve(candidates, field, fields, evidence, warnings)
    return {**fields, "evidence": evidence, "warnings": warnings}
