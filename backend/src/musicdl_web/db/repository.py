"""SQLite-backed queue, browser artifacts, and managed-library index."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from musicdl_web.domain import (
    Delivery,
    Job,
    JobRequest,
    JobStatus,
    PlatformTrack,
    Quality,
)
from musicdl_web.domain.models import ManagedDecision, utc_now
from musicdl_web.models import Source


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    output_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created
                    ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS managed_media (
                    source TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    quality INTEGER NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source, track_id)
                );
                CREATE TABLE IF NOT EXISTS browser_artifacts (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                (_dump_dt(utc_now()),),
            )

    def create_job(self, request: JobRequest) -> Job:
        now = utc_now()
        job = Job(
            id=uuid4().hex,
            request=request,
            status=JobStatus.QUEUED,
            attempt=0,
            progress=0,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as db:
            db.execute(
                "INSERT INTO jobs(id, request_json, status, attempt, progress, "
                "created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    request.model_dump_json(),
                    job.status,
                    job.attempt,
                    job.progress,
                    _dump_dt(now),
                    _dump_dt(now),
                ),
            )
        return job

    def create_rejected_job(
        self,
        *,
        source: Source,
        track_id: str,
        quality_id: str,
        quality_snapshot_id: str,
        delivery: Delivery,
        error_code: str,
    ) -> Job:
        request = JobRequest(
            track=PlatformTrack(
                source=source,
                track_id=track_id,
                title="Unavailable track",
                artists=("Unknown",),
            ),
            quality=Quality.UNRESOLVED,
            quality_id=quality_id,
            quality_label=quality_id,
            quality_snapshot_id=quality_snapshot_id,
            delivery=delivery,
        )
        job = self.create_job(request)
        return self.transition(
            job.id,
            JobStatus.FAILED,
            error_code=error_code,
            error_message="quality selection is invalid or expired",
        )

    def get_job(self, job_id: str) -> Job | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self, *, limit: int = 100) -> list[Job]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def claim_next(self) -> Job | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED,),
            ).fetchone()
            if row is None:
                return None
            now = _dump_dt(utc_now())
            db.execute(
                "UPDATE jobs SET status = ?, attempt = attempt + 1, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (JobStatus.RESOLVING, now, row["id"], JobStatus.QUEUED),
            )
            claimed = db.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return _row_to_job(claimed)

    def transition(
        self,
        job_id: str,
        status: JobStatus,
        *,
        progress: float | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        output_path: str | None = None,
    ) -> Job:
        assignments = ["status = ?", "updated_at = ?", "error_code = ?", "error_message = ?"]
        values: list[object] = [status, _dump_dt(utc_now()), error_code, error_message]
        if progress is not None:
            assignments.append("progress = ?")
            values.append(progress)
        if output_path is not None:
            assignments.append("output_path = ?")
            values.append(output_path)
        values.append(job_id)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                values,
            )
            if result.rowcount != 1:
                raise KeyError(job_id)
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE jobs SET status = ?, updated_at = ? "
                "WHERE id = ? AND status NOT IN (?, ?, ?)",
                (
                    JobStatus.CANCELLED,
                    _dump_dt(utc_now()),
                    job_id,
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                ),
            )
        return result.rowcount == 1

    def retry(self, job_id: str) -> Job:
        with self._connect() as db:
            result = db.execute(
                "UPDATE jobs SET status = ?, progress = 0, error_code = NULL, "
                "error_message = NULL, output_path = NULL, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (JobStatus.QUEUED, _dump_dt(utc_now()), job_id, JobStatus.FAILED),
            )
        if result.rowcount != 1:
            raise ValueError("only failed jobs can be retried")
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def recover_interrupted(self) -> int:
        active = tuple(
            status
            for status in JobStatus
            if not status.terminal and status != JobStatus.QUEUED
        )
        placeholders = ",".join("?" for _ in active)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE jobs SET status = ?, progress = 0, error_code = NULL, "  # noqa: S608
                f"error_message = NULL, updated_at = ? WHERE status IN ({placeholders})",
                (JobStatus.QUEUED, _dump_dt(utc_now()), *active),
            )
        return result.rowcount

    def clear_terminal_history(self) -> int:
        with self._connect() as db:
            result = db.execute(
                "DELETE FROM jobs WHERE status IN (?, ?, ?) AND NOT EXISTS "
                "(SELECT 1 FROM browser_artifacts WHERE browser_artifacts.job_id = jobs.id)",
                (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED),
            )
        return result.rowcount

    def managed_decision(
        self, source: Source, track_id: str, requested: Quality
    ) -> ManagedDecision:
        with self._connect() as db:
            row = db.execute(
                "SELECT quality, path FROM managed_media WHERE source = ? AND track_id = ?",
                (source, track_id),
            ).fetchone()
        if row is None:
            return ManagedDecision(allowed=True, reason="new")
        path = Path(row["path"])
        if not path.exists():
            return ManagedDecision(allowed=True, reason="missing", existing_path=path)
        if requested <= Quality(row["quality"]):
            # Allow a full re-download when audio is present but album art was never
            # written (earlier PNG/size proxy failures). Navidrome-facing cover.jpg /
            # cover.png sit next to the managed audio file.
            if not _has_album_cover(path):
                return ManagedDecision(
                    allowed=True, reason="cover_missing", existing_path=path
                )
            return ManagedDecision(allowed=False, reason="same_or_lower", existing_path=path)
        return ManagedDecision(allowed=True, reason="upgrade", existing_path=path)

    def managed_media(
        self, source: Source, track_id: str
    ) -> tuple[Quality, Path] | None:
        """Return the safe local-library index entry, never platform material."""

        with self._connect() as db:
            row = db.execute(
                "SELECT quality, path FROM managed_media WHERE source = ? AND track_id = ?",
                (source, track_id),
            ).fetchone()
        if row is None:
            return None
        return Quality(row["quality"]), Path(row["path"])

    def register_media(
        self, source: Source, track_id: str, quality: Quality, path: Path
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO managed_media(source, track_id, quality, path, updated_at) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(source, track_id) DO UPDATE SET "
                "quality=excluded.quality, path=excluded.path, updated_at=excluded.updated_at",
                (source, track_id, quality, str(path), _dump_dt(utc_now())),
            )

    def register_browser_artifact(
        self, job_id: str, path: Path, filename: str, expires_at: datetime
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO browser_artifacts(job_id, path, filename, expires_at) "
                "VALUES(?, ?, ?, ?)",
                (job_id, str(path), filename, _dump_dt(expires_at)),
            )

    def browser_artifact(self, job_id: str) -> tuple[Path, str] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT path, filename, expires_at, delivered_at FROM browser_artifacts "
                "WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None or row["delivered_at"] is not None:
            return None
        if _load_dt(row["expires_at"]) <= utc_now():
            return None
        return Path(row["path"]), str(row["filename"])

    def mark_delivered(self, job_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE browser_artifacts SET delivered_at = ? WHERE job_id = ?",
                (_dump_dt(utc_now()), job_id),
            )

    def expired_artifacts(self, now: datetime | None = None) -> list[tuple[str, Path]]:
        threshold = _dump_dt(now or utc_now())
        with self._connect() as db:
            rows = db.execute(
                "SELECT job_id, path FROM browser_artifacts "
                "WHERE expires_at <= ? OR delivered_at IS NOT NULL",
                (threshold,),
            ).fetchall()
        return [(str(row["job_id"]), Path(row["path"])) for row in rows]

    def delete_artifacts(self, job_ids: Iterable[str]) -> None:
        with self._connect() as db:
            db.executemany(
                "DELETE FROM browser_artifacts WHERE job_id = ?", ((i,) for i in job_ids)
            )


def _dump_dt(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _load_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _has_album_cover(audio_path: Path) -> bool:
    parent = audio_path.parent
    return (parent / "cover.jpg").is_file() or (parent / "cover.png").is_file()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        request=JobRequest.model_validate(json.loads(row["request_json"])),
        status=JobStatus(row["status"]),
        attempt=row["attempt"],
        progress=row["progress"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        output_path=row["output_path"],
        created_at=_load_dt(row["created_at"]),
        updated_at=_load_dt(row["updated_at"]),
    )
