from copy import deepcopy
from datetime import date

import pytest

from nlp.llm_extractor import extract_opportunities
from tests.test_extract_fields_cli import prd_email
from tests.test_llm_extractor import candidate, claim, client_for, ref
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


def test_prd_fixture_field_values_with_unstated_optional_fields():
    message = prd_email()
    description = "Registrations are now open for National Hackathon 2026."
    years = ref("Students from the 2nd, 3rd, and 4th years with a minimum CGPA of 7.0 are eligible.", 3)
    departments = ref("Eligible departments include CSE, ISE, and ECE.", 5)
    event = ref("The event will take place from 20 December 2026 to 22 December 2026.", 9)
    client = client_for(candidate(
        "Hackathon", ref(description), claim("title", "National Hackathon 2026", ref(description)),
        claim("description", description, ref(description)),
        claim("min_year", 2, years), claim("max_year", 4, years), claim("min_cgpa", 7.0, years),
        claim("department_scope", "SPECIFIC", departments),
        claim("eligible_departments", ["CSE", "ISE", "ECE"], departments),
        claim("deadline", "2026-12-15", ref("The registration deadline is 15 December 2026.", 7)),
        claim("start_date", "2026-12-20", event), claim("end_date", "2026-12-22", event),
    ))
    record, = extract_opportunities(message["subject"], message["body_text"], client=client)
    validated = validate_opportunity(record, REFERENCE_DATE)
    assert validated["status"] == "ready"
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


def test_deadline_after_start_warns_and_dates_stay_intact(ready_record):
    ready_record["opportunity"]["deadline"] = "2027-01-16"
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["opportunity"] == ready_record["opportunity"]
    assert not validated["errors"]
    assert [issue["code"] for issue in validated["warnings"]] == ["deadline_after_start"]
    assert validated["status"] == "needs_review"


def test_past_opportunity_dates_stay_intact_and_ready(ready_record):
    validated = validate_opportunity(ready_record, date(2028, 1, 1))
    assert validated["opportunity"] == ready_record["opportunity"]
    assert validated["status"] == "ready"
    assert not validated["requires_review"]
    assert not validated["warnings"]


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
    (["XYZ"], "SPECIFIC", [], None, {"unknown_department", "scope_mismatch"}),
    (["CSE"], "ALL", ["CSE"], None, {"scope_mismatch"}),
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
    ready_record["opportunity"].update(organizer=None, deadline="2027-01-16")
    ready_record["eligibility"]["eligible_departments"].append("XYZ")
    ready_record["processing"].update(
        requires_review=True, missing_fields=["organizer"], review_reasons=["Missing fields: organizer."]
    )
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["review_reasons"] == [
        "Missing fields: organizer.", "1 warning: deadline_after_start.",
    ]
    assert {issue["code"] for issue in result["warnings"]} == {
        "deadline_after_start", "unknown_department",
    }


@pytest.mark.parametrize("category", ["Hackathon", "Internship"])
def test_unstated_optional_fields_are_listed_without_review(ready_record, category):
    ready_record["opportunity"].update(
        type=category, organizer=None, location=None, fee=None, team_based=None, description=None,
        deadline=None, registration_link=None, start_date=None, end_date=None,
    )
    ready_record["eligibility"].update(min_year=None, max_year=None, min_cgpa=None, department_scope=None,
                                      eligible_departments=[])
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["status"] == "ready"
    assert {"organizer", "fee", "team_based", "description", "min_year", "max_year",
            "min_cgpa", "department_scope"} <= set(result["missing_fields"])
    assert result["review_reasons"] == []


def test_unstated_cgpa_alone_does_not_require_review(ready_record):
    ready_record["eligibility"]["min_cgpa"] = None
    validated = validate_opportunity(ready_record, REFERENCE_DATE)
    assert validated["status"] == "ready"
    assert validated["eligibility"]["min_cgpa"] is None
    assert "min_cgpa" in validated["missing_fields"]
    assert not validated["warnings"]


@pytest.mark.parametrize(("field", "status"), [
    ("location", "ready"), ("fee", "ready"), ("deadline", "needs_review"),
    ("registration_link", "needs_review"),
])
def test_nonblocking_warnings_and_material_conflicts(ready_record, field, status):
    ready_record["opportunity"][field] = None
    conflict = {"field": field, "candidates": [{"value": "One"}, {"value": "Two"}]}
    ready_record["processing"].update(conflicts=[conflict], warnings=["Optional evidence was rejected."])
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["status"] == status
    assert result["opportunity"][field] is None
    assert result["conflicts"] == [conflict]
    assert result["warnings"][0]["code"] == "extraction_warning"


def test_control_characters_make_title_invalid(ready_record):
    ready_record["opportunity"]["title"] = "Synthetic\x01 title"
    result = validate_opportunity(ready_record, REFERENCE_DATE)
    assert result["status"] == "rejected"
    assert result["opportunity"]["title"] is None
    assert result["errors"][0]["code"] == "invalid_title"
