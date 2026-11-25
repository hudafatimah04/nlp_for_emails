"""Build a request-specific Groq schema and convert its typed claims."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from nlp.llm_errors import GroqExtractionError

FIELD_TYPES = {
    "title": str, "description": str, "organizer": str,
    "deadline": str, "start_date": str, "end_date": str,
    "location": str, "registration_link": str, "fee": float, "team_based": bool,
    "min_year": int, "max_year": int, "min_cgpa": float,
    "department_scope": Literal["ALL", "SPECIFIC"], "eligible_departments": list[str],
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def generation_model(
    content: str, visual_sources: dict[str, tuple[str, int | None]],
) -> type[BaseModel]:
    """Allow citations only to text and visual parts included in the request."""
    documents = json.loads(content)["documents"]
    text_ids = tuple(doc["source_id"] for doc in documents if any(line.strip() for line in doc["lines"].values()))
    evidence_types = []
    if text_ids:
        evidence_types.append(create_model(
            "TextEvidence", __base__=_Strict, source_id=(Literal[text_ids], ...),
            line_number=(int, Field(ge=1)), span=(str, Field(min_length=1, max_length=500)),
        ))
    if visual_sources:
        evidence_types.append(create_model(
            "VisualEvidence", __base__=_Strict, kind=(Literal["visual"], ...),
            visual_id=(Literal[tuple(visual_sources)], ...), span=(str, Field(min_length=1, max_length=500)),
        ))
    if not evidence_types:
        return create_model("GroqResponse", __base__=_Strict, opportunities=(list[Any], Field(max_length=0)))
    evidence_type = evidence_types[0] if len(evidence_types) == 1 else evidence_types[0] | evidence_types[1]

    claim_models = {
        value_type: create_model(
            f"Claim{index}", __base__=_Strict, value=(value_type, ...), evidence=(evidence_type, ...),
        )
        for index, value_type in enumerate(dict.fromkeys(FIELD_TYPES.values()))
    }
    claims = create_model("Claims", __base__=_Strict, **{
        field: (list[claim_models[value_type]], ...) for field, value_type in FIELD_TYPES.items()
    })
    document_ids = tuple(dict.fromkeys([*text_ids, *(source_id for source_id, _ in visual_sources.values())]))
    opportunity = create_model(
        "Opportunity", __base__=_Strict, category=(Literal["Hackathon", "Internship", "Event"], ...),
        category_evidence=(evidence_type, ...), source_ids=(list[Literal[document_ids]], Field(min_length=1)),
        claims=(claims, ...),
    )
    return create_model("GroqResponse", __base__=_Strict, opportunities=(list[opportunity], Field(max_length=1)))


def internal_response(
    text: str, model: type[BaseModel], visual_sources: dict[str, tuple[str, int | None]],
) -> str:
    """Validate the wire response and translate it for shared extraction checks."""
    try:
        # Validate separately from conversion to keep numeric values as received.
        model.model_validate_json(text)
        data = json.loads(text)
    except (ValidationError, ValueError):
        raise GroqExtractionError("Groq returned invalid structured output.") from None

    def convert_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
        if evidence.get("kind") != "visual":
            return evidence
        source_id, page = visual_sources[evidence["visual_id"]]
        return {"kind": "visual", "source_id": source_id, "page_number": page, "span": evidence["span"]}

    for opportunity in data["opportunities"]:
        opportunity["category_evidence"] = convert_evidence(opportunity["category_evidence"])
        opportunity["claims"] = [
            {"field": field, "value": claim["value"], "evidence": convert_evidence(claim["evidence"])}
            for field, entries in opportunity["claims"].items() for claim in entries
        ]
    return json.dumps(data, ensure_ascii=False)
