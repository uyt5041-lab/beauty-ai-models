"""Durable one-submission receipts and conservative reservation accounting.

Reservations are administrative allowances, not invoices or guaranteed price
ceilings. They remain held after every submission, including failures. This
store does not control other consumers of the team's shared OpenAI account.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import UUID, uuid4

from .contracts import ExternalImageResult, ImageProviderUsage
from .errors import GatewayError


def money_units(value: str | Decimal | int | float, *, positive: bool = False) -> int:
    """Use exact microdollars; never round a reservation down or accept NaN."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > 1_000_000:
            raise ValueError
        units = amount * 1_000_000
        if units != units.to_integral_value() or (positive and units <= 0):
            raise ValueError
        return int(units)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Budget must be finite USD in [0, 1000000] with at most six decimals.") from None


class ProviderAttemptStore:
    """One receipt per internal job. No retry, lease expiry, or automatic refund."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as db:
                self._enable_wal(db)
                db.execute("BEGIN IMMEDIATE")
                if db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                    AND name IN ('budgets','reservations','settlements') LIMIT 1""").fetchone():
                    raise self._storage_error()
                db.execute("""CREATE TABLE IF NOT EXISTS provider_attempts (
                    job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('submitting','succeeded','failed','unknown')),
                    reservation_units INTEGER NOT NULL CHECK(reservation_units > 0),
                    provider_request_id TEXT, usage_json TEXT, result_json TEXT,
                    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""")
                db.execute("CREATE INDEX IF NOT EXISTS provider_project ON provider_attempts(project_id)")
                columns = {row[1] for row in db.execute("PRAGMA table_info(provider_attempts)")}
                for name, kind in (("raw_usage_json", "TEXT"), ("metadata_json", "TEXT"),
                                   ("elapsed_seconds", "REAL"), ("account_reservation_id", "TEXT")):
                    if name not in columns:
                        db.execute(f"ALTER TABLE provider_attempts ADD COLUMN {name} {kind}")
                db.execute("""CREATE TABLE IF NOT EXISTS provider_reuse_events (
                    event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, request_hash TEXT NOT NULL, created_at TEXT NOT NULL
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS experiment_limits (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    plan_sha256 TEXT NOT NULL, project_id TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL, jobs_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ready','running','halted','succeeded'))
                )""")
                db.commit()
        except (OSError, sqlite3.Error):
            raise self._storage_error() from None

    @staticmethod
    def _storage_error():
        return GatewayError("PROVIDER_ATTEMPT_STORAGE_ERROR", "Provider receipt storage is unavailable.")

    @staticmethod
    def _enable_wal(db):
        # SQLite's journal-mode conversion can report BUSY immediately even
        # with busy_timeout, when several processes first open an older DB.
        deadline = time.monotonic() + 15
        while True:
            try:
                db.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as error:
                if (getattr(error, "sqlite_errorcode", None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                        or time.monotonic() >= deadline):
                    raise
                time.sleep(0.01)

    @staticmethod
    def _conflict():
        return GatewayError("PROVIDER_ATTEMPT_CONFLICT", "This job already has a provider attempt; do not resubmit.")

    @staticmethod
    def _ids(job_id, project_id):
        if not isinstance(job_id, UUID) or not isinstance(project_id, UUID):
            raise ValueError("Job and project identifiers must be UUID values.")

    @contextmanager
    def _connection(self, *, write=False):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
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

    def begin(self, job_id: UUID, project_id: UUID, request_hash: str, *,
              budget_limit_usd, reservation_usd, metadata: dict | None = None,
              account_reservation_id: str | None = None) -> UUID:
        self._ids(job_id, project_id)
        if not isinstance(request_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", request_hash):
            raise ValueError("A canonical request hash is required.")
        if (account_reservation_id is not None and
                (type(account_reservation_id) is not str
                 or not re.fullmatch(r"[a-f0-9]{64}", account_reservation_id))):
            raise ValueError("Invalid account reservation identifier.")
        encoded_metadata = self._metadata_json(metadata, request_hash)
        limit = money_units(budget_limit_usd)
        reservation = money_units(reservation_usd, positive=True)
        attempt_id = uuid4()
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connection(write=True) as db:
                self._check_experiment_slot(db, job_id, project_id, request_hash)
                if db.execute("SELECT 1 FROM provider_attempts WHERE job_id=?", (str(job_id),)).fetchone():
                    raise self._conflict()
                held = db.execute("SELECT COALESCE(SUM(reservation_units),0) FROM provider_attempts WHERE project_id=?",
                                  (str(project_id),)).fetchone()[0]
                if held + reservation > limit:
                    raise GatewayError("PROVIDER_BUDGET_EXCEEDED", "Image reservation allowance is exhausted.")
                db.execute("""INSERT INTO provider_attempts
                    (job_id,project_id,request_hash,attempt_id,state,reservation_units,created_at,updated_at,
                     metadata_json,account_reservation_id)
                    VALUES (?,?,?,?, 'submitting',?,?,?,?,?)""",
                           (str(job_id), str(project_id), request_hash, str(attempt_id), reservation, now, now,
                            encoded_metadata, account_reservation_id))
        except sqlite3.Error:
            raise self._storage_error() from None
        return attempt_id

    @staticmethod
    def _metadata_json(metadata, request_hash):
        if metadata is None:
            return None
        fields = {"operation", "model", "quality", "width", "height", "prompt_version", "request_hash"}
        if (type(metadata) is not dict or set(metadata) != fields
                or metadata["request_hash"] != request_hash
                or metadata["operation"] not in {"generate", "edit", "cutout"}
                or any(type(metadata[name]) is not int or not 1 <= metadata[name] <= 16384
                       for name in ("width", "height"))
                or any(type(metadata[name]) is not str or not 1 <= len(metadata[name]) <= 200
                       for name in ("model", "quality"))
                or (metadata["prompt_version"] is not None and
                    (type(metadata["prompt_version"]) is not str
                     or not 1 <= len(metadata["prompt_version"]) <= 200))):
            raise ValueError("Invalid provider observation metadata.")
        return json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def finish(self, job_id: UUID, project_id: UUID, attempt_id: UUID, *, state: str,
               provider_request_id: str | None = None, usage: ImageProviderUsage | None = None,
               result: ExternalImageResult | None = None, error_code: str | None = None,
               raw_usage: dict | None = None, elapsed_seconds: float | None = None):
        self._ids(job_id, project_id)
        if not isinstance(attempt_id, UUID) or state not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Invalid attempt completion.")
        if provider_request_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", provider_request_id):
            raise ValueError("Invalid provider request identifier.")
        if error_code is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error_code):
            raise ValueError("Invalid provider error code.")
        if state == "succeeded":
            if not isinstance(result, ExternalImageResult) or result.attempt_id != attempt_id or error_code is not None:
                raise ValueError("Successful attempts require their saved result.")
        elif result is not None or error_code is None:
            raise ValueError("Failed or unknown attempts require a safe error code, without a result.")
        if (elapsed_seconds is not None and
                (type(elapsed_seconds) not in (int, float) or not math.isfinite(elapsed_seconds)
                 or elapsed_seconds < 0)):
            raise ValueError("Invalid provider observation duration.")
        # Import at the observation boundary: account_budget imports the exact
        # reservation arithmetic above, while the adapter imports its permit.
        from .openai_images import OpenAIImagesAdapter, OpenAIImagesError, validate_raw_usage
        invalid_usage = False
        try:
            raw_usage = validate_raw_usage(raw_usage)
            OpenAIImagesAdapter._usage(raw_usage, provider_request_id)
            if usage is not None:
                usage = ImageProviderUsage.model_validate(usage.model_dump(warnings=False))
        except (OpenAIImagesError, ValueError, TypeError, AttributeError):
            invalid_usage = True
            state, error_code, result = "failed", "PROVIDER_USAGE_INVALID", None
            usage, raw_usage = None, None
        encoded_raw = (json.dumps(raw_usage, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False)
                       if raw_usage is not None else None)
        try:
            with self._connection(write=True) as db:
                count = db.execute("""UPDATE provider_attempts SET state=?,provider_request_id=?,usage_json=?,
                    result_json=?,error_code=?,updated_at=?,raw_usage_json=?,elapsed_seconds=?
                    WHERE job_id=? AND project_id=? AND attempt_id=? AND state='submitting'""",
                                   (state, provider_request_id, usage.model_dump_json() if usage else None,
                                    result.model_dump_json() if result else None, error_code,
                                    datetime.now(timezone.utc).isoformat(), encoded_raw, elapsed_seconds,
                                    str(job_id), str(project_id), str(attempt_id))).rowcount
                if count != 1:
                    raise self._conflict()
                if state in {"failed", "unknown"}:
                    db.execute("UPDATE experiment_limits SET state='halted' WHERE singleton=1")
        except sqlite3.Error:
            raise self._storage_error() from None
        if invalid_usage:
            raise GatewayError("PROVIDER_USAGE_INVALID", "Provider usage observation is invalid.")

    @staticmethod
    def _experiment_error(code: str):
        return GatewayError(code, "Experiment execution stopped; do not retry this plan.")

    def configure_experiment(self, *, plan_sha256: str, project_id: UUID,
                             jobs: dict[UUID, str], max_attempts: int) -> None:
        """Bind this dedicated ledger to an immutable plan before any attempt.

        Counts are reserved submission slots, not proof of HTTP delivery or
        confirmed charges. Failed and uncertain slots are never refunded.
        Ordinary service ledgers without this configuration remain unchanged.
        """
        if (type(plan_sha256) is not str or not re.fullmatch(r"[a-f0-9]{64}", plan_sha256)
                or not isinstance(project_id, UUID) or type(max_attempts) is not int
                or not 1 <= max_attempts <= 30 or type(jobs) is not dict or not 1 <= len(jobs) <= 30
                or any(not isinstance(job, UUID) or type(digest) is not str
                       or not re.fullmatch(r"[a-f0-9]{64}", digest) for job, digest in jobs.items())):
            raise ValueError("EXPERIMENT_LIMIT_CONFIG_INVALID")
        encoded = json.dumps({str(job): digest for job, digest in jobs.items()}, sort_keys=True)
        wanted = (plan_sha256, str(project_id), max_attempts, encoded)
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM experiment_limits WHERE singleton=1").fetchone()
                if row is not None:
                    found = (row['plan_sha256'], row['project_id'], row['max_attempts'], row['jobs_json'])
                    if found != wanted:
                        raise self._experiment_error("EXPERIMENT_PLAN_CONFLICT")
                    return
                if db.execute("SELECT 1 FROM provider_attempts LIMIT 1").fetchone():
                    raise self._experiment_error("EXPERIMENT_PLAN_CONFLICT")
                db.execute("""INSERT INTO experiment_limits
                    (singleton,plan_sha256,project_id,max_attempts,jobs_json,state)
                    VALUES (1,?,?,?,?, 'ready')""", wanted)
        except sqlite3.Error:
            raise self._storage_error() from None

    def claim_experiment(self, plan_sha256: str) -> None:
        """Claim once, atomically. A crash consumes the claim, even before POST."""
        try:
            with self._connection(write=True) as db:
                changed = db.execute("""UPDATE experiment_limits SET state='running'
                    WHERE singleton=1 AND plan_sha256=? AND state='ready'""", (plan_sha256,)).rowcount
                if changed != 1:
                    raise self._experiment_error("EXPERIMENT_ALREADY_CLAIMED_OR_MISMATCH")
        except sqlite3.Error:
            raise self._storage_error() from None

    def _check_experiment_slot(self, db, job_id: UUID, project_id: UUID, request_hash: str) -> None:
        row = db.execute("SELECT * FROM experiment_limits WHERE singleton=1").fetchone()
        if row is None:
            return
        if row['state'] != 'running':
            raise self._experiment_error("EXPERIMENT_NOT_RUNNING")
        if (row['project_id'] != str(project_id)
                or json.loads(row['jobs_json']).get(str(job_id)) != request_hash):
            raise self._experiment_error("EXPERIMENT_REQUEST_NOT_IN_PLAN")
        if db.execute("SELECT COUNT(*) FROM provider_attempts").fetchone()[0] >= row['max_attempts']:
            raise self._experiment_error("EXPERIMENT_CALL_LIMIT")
        # A pending attempt may already have reached the provider. Never pass it
        # by starting a later case, even after process restart.
        if db.execute("SELECT 1 FROM provider_attempts WHERE state!='succeeded' LIMIT 1").fetchone():
            raise self._experiment_error("EXPERIMENT_PREVIOUS_ATTEMPT_UNRESOLVED")

    def finish_experiment(self, plan_sha256: str, *, succeeded: bool) -> None:
        if type(succeeded) is not bool:
            raise ValueError("EXPERIMENT_FINISH_INVALID")
        try:
            with self._connection(write=True) as db:
                row = db.execute("SELECT * FROM experiment_limits WHERE singleton=1").fetchone()
                if row is None or row['plan_sha256'] != plan_sha256 or row['state'] == 'ready':
                    raise self._experiment_error("EXPERIMENT_PLAN_CONFLICT")
                if succeeded:
                    expected = len(json.loads(row['jobs_json']))
                    completed = db.execute("SELECT COUNT(*) FROM provider_attempts WHERE state='succeeded'").fetchone()[0]
                    if row['state'] not in {'running', 'succeeded'} or completed != expected:
                        raise self._experiment_error("EXPERIMENT_INCOMPLETE")
                elif row['state'] == 'succeeded':
                    raise self._experiment_error("EXPERIMENT_PLAN_CONFLICT")
                db.execute("UPDATE experiment_limits SET state=? WHERE singleton=1",
                           ('succeeded' if succeeded else 'halted',))
        except sqlite3.Error:
            raise self._storage_error() from None

    def experiment_status(self) -> dict | None:
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM experiment_limits WHERE singleton=1").fetchone()
                if row is None:
                    return None
                return {key: row[key] for key in ('plan_sha256', 'project_id', 'max_attempts', 'state')} | {
                    'attempt_slots': db.execute("SELECT COUNT(*) FROM provider_attempts").fetchone()[0]}
        except sqlite3.Error:
            raise self._storage_error() from None

    def get(self, job_id: UUID, project_id: UUID) -> dict | None:
        self._ids(job_id, project_id)
        try:
            with self._connection() as db:
                row = db.execute("SELECT * FROM provider_attempts WHERE job_id=? AND project_id=?",
                                 (str(job_id), str(project_id))).fetchone()
            if row is None:
                return None
            result = ExternalImageResult.model_validate_json(row["result_json"]) if row["result_json"] else None
            usage = ImageProviderUsage.model_validate_json(row["usage_json"]) if row["usage_json"] else None
            return {
                "job_id": row["job_id"], "attempt_id": row["attempt_id"], "request_hash": row["request_hash"],
                "state": row["state"], "provider": "openai", "provider_request_id": row["provider_request_id"],
                "reservation_usd": format(Decimal(row["reservation_units"]) / 1_000_000, ".6f"),
                "billing_status": "UNRECONCILED", "actual_cost_usd": None,
                "submission_status": "UNKNOWN" if row["state"] in {"submitting", "unknown"} else "RESPONSE_RECEIVED",
                "usage": usage.model_dump(mode="json") if usage else None,
                "raw_usage": json.loads(row["raw_usage_json"]) if row["raw_usage_json"] else None,
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
                "elapsed_seconds": row["elapsed_seconds"],
                "account_reservation_id": row["account_reservation_id"],
                "result": result.model_dump(mode="json") if result else None,
                "error_code": row["error_code"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            }
        except (sqlite3.Error, ValueError, TypeError):
            raise self._storage_error() from None

    def record_reuse(self, job_id: UUID, project_id: UUID, request_hash: str) -> str:
        """Observe a verified receipt lookup, without adding an attempt or cost."""
        self._ids(job_id, project_id)
        event_id = str(uuid4())
        try:
            with self._connection(write=True) as db:
                row = db.execute("""SELECT attempt_id,result_json FROM provider_attempts
                    WHERE job_id=? AND project_id=? AND request_hash=? AND state='succeeded'""",
                                 (str(job_id), str(project_id), request_hash)).fetchone()
                if row is None or row["result_json"] is None:
                    raise self._conflict()
                result = ExternalImageResult.model_validate_json(row["result_json"])
                if str(result.attempt_id) != row["attempt_id"]:
                    raise self._conflict()
                db.execute("""INSERT INTO provider_reuse_events
                    (event_id,job_id,project_id,attempt_id,request_hash,created_at) VALUES (?,?,?,?,?,?)""",
                           (event_id, str(job_id), str(project_id), row["attempt_id"], request_hash,
                            datetime.now(timezone.utc).isoformat()))
        except (sqlite3.Error, ValueError, TypeError):
            raise self._storage_error() from None
        return event_id

    def get_reuse_events(self, job_id: UUID, project_id: UUID) -> list[dict]:
        self._ids(job_id, project_id)
        try:
            with self._connection() as db:
                rows = db.execute("""SELECT event_id,job_id,project_id,attempt_id,request_hash,created_at
                    FROM provider_reuse_events WHERE job_id=? AND project_id=? ORDER BY created_at,event_id""",
                                  (str(job_id), str(project_id))).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            raise self._storage_error() from None
