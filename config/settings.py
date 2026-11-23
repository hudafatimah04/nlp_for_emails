import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH") or None
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH") or None
ALLOWED_SENDERS = [
    sender.strip()
    for sender in os.getenv("ALLOWED_SENDERS", "").split(",")
    if sender.strip()
]
ATTACHMENT_DIR = os.getenv("ATTACHMENT_DIR") or None
TESSERACT_CMD = os.getenv("TESSERACT_CMD") or None
PROCESSED_DB_PATH = os.getenv("PROCESSED_DB_PATH") or "processed_emails.db"

_poll_interval = os.getenv("POLL_INTERVAL_SECONDS", "").strip()
POLL_INTERVAL_SECONDS = int(_poll_interval) if _poll_interval else None

BACKEND_API_URL = os.getenv("BACKEND_API_URL") or None
