import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_EXCLUDED = {".git", "venv", ".pytest_cache", "__pycache__", "output_json", "backend_outbox", "attachments"}
_SUFFIXES = {".py", ".md", ".txt", ".json", ".csv", ".yaml", ".yml"}


def _candidate_files():
    try:
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        return {ROOT / name for name in listing.stdout.split("\0") if name}
    except (OSError, subprocess.CalledProcessError):
        directories = (
            "config", "document_processing", "gmail_integration", "integration", "nlp",
            "output", "pipeline", "preprocessing", "sample_emails", "storage", "tests", "validation",
        )
        return {ROOT / "main.py", ROOT / ".env.example"} | {
            path for directory in directories for path in (ROOT / directory).rglob("*")
        }


def test_no_real_data_in_source_tests_or_fixtures():
    patterns = {
        "message identifier": re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{16}(?![0-9A-Fa-f])"),
        "personal or institutional domain": re.compile(r"@" + r"(?:gmail\.com|rvce\.edu\.in)", re.IGNORECASE),
        "OAuth token": re.compile(r"ya" + r"29\."),
        "OAuth client secret": re.compile(r"GOC" + r"SPX-"),
    }
    findings = []
    for path in sorted(_candidate_files()):
        relative = path.relative_to(ROOT)
        if _EXCLUDED.intersection(relative.parts) or not (
            path.suffix in _SUFFIXES or path.name == ".env.example"
        ):
            continue
        if not path.is_file():
            continue
        contents = path.read_text(encoding="utf-8")
        findings.extend(f"{relative}: {kind}" for kind, pattern in patterns.items() if pattern.search(contents))
    assert not findings, "Potential sensitive data found:\n" + "\n".join(findings)


def test_sensitive_outputs_and_credentials_are_ignored():
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"output_json/", "backend_outbox/", "*.db", "token.json", "credentials.json", ".env"} <= set(patterns)
