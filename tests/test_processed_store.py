import sqlite3
from contextlib import closing
from datetime import datetime

from config import settings
from storage.processed_store import is_delivered, is_processed, mark_processed, record_delivery


def test_processed_store_matches_either_id_and_leaves_lookup_read_only(tmp_path, monkeypatch):
    database = tmp_path / "processed # inbox.db"
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(database))
    assert not is_processed("first", "<synthetic-id>")
    assert not database.exists()

    mark_processed("first", "<synthetic-id>", "Hackathon")
    assert is_processed("first", None)
    assert is_processed("different", "<synthetic-id>")
    assert not is_processed("different", "<different-id>")
    assert not is_processed("different", None)

    mark_processed("first", None, "Internship")
    assert is_processed("different", "<synthetic-id>")
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute("SELECT status, processed_at FROM processed_emails").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "Internship"
    assert datetime.fromisoformat(rows[0][1]).utcoffset().total_seconds() == 0


def test_nullable_header_does_not_match_other_missing_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(tmp_path / "processed.db"))
    mark_processed("one", None, "Other")
    assert is_processed("one", None)
    assert not is_processed("two", None)
    assert not is_processed("two", "")


def test_ledger_is_lazy_and_delivery_keys_are_unique(tmp_path, monkeypatch):
    database = tmp_path / "local.db"
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(database))
    key = "synthetic-email:1"
    assert not is_delivered(key, "mock")
    assert not database.exists()
    mark_processed("legacy-email", None, "processed")
    assert not is_delivered(key, "mock")
    record_delivery(key, "mock", "synthetic-file.json")
    record_delivery(key, "mock", "synthetic-retry-reference")
    assert is_delivered(key, "mock")
    assert not is_delivered("synthetic-email:2", "mock")
    assert not is_processed("synthetic-email", None)
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute("SELECT idempotency_key, mode, reference, delivered_at FROM handoff_ledger").fetchall()
    assert len(rows) == 1
    assert rows[0][:3] == (key, "mock", "synthetic-file.json")
    assert datetime.fromisoformat(rows[0][3]).utcoffset().total_seconds() == 0


def test_legacy_handoff_ledger_migrates_once_and_preserves_rows(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    monkeypatch.setattr(settings, "PROCESSED_DB_PATH", str(database))
    key = "fake-msg-001:1"
    legacy = [
        (key, "2026-01-01T00:00:00+00:00", "mock", "fake-outbox.json"),
        ("fake-msg-002:1", "2026-01-02T00:00:00+00:00", "http", None),
    ]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("""
            CREATE TABLE handoff_ledger (
                idempotency_key TEXT PRIMARY KEY NOT NULL,
                delivered_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                reference TEXT
            )
        """)
        connection.executemany("INSERT INTO handoff_ledger VALUES (?, ?, ?, ?)", legacy)
        legacy_version = connection.execute("PRAGMA schema_version").fetchone()
    assert is_delivered(key, "mock")
    assert not is_delivered(key, "http")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA schema_version").fetchone() == legacy_version

    record_delivery(key, "http", "fake-receipt")
    with closing(sqlite3.connect(database)) as connection:
        migrated_version = connection.execute("PRAGMA schema_version").fetchone()
        assert migrated_version != legacy_version
        columns = connection.execute("PRAGMA table_info(handoff_ledger)").fetchall()
        assert {row[1]: row[5] for row in columns if row[5]} == {"idempotency_key": 1, "mode": 2}
    record_delivery(key, "http", "ignored-repeat")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA schema_version").fetchone() == migrated_version
        rows = connection.execute(
            "SELECT idempotency_key, delivered_at, mode, reference FROM handoff_ledger"
        ).fetchall()
    assert len(rows) == 3
    assert all(row in rows for row in legacy)
    assert [row[3] for row in rows if row[0] == key and row[2] == "http"] == ["fake-receipt"]
