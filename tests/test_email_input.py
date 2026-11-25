"""Verify conservative forwarded-header cleaning and original line citations."""

import json

import pytest

from nlp.email_input import forwarded_recipient_lines
from nlp.llm_extractor import GeminiEvidenceError, extract_opportunities
from tests.test_llm_extractor import candidate, claim, client_for, ref


BODY = (
    "Correction: Registration now closes on 10 July 2028.\r\n"
    "\r\n"
    "---------- Forwarded message ---------\r\n"
    "From: Synthetic Organizer <organizer@example.org>\r\n"
    "Subject: Synthetic Campus\r\n"
    "Hackathon\r\n"
    "To: Synthetic Group <\r\n"
    "group@example.org>, <\r\n"
    "another@example.org>\r\n"
    "Cc: other@example.org\r\n"
    "\r\n"
    "> Synthetic Campus Hackathon\r\n"
    "> Register at https://example.org/register\r\n"
    "> Contact: organizer@example.org\r\n"
    "> Open to second-year students.\r\n"
)


def test_forwarded_routing_removed_without_renumbering_or_removing_content():
    original = BODY.split("\n")
    assert forwarded_recipient_lines(original) == {7, 8, 9, 10}
    client = client_for(candidate(
        "Hackathon", ref("> Synthetic Campus Hackathon", 12),
        claim("registration_link", "https://example.org/register", ref("> Register at https://example.org/register", 13)),
    ))
    record, = extract_opportunities("Synthetic forward", BODY, client=client)
    payload = json.loads(client.models.generate_content.call_args.kwargs["contents"])
    sent = payload["documents"][1]["lines"]
    assert sent == {
        "1": "Correction: Registration now closes on 10 July 2028.",
        "4": "From: Synthetic Organizer <organizer@example.org>",
        "5": "Subject: Synthetic Campus", "6": "Hackathon",
        "12": "Synthetic Campus Hackathon",
        "13": "Register at https://example.org/register",
        "14": "Contact: organizer@example.org",
        "15": "Open to second-year students.",
    }
    assert record["evidence"]["registration_link"]["line_number"] == 13


@pytest.mark.parametrize("line, span", [
    (9, "another@example.org"), (6, "Hackathon To: Synthetic Group"),
])
def test_hidden_routing_lines_cannot_be_used_as_evidence(line, span):
    with pytest.raises(GeminiEvidenceError):
        extract_opportunities(None, BODY, client=client_for(candidate("Hackathon", ref(span, line))))


@pytest.mark.parametrize("body", [
    "To: example@example.org\nApply by Friday.",
    "---------- Forwarded message ---------\nTo: example@example.org\n",
    "Begin forwarded message:\n\nFrom: Example\nTo: example@example.org\nApply by Friday.",
    "---------- Forwarded message ---------\nFrom: Example\nTo: 3rd-year students\n",
])
def test_ambiguous_headers_and_ordinary_body_are_preserved(body):
    assert forwarded_recipient_lines(body.split("\n")) == set()

def test_nested_forward_keeps_faculty_correction_and_original_evidence():
    body = """New deadline: 10 July 2028.
Only second-year students may apply.

> ---------- Forwarded message ---------
> From: Faculty <faculty@example.org>
> Date: Tuesday, 2 June 2028
> Subject: Fwd: Campus announcement
> To: Synthetic Engineering & Design Group <students@example.org>,
> <more@example.org>
>
> Please obtain faculty approval.
> Sent from my iPhone
>
> ---------- Forwarded message ---------
> From: Organizer <organizer@example.org>
> Date: Monday, 1 June 2028
> Subject: Original hackathon details
> Cc: organizer@example.org
>
> **Campus Hackathon**
> Original deadline: 1 July 2028.
> Register at https://example.org/register?q=a*b and pay ₹200."""
    client = client_for(candidate(
        "Hackathon", ref("Campus Hackathon", 20),
        claim("deadline", "2028-07-10", ref("New deadline: 10 July 2028.", 1)),
    ))
    record, = extract_opportunities("Campus announcement", body, client=client)
    sent = json.loads(client.models.generate_content.call_args.kwargs["contents"])["documents"][1]["lines"]
    assert sent == {
        "1": "New deadline: 10 July 2028.",
        "2": "Only second-year students may apply.",
        "5": "From: Faculty <faculty@example.org>",
        "11": "Please obtain faculty approval.",
        "15": "From: Organizer <organizer@example.org>",
        "17": "Subject: Original hackathon details",
        "20": "Campus Hackathon",
        "21": "Original deadline: 1 July 2028.",
        "22": "Register at https://example.org/register?q=a*b and pay ₹200.",
    }
    assert record["evidence"]["type"]["line_number"] == 20
    assert record["evidence"]["deadline"]["line_number"] == 1
