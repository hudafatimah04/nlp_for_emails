"""Configurable NLP validation rules for local JSON output."""

from config.departments import MAX_ACADEMIC_YEAR

# PENDING CONFIRMATION WITH BACKEND: mandatory fields and ranges.
REQUIRED_FIELDS = ["title", "type"]
RECOMMENDED_FIELDS = [
    "deadline", "organizer", "location", "registration_link", "fee", "team_based",
    "min_year", "max_year", "min_cgpa", "department_scope",
]
MIN_YEAR_ALLOWED = 1
MAX_YEAR_ALLOWED = MAX_ACADEMIC_YEAR
MIN_CGPA_ALLOWED = 0
MAX_CGPA_ALLOWED = 10
ALLOWED_TYPES = ["Hackathon", "Internship"]
ALLOWED_URL_SCHEMES = ("http", "https")
