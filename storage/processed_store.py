"""Remember emails that were processed successfully."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
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
_HANDOFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff_ledger (
    idempotency_key TEXT PRIMARY KEY NOT NULL,
    delivered_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    reference TEXT
)
"""


@contextmanager
def _read_table(table: str) -> Iterator[sqlite3.Connection | None]:
    path = Path(settings.PROCESSED_DB_PATH).expanduser().resolve()
    if not path.exists():
        yield None
        return
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        yield connection if exists else None


def is_processed(gmail_message_id: str, message_id_header: str | None) -> bool:
    """Check both identifiers without creating or modifying the database."""
    with _read_table("processed_emails") as connection:
        if connection is None:
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


def clear_processed(gmail_message_id: str, message_id_header: str | None) -> None:
    """Clear a prior skip marker for an explicit reprocess, leaving deliveries intact."""
    if not is_processed(gmail_message_id, message_id_header):
        return
    path = Path(settings.PROCESSED_DB_PATH).expanduser()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "DELETE FROM processed_emails WHERE gmail_message_id = ? OR message_id_header = ?",
            (gmail_message_id, (message_id_header or "").strip() or None),
        )


def is_delivered(idempotency_key: str) -> bool:
    """Check for a successful handoff without creating the database or table."""
    with _read_table("handoff_ledger") as connection:
        return connection is not None and connection.execute(
            "SELECT 1 FROM handoff_ledger WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone() is not None


def record_delivery(idempotency_key: str, mode: str, reference: str | None) -> None:
    """Record a successful handoff in the local NLP ledger."""
    path = Path(settings.PROCESSED_DB_PATH).expanduser()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(_HANDOFF_SCHEMA)
        connection.execute(
            """
            INSERT INTO handoff_ledger (idempotency_key, delivered_at, mode, reference)
            VALUES (?, ?, ?, ?) ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (idempotency_key, datetime.now(timezone.utc).isoformat(), mode, reference),
        )
