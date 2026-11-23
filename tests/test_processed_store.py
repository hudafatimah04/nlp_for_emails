import sqlite3
from contextlib import closing
from datetime import datetime

from config import settings
from storage.processed_store import is_processed, mark_processed


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
