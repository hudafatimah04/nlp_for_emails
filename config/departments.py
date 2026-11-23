"""Institution-specific extraction settings."""

# VERIFY AGAINST THE DEPARTMENT TABLE IN THE DATABASE
DEPARTMENT_ALIASES = {
    "CSE": ["computer science and engineering", "computer science & engineering", "computer science"],
    "ISE": ["information science and engineering", "information science & engineering", "information science"],
    "ECE": ["electronics and communication engineering", "electronics & communication engineering"],
    "EEE": ["electrical and electronics engineering", "electrical & electronics engineering"],
    "ME": ["MECH", "mechanical engineering"],
    "CV": ["CE", "CIVIL", "civil engineering"],
    "AIML": ["AI/ML", "AI & ML", "artificial intelligence and machine learning"],
    "IT": ["information technology"],
    "DS": ["data science"],
    "BT": ["biotechnology"],
}

MAX_ACADEMIC_YEAR = 4
ALL_DEPARTMENT_PHRASES = (
    "all branches", "open to all", "all departments", "all streams", "any department",
)
