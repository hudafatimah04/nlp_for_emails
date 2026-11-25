"""Own and inventory disposable attachment files without scanning legacy storage."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from tempfile import mkdtemp

ROOT = Path(__file__).absolute().parents[1] / "runtime_storage"
_current: ContextVar["StorageJob | None"] = ContextVar("storage_job", default=None)


def _plain(path: Path, directory: bool) -> bool:
    """Reject links, junctions and other filesystem objects."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400)


def _identity(path: Path) -> list[int]:
    info = path.lstat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


class StorageJob:
    """Track only files created by the downloader in an isolated workspace."""

    def __init__(self, temporary: bool):
        self.temporary = temporary
        self.path: Path | None = None
        self.files: dict[str, list[int]] = {}
        self.state = "temporary" if temporary else "retained"

    def directory(self) -> Path:
        """Create the workspace lazily on the first eligible download."""
        if self.path is None:
            ROOT.mkdir(exist_ok=True)
            if not _plain(ROOT, True) or ROOT.resolve() != ROOT:
                raise OSError("Managed storage root redirects to another location.")
            self.path = Path(mkdtemp(prefix="job-", dir=ROOT))
        return self.path

    def register(self, path: Path) -> None:
        """Record exactly which downloaded file belongs to this job."""
        if self.path is None:
            return
        name = path.relative_to(self.path)
        if len(name.parts) == 2 and _plain(path, False):
            self.files[name.as_posix()] = _identity(path)

    def close(self) -> None:
        """Publish a completed job or remove completed preview files."""
        if self.path is None:
            return
        # Publish atomically so cleanup never sees partial ownership metadata.
        marker = self.path / "complete.json"
        staged = self.path / "manifest.tmp"
        with staged.open("x", encoding="utf-8") as stream:
            json.dump({"state": self.state, "files": self.files}, stream)
        staged.replace(marker)
        if self.temporary:
            _sweep_job(self.path, apply=True, immediate=True)


def current_job() -> StorageJob | None:
    """Return the attachment workspace scoped to this operation."""
    return _current.get()


@contextmanager
def storage_job(temporary: bool):
    """Close the workspace after success or failure."""
    job = StorageJob(temporary)
    token = _current.set(job)
    try:
        yield job
    finally:
        _current.reset(token)
        job.close()


def _sweep_job(job: Path, *, apply: bool, immediate: bool = False) -> list[dict]:
    marker = job / "complete.json"
    if not _plain(job, True) or not _plain(marker, False):
        return []
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        state = manifest["state"]
        period = {
            "temporary": ("TEMP_RETENTION_HOURS", 24, 3600),
            "failed": ("FAILED_RETENTION_DAYS", 30, 86400),
            "logs": ("LOG_RETENTION_DAYS", 14, 86400),
        }.get(state)
        if period is None:
            return []
        variable, default, seconds = period
        if not immediate and time.time() - marker.stat().st_mtime < max(
            0, int(os.getenv(variable, default))
        ) * seconds:
            return []
        paths = manifest["files"]
        if not isinstance(paths, dict):
            return []
    except (OSError, ValueError, KeyError, TypeError):
        return []

    removed = []
    for name, identity in paths.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9._-]+", name):
            continue
        parent, filename = name.split("/")
        if filename in {".", "..", ".env", "credentials.json", "token.json"} or Path(filename).suffix.lower() in {".db", ".sqlite", ".sqlite3", ".pem", ".key"}:
            continue
        path = job / parent / filename
        try:
            if not _plain(job / parent, True) or not _plain(path, False) or _identity(path) != identity:
                continue
            if apply:
                path.unlink()
            removed.append({"path": str(path), "bytes": identity[2]})
            if apply:
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        except OSError:
            continue
    if apply:
        try:
            if set(job.iterdir()) == {marker}:
                marker.unlink()
                job.rmdir()
        except OSError:
            pass
    return removed


def cleanup(*, apply: bool = False) -> dict:
    """List or remove eligible artifacts from completed managed workspaces."""
    files = []
    if _plain(ROOT, True) and ROOT.resolve() == ROOT:
        for job in sorted(ROOT.iterdir()):
            if re.fullmatch(r"job-[a-z0-9_]{8}", job.name):
                files.extend(_sweep_job(job, apply=apply))
    return {"dry_run": not apply, "files": files, "bytes": sum(file["bytes"] for file in files)}


@contextmanager
def processing_logs():
    """Rotate safe provider telemetry in an isolated command-owned directory."""
    job = StorageJob(temporary=False)
    job.state = "logs"
    directory = job.directory() / "logs"
    directory.mkdir()
    handler = RotatingFileHandler(
        directory / "processing.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8", delay=True,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    loggers = [logging.getLogger(name) for name in ("nlp.llm_extractor", "nlp.groq_provider")]
    for logger in loggers:
        logger.addHandler(handler)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeHandler(handler)
        handler.close()
        for path in directory.glob("processing.log*"):
            job.register(path)
        if not job.files:
            directory.rmdir()
            job.path.rmdir()
        else:
            job.close()
