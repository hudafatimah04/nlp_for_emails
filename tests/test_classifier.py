from pathlib import Path

import pytest

from nlp.opportunity_classifier import classify_opportunity

FIXTURES = Path(__file__).resolve().parents[1] / "sample_emails"


@pytest.mark.parametrize(("fixture", "expected", "review"), [
    ("hackathon", "Hackathon", False),
    ("internship", "Internship", False),
    ("library", "Other", False),
    ("research_email", "Other", True),
    ("ocr", "Internship", False),
    ("mixed", "Internship", True),
])
def test_classify_synthetic_emails(fixture, expected, review):
    subject, body = (FIXTURES / f"{fixture}.txt").read_text(encoding="utf-8").split("\n\n", 1)
    attachments = []
    if fixture == "research_email":
        attachments.append((FIXTURES / "research_report.txt").read_text(encoding="utf-8"))
    result = classify_opportunity(subject, body, attachments)
    assert result["classification"] == expected
    assert result["requires_review"] is review
    assert bool(result["review_reasons"]) is review
    assert result["reasons"]
    assert 0 <= result["confidence"] <= 1
    if fixture == "ocr":
        assert {match["term"] for match in result["matched_terms"]} >= {"summer internship", "apply by"}
    if fixture == "research_email":
        assert result["confidence"] < 1


def test_whole_words_and_missing_content():
    assert classify_opportunity(None, None, [])["classification"] == "Other"
    result = classify_opportunity("International news", "Internshipship and hackathoners", [])
    assert result["matched_terms"] == []
    assert result["classification"] == "Other"


def test_passing_research_mention_even_inside_scanned_prefix():
    result = classify_opportunity(
        "Ceramics research", None, ["One participant had previously completed an internship."]
    )
    assert result["classification"] == "Other"
    assert result["matched_terms"] == []


def test_terms_are_capped_at_the_strongest_source():
    result = classify_opportunity("Internship", "internship " * 200, ["internship " * 3000] * 3)
    assert result["classification"] == "Other"
    assert result["matched_terms"] == [{"term": "internship", "source": "subject", "weight": 10}]
    assert (
        classify_opportunity(None, "internship", [])["matched_terms"][0]["weight"]
        > classify_opportunity(None, None, ["internship. Apply by Friday."])["matched_terms"][0]["weight"]
    )


def test_attachment_prefix_and_late_context():
    padding = "Technical measurements.\n" * 220
    assert classify_opportunity(None, None, [padding + "Summer internship\nStipend"])["matched_terms"] == []
    result = classify_opportunity(None, None, [
        "Summer internship\n" + padding + "Eligibility requirements\nApply by Friday"
    ])
    assert result["classification"] == "Internship"
    assert all(match["source"] == "attachment" for match in result["matched_terms"])


def test_close_scores_reduce_confidence():
    clear = classify_opportunity("Internship", "Apply by Friday.", [])
    mixed = classify_opportunity("Hackathon and internship", "Apply by Friday. Teams can win a prize.", [])
    assert mixed["classification"] == clear["classification"] == "Internship"
    assert mixed["confidence"] < clear["confidence"]
    assert mixed["requires_review"]


@pytest.mark.parametrize("subject", ["Campus hackathon registration deadline", "Summer internship"])
def test_subject_only_never_qualifies(subject):
    result = classify_opportunity(subject, None, [])
    assert result["classification"] == "Other"
    assert "corroborating" in result["reasons"][0]


def test_incidental_attachment_terms_never_boost_subject():
    report = ("Materials research report. " * 100 + "Hackathon, prize, coding competition. ") * 30
    baseline = classify_opportunity("Campus hackathon", None, [])
    result = classify_opportunity("Campus hackathon", None, [report])
    assert result["classification"] == baseline["classification"] == "Other"
    assert result["matched_terms"] == baseline["matched_terms"]
    assert result["documents"][1]["rejection_reasons"]["Hackathon"] == "no_cue"


def test_attachment_can_corroborate_subject():
    result = classify_opportunity("Campus hackathon", None, ["Enter the campus hackathon. Registration deadline: Friday."])
    assert result["classification"] == "Hackathon"
    assert {"term": "registration deadline", "source": "attachment", "weight": 4} in result["matched_terms"]


def test_body_can_qualify_but_unrelated_attachment_prompts_review():
    result = classify_opportunity(
        "Internship brochure", "Apply by Friday.", ["Materials research measurements and calibration."]
    )
    assert result["classification"] == "Internship"
    assert result["requires_review"]
    assert result["review_reasons"]


def test_generic_email_with_two_opportunity_attachments():
    body = (FIXTURES / "generic_attachment_email.txt").read_text(encoding="utf-8")
    hackathon = (FIXTURES / "attachment_hackathon.txt").read_text(encoding="utf-8")
    internship = (FIXTURES / "attachment_internship_ocr.txt").read_text(encoding="utf-8")
    result = classify_opportunity(None, body, [("internship.docx", hackathon), ("flyer.pdf", internship)])
    email, first, second = result["documents"]
    assert (email["source"], email["classification"]) == ("email", "Other")
    assert (first["source"], first["classification"]) == ("internship.docx", "Hackathon")
    assert (second["source"], second["classification"]) == ("flyer.pdf", "Internship")
    assert first["score"] < second["score"]
    assert result["classification"] == second["classification"]
    assert result["requires_review"]
    assert result["review_reasons"] == [
        "Email appears to contain multiple opportunities (Hackathon, Internship)"
    ]
    assert classify_opportunity(None, body, [("internship.docx", hackathon)])["classification"] == "Hackathon"


def test_repeated_category_terms_can_qualify_outside_title_zone():
    intro = "Technical measurements.\n" * 20
    once = classify_opportunity(None, None, [intro + "An internship was mentioned. Apply by Friday."])
    assert once["classification"] == "Other"
    assert once["confidence"] < 1
    assert once["documents"][1]["rejection_reasons"]["Internship"] == "passing_mention"
    twice = classify_opportunity(None, None, [intro + "Interns are invited. Interns may apply."])
    assert twice["classification"] == "Internship"


def test_attachments_cannot_borrow_cues_from_one_another():
    result = classify_opportunity(None, None, [("title.docx", "Campus hackathon"), ("cue.pdf", "Registration")])
    assert result["classification"] == "Other"
    assert result["documents"][1]["rejection_reasons"]["Hackathon"] == "no_cue"
    assert result["documents"][2]["rejection_reasons"]["Hackathon"] == "no_category_term"
    assert result["confidence"] < 1
    assert not classify_opportunity(None, "Normal library hours", [])["requires_review"]


def test_other_confidence_distinguishes_cues_from_no_hits():
    result = classify_opportunity("Library update", "Selection of books is unchanged.", [])
    assert 0 < result["confidence"] < 1
    assert not result["requires_review"]
    assert classify_opportunity("Library hours", None, [])["confidence"] == 1
