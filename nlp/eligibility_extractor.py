"""Extract academic eligibility criteria and their supporting evidence."""

import re
from typing import Any

from config.departments import ALL_DEPARTMENT_PHRASES, DEPARTMENT_ALIASES, MAX_ACADEMIC_YEAR
from nlp.extraction_evidence import Evidence, evidence_at

_YEAR_VALUES = {
    "first": 1, "second": 2, "third": 3, "fourth": 4,
    "i": 1, "ii": 2, "iii": 3, "iv": 4,
    "final": MAX_ACADEMIC_YEAR, "pre-final": MAX_ACADEMIC_YEAR - 1,
}
_YEAR_TOKEN = r"(?:pre[- ]final|final|first|second|third|fourth|[1-4](?:st|nd|rd|th)|iv|iii|ii|i)"
_YEAR_LIST = re.compile(
    rf"\b(?P<years>{_YEAR_TOKEN}(?:[ \t]*(?:,[ \t]*(?:and[ \t]+)?|and\b|&|to\b|[-–])[ \t]*"
    rf"{_YEAR_TOKEN})*)[ \t]+years?\b", re.IGNORECASE,
)
_YEAR_TOKEN_PATTERN = re.compile(rf"\b{_YEAR_TOKEN}\b", re.IGNORECASE)
_SEMESTER = re.compile(r"\b(?:semester|semesters|sem)\b", re.IGNORECASE)
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE)
_CGPA = re.compile(
    r"\b(?:minimum|min\.?)\s+CGPA\s*(?:of\s+|[:=]\s*)?(?P<minimum>\d+(?:\.\d+)?)"
    r"|\bCGPA\s*(?:>=|≥|at least\s+|(?:of|:)\s*)\s*(?P<label>\d+(?:\.\d+)?)"
    r"|\b(?P<suffix>\d+(?:\.\d+)?)\s+CGPA\s+(?:and|or)\s+above\b",
    re.IGNORECASE,
)
_ALL = re.compile(r"\b(?:" + "|".join(map(re.escape, ALL_DEPARTMENT_PHRASES)) + r")\b", re.IGNORECASE)
_ALIAS_TO_CODE = {
    alias.casefold(): code
    for code, aliases in DEPARTMENT_ALIASES.items() for alias in [code, *aliases]
}
_ALIASES = re.compile(
    r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(_ALIAS_TO_CODE, key=len, reverse=True))) + r")(?!\w)",
    re.IGNORECASE,
)
_DEPARTMENT_LABEL = re.compile(r"\b(?:departments?|branch(?:es)?|streams?)\b", re.IGNORECASE)
_CODE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
_NON_CODES = {"ALL", "ANY", "ONLY", "AND", "OR", "THE", "ARE", "FOR", "FROM", "CGPA", "YEAR", "YEARS",
              "ELIGIBLE", "BRANCH", "BRANCHES", "APPLY", "OPEN", "UG", "PG", "IN", "OF", "TO", "WITH",
              "STUDENTS", "DEPARTMENT", "SPECIFIC", "ENGINEERING", "MIN", "NO"}


def _years(text: str, source: str, fields: dict[str, Any], evidence: dict[str, Evidence],
           warnings: list[str]) -> None:
    bounds = {}
    for match in _YEAR_LIST.finditer(text):
        values = []
        for token in _YEAR_TOKEN_PATTERN.findall(match["years"]):
            token = token.casefold().replace(" ", "-")
            values.append(int(token[0]) if token[0].isdigit() else _YEAR_VALUES[token])
        if any(not 1 <= year <= MAX_ACADEMIC_YEAR for year in values):
            warnings.append("An academic year is outside the configured year range.")
            continue
        if "final" in match["years"].casefold():
            warning = f"Final-year wording uses the configured maximum academic year ({MAX_ACADEMIC_YEAR}); verify it."
            if warning not in warnings:
                warnings.append(warning)
        bounds.setdefault((min(values), max(values)), evidence_at(text, *match.span(), source))
    if len(bounds) == 1:
        (fields["min_year"], fields["max_year"]), match_evidence = next(iter(bounds.items()))
        evidence.update(min_year=match_evidence, max_year=match_evidence)
    elif bounds:
        warnings.append("Multiple academic year requirements were found.")
    elif _SEMESTER.search(text):
        warnings.append("Semester-only eligibility was not converted to academic years.")


def _cgpa(text: str, source: str, fields: dict[str, Any], evidence: dict[str, Evidence],
          warnings: list[str]) -> None:
    values = {}
    for match in _CGPA.finditer(text):
        if re.match(r"[ \t]*(?:%|percent\b|\.[\d])", text[match.end():], re.IGNORECASE):
            continue
        value = float(next(group for group in match.groups() if group is not None))
        if 0 <= value <= 10:
            values.setdefault(value, evidence_at(text, *match.span(), source))
        else:
            warnings.append("A CGPA requirement is outside the 0 to 10 range.")
    if len(values) == 1:
        fields["min_cgpa"], evidence["min_cgpa"] = next(iter(values.items()))
    elif values:
        warnings.append("Multiple minimum CGPA values were found.")
    if _PERCENT.search(text):
        warnings.append("Percentage requirements were not converted to CGPA.")


def _departments(text: str, source: str, fields: dict[str, Any], evidence: dict[str, Evidence],
                 warnings: list[str]) -> None:
    all_match = _ALL.search(text)
    codes = {}
    first_department = None
    for match in _ALIASES.finditer(text):
        # Common pronouns "it" and "me" need list/department context as lowercase codes.
        if match[0].casefold() in {"it", "me"} and not match[0].isupper():
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            line = text[start:len(text) if end == -1 else end]
            if not (_DEPARTMENT_LABEL.search(line) or re.search(r"[,/]", line)):
                continue
        codes.setdefault(_ALIAS_TO_CODE[match[0].casefold()], None)
        if first_department is None:
            first_department = match
    fields["eligible_departments"] = list(codes)
    if first_department:
        evidence["eligible_departments"] = evidence_at(text, *first_department.span(), source)
    if all_match and codes:
        warnings.append("Both all-department wording and specific departments were found.")
    elif all_match:
        fields["department_scope"] = "ALL"
        evidence["department_scope"] = evidence_at(text, *all_match.span(), source)
    elif codes:
        fields["department_scope"] = "SPECIFIC"
        evidence["department_scope"] = evidence["eligible_departments"]

    unknown = set()
    for line in text.splitlines():
        for label in _DEPARTMENT_LABEL.finditer(line):
            before = re.split(r"[.!?;:]", line[:label.start()])[-1][-60:]
            after = re.split(r"[.!?;]", line[label.end():])[0][:80]
            surrounding = _ALIASES.sub(" ", before + " " + after)
            unknown.update(match[0] for match in _CODE.finditer(surrounding) if match[0] not in _NON_CODES)
    if unknown:
        warnings.append(f"Unrecognized department codes: {', '.join(sorted(unknown))}.")


def extract_eligibility(text: str | None, *, source: str = "email") -> dict[str, Any]:
    """Return eligibility fields, evidence, and warnings without guessing values."""
    text = text or ""
    fields = dict.fromkeys(("min_year", "max_year", "min_cgpa", "department_scope"))
    fields["eligible_departments"] = []
    evidence: dict[str, Evidence] = {}
    warnings: list[str] = []
    _years(text, source, fields, evidence, warnings)
    _cgpa(text, source, fields, evidence, warnings)
    _departments(text, source, fields, evidence, warnings)
    return {**fields, "evidence": evidence, "warnings": warnings}
