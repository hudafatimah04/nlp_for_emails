"""Classify email and attachment documents using explainable evidence rules."""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

_RULES = {
    "source_weights": {"subject": 2, "body": 1.5, "attachment": 1},
    "minimum_score": 11,
    "review_margin": 3,
    "attachment_cues": (
        "apply", "application", "register", "registration", "deadline", "last date",
        "eligibility", "eligible", "cgpa", "stipend", "duration", "selection",
        "shortlisted", "required to",
    ),
    "body_context_terms": {
        "Hackathon": {"registration deadline", "prize", "problem statement"},
        "Internship": {"apply by", "last date to apply", "stipend"},
    },
    # Each term maps to its category, base weight, and whether it identifies the category.
    "terms": {
        "hackathon": ("Hackathon", 6, True),
        "coding competition": ("Hackathon", 3, True),
        "registration deadline": ("Hackathon", 4, False),
        "problem statement": ("Hackathon", 2, False),
        "prize": ("Hackathon", 2, False),
        "summer internship": ("Internship", 7, True),
        "internship": ("Internship", 5, True),
        "intern": ("Internship", 3, True),
        "stipend": ("Internship", 4, False),
        "apply by": ("Internship", 4, False),
        "last date to apply": ("Internship", 2, False),
        "hiring": ("Internship", 2, False),
        "placement": ("Internship", 1, False),
    },
}

_CATEGORIES = ("Hackathon", "Internship")
_ATTACHMENT_PREFIX = 5000
_TITLE_CHARACTERS = 300
_TITLE_LINES = 5
_OCR_WORDS = {"hackathon": r"hack[\s._-]*athon", "internship": r"intern[\s._-]*ship"}


def _compile_terms(terms: Sequence[str]) -> tuple[re.Pattern[str], list[str]]:
    names = sorted(terms, key=len, reverse=True)
    patterns = []
    for index, term in enumerate(names):
        words = term.split()
        phrase = r"[\W_]+".join(_OCR_WORDS.get(word, re.escape(word)) for word in words)
        plural = "s?" if words[-1] not in {"by", "apply", "to", "hiring", "eligibility", "eligible", "shortlisted", "register"} else ""
        patterns.append(f"(?P<t{index}>{phrase}{plural})")
    return re.compile(r"(?<!\w)(?:" + "|".join(patterns) + r")(?!\w)", re.IGNORECASE), names


_TERM_PATTERN, _TERMS = _compile_terms(tuple(_RULES["terms"]))
_CUE_PATTERN, _CUES = _compile_terms(_RULES["attachment_cues"])


def _term_matches(
    text: str, pattern: re.Pattern[str] = _TERM_PATTERN, terms: Sequence[str] = _TERMS
) -> list[tuple[str, int]]:
    return [(terms[int(match.lastgroup[1:])], match.end()) for match in pattern.finditer(text)]


def _score(sources: Sequence[tuple[str, Sequence[str]]]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    scores = dict.fromkeys(_CATEGORIES, 0.0)
    hits = []
    seen = set()
    for source, terms in sources:
        for term in terms:
            if term in seen:
                continue
            seen.add(term)
            category, base, _ = _RULES["terms"][term]
            weight = base * _RULES["source_weights"][source]
            scores[category] += weight
            hits.append({"term": term, "source": source, "weight": weight})
    return scores, hits


def _add_unscored_hits(hits: list[dict[str, Any]], source: str, terms: Iterable[str]) -> None:
    seen = {hit["term"] for hit in hits if hit["source"] == source}
    for term in dict.fromkeys(terms):
        if term not in seen:
            hits.append({"term": term, "source": source, "weight": 0})


def _document(
    source: str | None, scores: dict[str, float], hits: list[dict[str, Any]], rejected: dict[str, str],
) -> dict[str, Any]:
    eligible = [category for category in _CATEGORIES if category not in rejected]
    category = max(eligible, key=scores.__getitem__) if eligible else "Other"
    score = scores[category] if eligible else max(scores.values())
    minimum = _RULES["minimum_score"]
    if eligible:
        confidence = score / (score + minimum / 2)
        for alternative in eligible:
            if alternative != category:
                rejected[alternative] = "lower_score"
    else:
        # Partial evidence discounts Other confidence by score / (2 × threshold),
        # with a minimum discount for cue-only hits and a maximum discount of 0.5.
        confidence = 1 - min(0.5, max(score, 1) / (2 * minimum)) if hits else 1.0
    return {
        "source": source,
        "classification": category,
        "score": score,
        "confidence": round(confidence, 3),
        "matched_terms": hits,
        "rejection_reasons": rejected,
        "category_scores": scores,
    }


def _email_document(subject: str, body: str) -> tuple[dict[str, Any], bool, str | None]:
    subject_terms = [term for term, _ in _term_matches(subject)]
    body_terms = [term for term, _ in _term_matches(body)]
    scores, hits = _score((("subject", subject_terms), ("body", body_terms)))
    anchors = {_RULES["terms"][hit["term"]][0] for hit in hits if _RULES["terms"][hit["term"]][2]}
    for source, text in (("subject", subject), ("body", body)):
        _add_unscored_hits(hits, source, (term for term, _ in _term_matches(text, _CUE_PATTERN, _CUES)))
    corroborated = set()
    for term in set(body_terms) - set(subject_terms):
        category, _, anchor = _RULES["terms"][term]
        if anchor or term in _RULES["body_context_terms"][category]:
            corroborated.add(category)

    rejected = {}
    for category in _CATEGORIES:
        if category not in anchors:
            rejected[category] = "no_category_term"
        elif category not in corroborated:
            rejected[category] = "subject_only"
        elif scores[category] < _RULES["minimum_score"]:
            rejected[category] = "below_threshold"

    close = None
    if not rejected and abs(scores["Hackathon"] - scores["Internship"]) <= _RULES["review_margin"]:
        close = "Strong hackathon and internship signals scored close to each other."
    document = _document("email", scores, hits, rejected)
    if close:
        document["confidence"] = round(document["score"] / sum(scores.values()), 3)
    return document, bool(anchors), close


def _title_end(text: str) -> int:
    end = 0
    nonempty = 0
    for line in text.splitlines(keepends=True):
        end += len(line)
        if line.strip():
            nonempty += 1
            if nonempty == _TITLE_LINES:
                break
    return max(_TITLE_CHARACTERS, end)


def _attachment_document(filename: str | None, text: str) -> dict[str, Any]:
    matches = _term_matches(text)
    title_end = _title_end(text)
    scoring_end = max(title_end, _ATTACHMENT_PREFIX)
    prefix = Counter()
    title = set()
    anchors = set()
    for term, end in matches:
        category, _, is_anchor = _RULES["terms"][term]
        if is_anchor:
            anchors.add(category)
            if end <= title_end:
                title.add(category)
            if end <= _ATTACHMENT_PREFIX:
                prefix[category] += 1

    scores, hits = _score((("attachment", [term for term, end in matches if end <= scoring_end]),))
    cues = [term for term, _ in _term_matches(text, _CUE_PATTERN, _CUES)]
    # Cues satisfy the evidence gate. Late terms and cues add no scoring weight.
    _add_unscored_hits(hits, "attachment", [*(term for term, _ in matches), *cues])

    rejected = {}
    for category in _CATEGORIES:
        if category not in anchors:
            rejected[category] = "no_category_term"
        elif category not in title and prefix[category] < 2:
            rejected[category] = "passing_mention"
        elif not cues:
            rejected[category] = "no_cue"

    return _document(filename, scores, hits, rejected)


def classify_opportunity(
    subject: str | None, body_text: str | None,
    attachment_texts: Sequence[str | tuple[str | None, str | None]],
) -> dict[str, Any]:
    """Classify each document independently and select the highest-scoring result."""
    email, email_has_anchor, close = _email_document(subject or "", body_text or "")
    documents = [email]
    has_attachment_text = False
    for attachment in attachment_texts:
        filename, text = ("attachment", attachment) if isinstance(attachment, str) else attachment
        documents.append(_attachment_document(filename, text or ""))
        has_attachment_text |= bool(text and text.strip())

    candidates = [doc for doc in documents if doc["classification"] != "Other"]
    winner = max(candidates, key=lambda doc: doc["score"]) if candidates else None
    review = []
    if winner:
        classification = winner["classification"]
        confidence = winner["confidence"]
        reasons = [f"{classification} had the highest qualifying document score ({winner['score']:g})."]
        categories = {doc["classification"] for doc in candidates}
        if len(categories) > 1:
            review.append("Email appears to contain multiple opportunities (Hackathon, Internship)")
        elif close:
            review.append(close)
        elif winner is email and has_attachment_text and not any(
            doc["classification"] == classification for doc in documents[1:]
        ):
            review.append(f"The attachments did not corroborate the {classification.lower()} classification.")
    else:
        classification = "Other"
        confidence = min(doc["confidence"] for doc in documents)
        reasons = ["No document had enough evidence for a hackathon or internship."]
        if email_has_anchor:
            reasons = ["The email mentioned an opportunity category without enough corroborating evidence."]
            review.extend(reasons)

    # Preserve the summary of scored terms. Document diagnostics retain all hits.
    summary_hits: dict[str, dict[str, Any]] = {}
    for doc in [email, *[doc for doc in candidates if doc is not email]]:
        for hit in doc["matched_terms"]:
            if hit["weight"] > summary_hits.get(hit["term"], {}).get("weight", 0):
                summary_hits[hit["term"]] = hit

    return {
        "classification": classification,
        "confidence": confidence,
        "matched_terms": list(summary_hits.values()),
        "reasons": reasons,
        "requires_review": bool(review),
        "review_reasons": review,
        "documents": documents,
    }
