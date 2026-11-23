"""Remember emails that were processed successfully."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_emails (
    gmail_message_id TEXT PRIMARY KEY NOT NULL,
    message_id_header TEXT,
    processed_at TEXT NOT NULL,
    status TEXT NOT NULL
)
"""


def is_processed(gmail_message_id: str, message_id_header: str | None) -> bool:
    """Check both identifiers without creating or modifying the database."""
    path = Path(settings.PROCESSED_DB_PATH).expanduser().resolve()
    if not path.exists():
        return False

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'processed_emails'"
        ).fetchone() is None:
            return False
        return connection.execute(
            """
            SELECT 1 FROM processed_emails
            WHERE gmail_message_id = ? OR message_id_header = ?
            LIMIT 1
            """,
            (gmail_message_id, (message_id_header or "").strip() or None),
        ).fetchone() is not None


def mark_processed(gmail_message_id: str, message_id_header: str | None, status: str) -> None:
    """Record a handled email or update its status after explicit reprocessing."""
    path = Path(settings.PROCESSED_DB_PATH).expanduser()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(_SCHEMA)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS processed_emails_header ON processed_emails (message_id_header)"
        )
        connection.execute(
            """
            INSERT INTO processed_emails (gmail_message_id, message_id_header, processed_at, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (gmail_message_id) DO UPDATE SET
                message_id_header = COALESCE(excluded.message_id_header, processed_emails.message_id_header),
                processed_at = excluded.processed_at,
                status = excluded.status
            """,
            (gmail_message_id, (message_id_header or "").strip() or None,
             datetime.now(timezone.utc).isoformat(), status),
        )
