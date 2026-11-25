"""Tests for managed artifact ownership and retention."""

import os

import pytest

from storage import runtime_storage as storage


@pytest.fixture
def managed(tmp_path, monkeypatch):
    root = tmp_path / "runtime_storage"
    monkeypatch.setattr(storage, "ROOT", root)
    return root


def _write(job, filename="poster.jpeg"):
    folder = job.directory() / "fake-msg-001"
    folder.mkdir(exist_ok=True)
    path = folder / filename
    path.write_bytes(b"demo")
    job.register(path)
    return path


def test_preview_cleanup_on_success_and_exception(managed):
    with storage.storage_job(temporary=True) as job:
        preview = _write(job)
        assert storage.cleanup(apply=True)["files"] == []
    assert not preview.exists()

    with pytest.raises(RuntimeError):
        with storage.storage_job(temporary=True) as job:
            preview = _write(job)
            raise RuntimeError("synthetic")
    assert not preview.exists()


def test_failed_retention_and_dry_run(managed, monkeypatch):
    with storage.storage_job(temporary=False) as job:
        artifact = _write(job)
        job.state = "failed"
    assert storage.cleanup()["files"] == []
    marker = job.path / "complete.json"
    os.utime(marker, (1, 1))
    assert storage.cleanup() == {
        "dry_run": True, "bytes": 4, "files": [{"path": str(artifact), "bytes": 4}],
    }
    assert artifact.exists()
    assert storage.cleanup(apply=True)["bytes"] == 4
    assert not artifact.exists()
    assert storage.cleanup(apply=True)["bytes"] == 0


def test_retains_review_pending_credentials_databases_and_unknown_files(managed, monkeypatch):
    monkeypatch.setenv("FAILED_RETENTION_DAYS", "0")
    with storage.storage_job(temporary=False) as job:
        review = _write(job, "review.pdf")
        pending = _write(job, "pending.pdf")
    with storage.storage_job(temporary=False) as job:
        job.state = "failed"
        credential = _write(job, "credentials.json")
        database = _write(job, "processed_emails.db")
        unrelated = credential.parent / "unregistered.txt"
        unrelated.write_text("unrelated")
        replaced = _write(job, "replaced.pdf")
        replaced.write_bytes(b"changed after registration")
    assert storage.cleanup(apply=True)["files"] == []
    assert all(p.exists() for p in (review, pending, credential, database, unrelated, replaced))


def test_redirected_workspace_is_ignored(managed, monkeypatch):
    monkeypatch.setenv("FAILED_RETENTION_DAYS", "0")
    with storage.storage_job(temporary=False) as job:
        job.state = "failed"
        document = _write(job)
    outside = managed.parent / "elsewhere"
    document.parent.rename(outside)
    try:
        document.parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires additional permissions on this host.")
    assert storage.cleanup(apply=True)["files"] == []
    assert (outside / document.name).is_file()


def test_closed_log_retention(managed, monkeypatch):
    import logging

    monkeypatch.setenv("LOG_RETENTION_DAYS", "0")
    with storage.processing_logs():
        logging.getLogger("nlp.groq_provider").warning("synthetic status")
        assert storage.cleanup()["files"] == []
    plan = storage.cleanup()
    assert len(plan["files"]) == 1
    assert plan["files"][0]["path"].endswith("processing.log")
    assert plan["bytes"] > 0
