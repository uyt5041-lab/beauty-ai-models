"""Durable image-job state for the single-host inference service.

SQLite owns the queue. Claiming a row is atomic across processes, but callers
must separately own the exclusive execution lock to serialize GPU execution.
No timeout/lease expiry is interpreted as evidence that a processor stopped.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Iterator, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from .contracts import (
    BackgroundRequest, CutoutRequest, ImageJob, ImageRequest, ImageResult,
    content_hash,
)
from .errors import GatewayError
from .execution_trace import ExecutionRecord, request_metadata


_REQUEST_ADAPTER = TypeAdapter(ImageRequest)
_RESULT_ADAPTER = TypeAdapter(ImageResult)
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


@dataclass(frozen=True)
class ClaimedJob:
    job: ImageJob
    project_id: UUID
    request: ImageRequest
    created_at: str | None = None
    claimed_at: str | None = None


@dataclass(frozen=True)
class JobTiming:
    """Server wall-clock receipt-to-terminal interval, not GPU compute time."""

    job_id: UUID
    status: str
    created_at: str
    updated_at: str
    ledger_elapsed_ms: float | None
    clock_basis: Literal["server_wall_clock"] = "server_wall_clock"
    unavailable_reason: Literal["JOB_NOT_TERMINAL", "CLOCK_MOVED_BACKWARD"] | None = None


class JobExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    request_hash: str
    recording_status: Literal["RECORDED", "NOT_RECORDED"]
    execution: ExecutionRecord | None


class JobLedger:
    """One durable SQLite job registry, shared by API and worker processes."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','succeeded','failed','interrupted')),
                    owner_id TEXT,
                    result_json TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
                db.execute("CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project_id)")
                db.execute("""CREATE TABLE IF NOT EXISTS job_executions (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
                    execution_json TEXT NOT NULL
                )""")
        except (OSError, sqlite3.Error) as exc:
            raise self._storage_error() from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _storage_error(*, retryable: bool = True) -> GatewayError:
        return GatewayError("JOB_STORAGE_ERROR", "Image job storage is unavailable or inconsistent.",
                            retryable=retryable)

    @staticmethod
    def _state_error() -> GatewayError:
        return GatewayError("JOB_STATE_CONFLICT", "Image job state or completion does not match its owner.")

    @staticmethod
    def _not_found() -> GatewayError:
        return GatewayError("IMAGE_JOB_NOT_FOUND", "Image job was not found in this project.")

    @staticmethod
    def _uuid(value: UUID) -> UUID:
        if not isinstance(value, UUID):
            raise ValueError("Job and project identifiers must be UUID values")
        return value

    @staticmethod
    def _owner(owner_id: str) -> str:
        if (not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128
                or any(ord(char) < 32 for char in owner_id)):
            raise ValueError("Worker owner ID must be non-empty and at most 128 characters")
        return owner_id

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> ClaimedJob:
        try:
            request = _REQUEST_ADAPTER.validate_json(row["request_json"])
            if content_hash(request) != row["request_hash"]:
                raise ValueError("Stored snapshot hash mismatch")
            result = (_RESULT_ADAPTER.validate_json(row["result_json"])
                      if row["result_json"] is not None else None)
            job = ImageJob(job_id=row["job_id"], request_hash=row["request_hash"],
                           status=row["status"], result=result, error_code=row["error_code"])
            if result is not None and result.kind != request.kind:
                raise ValueError("Stored result kind mismatch")
            if job.status in ("queued", "running") and job.error_code is not None:
                raise ValueError("Non-terminal job cannot contain an error")
            if job.status == "running" and not row["owner_id"]:
                raise ValueError("Running job requires an owner")
            if job.status == "queued" and row["owner_id"] is not None:
                raise ValueError("Queued job cannot have an owner")
            if job.error_code is not None and not _ERROR_CODE.fullmatch(job.error_code):
                raise ValueError("Stored error must be a stable code")
            return ClaimedJob(job=job, project_id=UUID(row["project_id"]), request=request,
                              created_at=row["created_at"],
                              claimed_at=row["updated_at"] if job.status == "running" else None)
        except (ValueError, TypeError, ValidationError) as exc:
            raise cls._storage_error(retryable=False) from exc

    def submit(self, job_id: UUID, project_id: UUID, request: ImageRequest) -> ImageJob:
        """Commit a snapshot once; return the durable current state on re-submit."""
        self._uuid(job_id)
        self._uuid(project_id)
        if not isinstance(request, (CutoutRequest, BackgroundRequest)):
            raise ValueError("A validated image request is required")
        # Revalidate model_copy/constructed values, and sever mutable references.
        try:
            snapshot = _REQUEST_ADAPTER.validate_python(request.model_dump(mode="json"))
            request_hash = content_hash(snapshot)
        except (ValueError, TypeError, ValidationError) as exc:
            raise self._state_error() from exc
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM jobs WHERE job_id=?", (str(job_id),)).fetchone()
                if row is not None:
                    if row["project_id"] != str(project_id):
                        raise self._not_found()
                    if row["request_hash"] != request_hash:
                        raise GatewayError("IDEMPOTENCY_CONFLICT",
                                           "Job ID already belongs to different inputs.")
                    job = self._decode(row).job
                else:
                    now = self._now()
                    db.execute("""INSERT INTO jobs
                        (job_id, project_id, request_hash, request_json, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 'queued', ?, ?)""", (
                        str(job_id), str(project_id), request_hash, snapshot.model_dump_json(), now, now))
                    row = db.execute("SELECT * FROM jobs WHERE job_id=?", (str(job_id),)).fetchone()
                    job = self._decode(row).job
            # Returning outside the context guarantees the commit has completed.
            return job
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def get(self, job_id: UUID, project_id: UUID) -> ImageJob:
        self._uuid(job_id)
        self._uuid(project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM jobs WHERE job_id=? AND project_id=?",
                                 (str(job_id), str(project_id))).fetchone()
            if row is None:
                raise self._not_found()
            return self._decode(row).job
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def timing(self, job_id: UUID, project_id: UUID) -> JobTiming:
        """Read one project-scoped snapshot without inferring missing phases.

        The interval includes queueing, processing and storage. A terminal job
        may also be failed or interrupted; this is not a quality/success metric.
        Wall-clock corrections can affect this value even when non-negative.
        """
        self._uuid(job_id)
        self._uuid(project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM jobs WHERE job_id=? AND project_id=?",
                                 (str(job_id), str(project_id))).fetchone()
            if row is None:
                raise self._not_found()
            job = self._decode(row).job
            try:
                created = datetime.fromisoformat(row["created_at"])
                updated = datetime.fromisoformat(row["updated_at"])
                if created.utcoffset() is None or updated.utcoffset() is None:
                    raise ValueError("Timestamps require a timezone")
                elapsed = (updated - created).total_seconds() * 1000
            except (ValueError, TypeError, OverflowError) as exc:
                raise self._storage_error(retryable=False) from exc
            reason = ("JOB_NOT_TERMINAL" if job.status in ("queued", "running") else
                      "CLOCK_MOVED_BACKWARD" if elapsed < 0 else None)
            return JobTiming(job.job_id, job.status, row["created_at"], row["updated_at"],
                             elapsed if reason is None else None, unavailable_reason=reason)
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def claim_next(self, owner_id: str) -> ClaimedJob | None:
        """Atomically claim the oldest queued snapshot, including after restart.

        This method prevents duplicate claims; it does not itself acquire the
        single-host processor lock. Only the exclusive worker calls it to run.
        """
        self._owner(owner_id)
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                if row is None:
                    claimed = None
                else:
                    self._decode(row)
                    db.execute("""UPDATE jobs SET status='running', owner_id=?, updated_at=?
                        WHERE job_id=? AND status='queued'""", (owner_id, self._now(), row["job_id"]))
                    updated = db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()
                    claimed = self._decode(updated)
            return claimed
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    @staticmethod
    def _execution_matches(packet: ExecutionRecord, claimed: ClaimedJob, status: str) -> bool:
        expected = request_metadata(claimed.request)
        return (packet.job_id == claimed.job.job_id and packet.project_id == claimed.project_id
                and packet.request_hash == claimed.job.request_hash and packet.outcome == status
                and all(getattr(packet, name) == value for name, value in expected.items()))

    def execution(self, job_id: UUID, project_id: UUID) -> JobExecution:
        """Read one atomic job/observation snapshot; absent data stays absent."""
        self._uuid(job_id)
        self._uuid(project_id)
        try:
            with self._connection() as db:
                row = db.execute("""SELECT jobs.*, e.job_id AS execution_job_id,
                    e.project_id AS execution_project_id, e.request_hash AS execution_request_hash,
                    e.status AS execution_status, e.execution_json AS execution_json
                    FROM jobs LEFT JOIN job_executions e ON e.job_id=jobs.job_id
                    WHERE jobs.job_id=? AND jobs.project_id=?""", (str(job_id), str(project_id))).fetchone()
            if row is None:
                raise self._not_found()
            claimed = self._decode(row)
            packet = None
            if row["execution_job_id"] is not None:
                try:
                    raw = row["execution_json"]
                    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
                        raise ValueError("Invalid execution payload")
                    packet = ExecutionRecord.model_validate_json(raw)
                    if (row["execution_project_id"] != row["project_id"]
                            or row["execution_request_hash"] != row["request_hash"]
                            or row["execution_status"] != claimed.job.status
                            or not self._execution_matches(packet, claimed, claimed.job.status)):
                        raise ValueError("Execution does not match current job")
                except (ValueError, TypeError, ValidationError):
                    raise self._storage_error(retryable=False) from None
            return JobExecution(job_id=job_id, status=claimed.job.status,
                                request_hash=claimed.job.request_hash,
                                recording_status="RECORDED" if packet is not None else "NOT_RECORDED",
                                execution=packet)
        except sqlite3.Error:
            raise self._storage_error() from None

    def finish(self, job_id: UUID, owner_id: str, result: ImageResult | None = None,
               error_code: str | None = None, *, execution: ExecutionRecord | None = None) -> ImageJob:
        """Complete only an owned running job, after worker result-file checks.

        Exactly one of a typed success result and a stable error code is needed.
        Terminal states are immutable, including duplicate completion attempts.
        """
        self._uuid(job_id)
        self._owner(owner_id)
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM jobs WHERE job_id=?", (str(job_id),)).fetchone()
                if row is None:
                    raise self._not_found()
                claimed = self._decode(row)
                if claimed.job.status != "running" or row["owner_id"] != owner_id:
                    raise self._state_error()
                if (result is None) == (error_code is None):
                    raise self._state_error()
                if error_code is not None:
                    if not isinstance(error_code, str) or not _ERROR_CODE.fullmatch(error_code):
                        raise self._state_error()
                    result_json, status = None, "failed"
                else:
                    try:
                        validated = _RESULT_ADAPTER.validate_python(
                            result.model_dump(mode="json"))
                    except (ValueError, TypeError, ValidationError, AttributeError) as exc:
                        raise self._state_error() from exc
                    if validated.kind != claimed.request.kind:
                        raise self._state_error()
                    result_json, status = validated.model_dump_json(), "succeeded"
                execution_json = None
                if execution is not None:
                    try:
                        if not isinstance(execution, ExecutionRecord):
                            raise ValueError("A validated execution record is required")
                        execution_json = execution.model_dump_json()
                        if len(execution_json.encode("utf-8")) > 65536:
                            raise ValueError("Execution record too large")
                        packet = ExecutionRecord.model_validate_json(execution_json)
                        if not self._execution_matches(packet, claimed, status):
                            raise ValueError("Execution does not match completion")
                    except (ValueError, TypeError, ValidationError):
                        raise self._state_error() from None
                db.execute("""UPDATE jobs SET status=?, result_json=?, error_code=?, updated_at=?
                    WHERE job_id=? AND status='running' AND owner_id=?""", (
                    status, result_json, error_code, self._now(), str(job_id), owner_id))
                if execution_json is not None:
                    db.execute("INSERT INTO job_executions VALUES (?, ?, ?, ?, ?)", (
                        str(job_id), str(claimed.project_id), claimed.job.request_hash, status, execution_json))
                updated = db.execute("SELECT * FROM jobs WHERE job_id=?", (str(job_id),)).fetchone()
                job = self._decode(updated).job
            return job
        except sqlite3.Error as exc:
            raise self._storage_error() from exc

    def recover_interrupted(self) -> int:
        """Mark only previous running jobs interrupted after exclusive lock acquisition.

        PRECONDITION: the caller holds the same exclusive execution lock used by
        every processor sharing this ledger, and no previous processor survives.
        Never call this from an API process or in response to a waiting timeout.
        Queued work remains queued and is rediscovered by ``claim_next``.
        """
        try:
            with self._connection(write=True) as db:
                count = db.execute("""UPDATE jobs SET status='interrupted', result_json=NULL,
                    error_code='WORKER_INTERRUPTED', updated_at=? WHERE status='running'""",
                    (self._now(),)).rowcount
            return count
        except sqlite3.Error as exc:
            raise self._storage_error() from exc
