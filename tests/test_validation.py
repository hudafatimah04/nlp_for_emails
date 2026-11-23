from copy import deepcopy
from datetime import date

import pytest

from tests.test_extraction import build, prd_email
from validation.data_validator import validate_opportunity


REFERENCE_DATE = date(2026, 12, 1)


@pytest.fixture
def ready_record():
    return {
        "opportunity": {
            "type": "Hackathon",
            "title": "Example Campus Hackathon",
            "description": "Synthetic event description.",
            "organizer": "Example Student Club",
            "deadline": "2027-01-10",
            "start_date": "2027-01-15",
            "end_date": "2027-01-16",
            "location": "Example venue",
            "registration_link": "https://example.invalid/register",
            "fee": 0,
            "team_based": False,
        },
        "eligibility": {
            "min_year": 1,
            "max_year": 4,
            "min_cgpa": 7.0,
            "department_scope": "SPECIFIC",
            "eligible_departments": ["ECE", "CSE"],
        },
        "source": {},
        "processing": {"classification": "Hackathon", "requires_review": False},
        "evidence": {},
    }


def test_prd_fixture_field_values_and_review():
    record, = build(prd_email(), [])
    validated = validate_opportunity(record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert not validated["errors"]
    assert validated["opportunity"] == {
        "title": "National Hackathon 2026",
        "type": "Hackathon",
        "description": "Registrations are now open for National Hackathon 2026.",
        "organizer": None,
        "deadline": "2026-12-15",
        "start_date": "2026-12-20",
        "end_date": "2026-12-22",
        "location": None,
        "registration_link": None,
        "fee": None,
        "team_based": None,
    }
    assert validated["eligibility"] == {
        "min_year": 2, "max_year": 4, "min_cgpa": 7.0,
        "department_scope": "SPECIFIC", "eligible_departments": ["CSE", "ISE", "ECE"],
    }


def test_ready_values_remain_unchanged(ready_record):
    original = deepcopy(ready_record)
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "ready"
    assert not validated["requires_review"]
    assert validated["warnings"] == validated["errors"] == []
    assert validated["opportunity"] == original["opportunity"]
    assert validated["eligibility"] == original["eligibility"]
    assert ready_record == original


def test_reversed_event_dates_clear_both(ready_record):
    ready_record["opportunity"]["end_date"] = "2027-01-14"
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["opportunity"]["start_date"] is None
    assert validated["opportunity"]["end_date"] is None
    assert validated["opportunity"]["deadline"] == "2027-01-10"
    assert [issue["code"] for issue in validated["errors"]] == ["end_before_start"]
    assert validated["status"] == "needs_review"


@pytest.mark.parametrize(("deadline", "code"), [
    ("2027-01-16", "deadline_after_start"),
    ("2026-11-30", "deadline_passed"),
])
def test_suspicious_deadlines_are_warnings_and_dates_stay_intact(ready_record, deadline, code):
    ready_record["opportunity"]["deadline"] = deadline
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["opportunity"] == ready_record["opportunity"]
    assert not validated["errors"]
    assert [issue["code"] for issue in validated["warnings"]] == [code]
    assert validated["status"] == "needs_review"


@pytest.mark.parametrize("invalid", ["2026-02-30", "20260101", "2026-1-01", "2026-W01-1", " 2026-12-01"])
def test_invalid_dates_clear_without_normalization(ready_record, invalid):
    ready_record["opportunity"]["deadline"] = invalid
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert validated["opportunity"]["deadline"] is None
    assert [(issue["field"], issue["code"]) for issue in validated["errors"]] == [("deadline", "invalid_date")]


@pytest.mark.parametrize("invalid", [11, -1, True, "7.5", float("inf"), float("nan")])
def test_invalid_cgpa_alone_does_not_reject(ready_record, invalid):
    ready_record["eligibility"]["min_cgpa"] = invalid
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert validated["eligibility"]["min_cgpa"] is None
    assert [issue["code"] for issue in validated["errors"]] == ["invalid_cgpa"]


@pytest.mark.parametrize(("minimum", "maximum", "expected"), [
    (3, 2, (None, None)), (1, 5, (1, None)), (True, 4, (None, 4)), (2.0, 4, (None, 4)),
])
def test_invalid_years_clear_only_unreliable_bounds(ready_record, minimum, maximum, expected):
    ready_record["eligibility"].update(min_year=minimum, max_year=maximum)
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    eligibility = validated["eligibility"]
    assert (eligibility["min_year"], eligibility["max_year"]) == expected
    assert [issue["code"] for issue in validated["errors"]] == ["invalid_year_range"]


@pytest.mark.parametrize(("codes", "scope", "expected_codes", "expected_scope", "expected_issues"), [
    (["CSE", "XYZ"], "SPECIFIC", ["CSE"], "SPECIFIC", {"unknown_department"}),
    (["XYZ"], "SPECIFIC", [], None, {"unknown_department", "scope_mismatch", "missing_recommended"}),
    (["CSE"], "ALL", ["CSE"], None, {"scope_mismatch", "missing_recommended"}),
    ([], "ALL", [], "ALL", set()),
])
def test_department_codes_and_scope(ready_record, codes, scope, expected_codes, expected_scope, expected_issues):
    ready_record["eligibility"].update(eligible_departments=codes, department_scope=scope)
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["eligibility"]["eligible_departments"] == expected_codes
    assert validated["eligibility"]["department_scope"] == expected_scope
    assert {issue["code"] for issue in validated["warnings"]} == expected_issues
    assert not validated["errors"]


@pytest.mark.parametrize(("field", "invalid", "code"), [
    ("registration_link", "ftp://x", "invalid_url"),
    ("registration_link", "not a url", "invalid_url"),
    ("registration_link", "https:///no-host", "invalid_url"),
    ("fee", -1, "invalid_fee"),
    ("fee", False, "invalid_fee"),
    ("team_based", 0, "invalid_team_based"),
    ("team_based", "true", "invalid_team_based"),
])
def test_invalid_optional_values_are_cleared(ready_record, field, invalid, code):
    ready_record["opportunity"][field] = invalid
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert validated["opportunity"][field] is None
    assert [issue["code"] for issue in validated["errors"]] == [code]


@pytest.mark.parametrize(("field", "value", "code"), [
    ("title", None, "missing_required"), ("title", "  ", "invalid_title"),
    ("type", None, "missing_required"), ("type", "Other", "invalid_type"),
])
def test_required_field_failures_are_rejected(ready_record, field, value, code):
    ready_record["opportunity"][field] = value
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "rejected"
    assert validated["opportunity"][field] is None
    assert [issue["code"] for issue in validated["errors"]] == [code]


def test_extraction_review_details_survive(ready_record):
    conflict = {"field": "location", "candidates": [{"value": "Example venue", "source": "email"}]}
    ready_record["processing"].update(
        requires_review=True,
        review_reasons=["Manual review requested during extraction."],
        conflicts=[conflict],
        warnings=["Synthetic extraction warning."],
    )
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert validated["requires_review"]
    assert validated["conflicts"] == [conflict]
    assert ready_record["processing"]["review_reasons"][0] in validated["review_reasons"]
    assert validated["warnings"][0]["message"] == "Synthetic extraction warning."
    assert set(validated["warnings"][0]) == {"field", "code", "message"}


def test_extraction_review_flag_alone_prevents_ready(ready_record):
    ready_record["processing"]["requires_review"] = True
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "needs_review"
    assert validated["requires_review"]
    assert not validated["errors"] and not validated["warnings"]


def test_warning_reasons_name_the_cause_without_repeating_missing_fields(ready_record):
    ready_record["opportunity"].update(organizer=None, deadline="2026-11-30")
    ready_record["eligibility"]["eligible_departments"].append("XYZ")
    ready_record["processing"].update(
        requires_review=True, missing_fields=["organizer"], review_reasons=["Missing fields: organizer."]
    )
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["review_reasons"] == [
        "Missing fields: organizer.", "2 warnings: deadline_passed, unknown_department.",
    ]
    assert {issue["code"] for issue in result["warnings"]} == {
        "deadline_passed", "unknown_department", "missing_recommended",
    }


def test_missing_recommended_warning_reason_when_no_explanation_exists(ready_record):
    ready_record["opportunity"].update(organizer=None, location=None)
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["review_reasons"] == ["2 warnings: missing recommended fields."]
