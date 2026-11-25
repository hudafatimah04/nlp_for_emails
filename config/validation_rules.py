"""Configurable NLP validation rules for local JSON output."""

from config.departments import MAX_ACADEMIC_YEAR

# PENDING CONFIRMATION WITH BACKEND: mandatory fields and ranges.
REQUIRED_FIELDS = ["title", "type"]
# Unstated optional fields remain null without triggering review.
RECOMMENDED_FIELDS: list[str] = []
MATERIAL_CONFLICT_FIELDS = {"type", "title", "deadline", "registration_link"}
REVIEW_WARNING_CODES = {"deadline_after_start"}
MIN_YEAR_ALLOWED = 1
MAX_YEAR_ALLOWED = MAX_ACADEMIC_YEAR
MIN_CGPA_ALLOWED = 0
MAX_CGPA_ALLOWED = 10
ALLOWED_TYPES = ["Hackathon", "Internship", "Event"]
ALLOWED_URL_SCHEMES = ("http", "https")
