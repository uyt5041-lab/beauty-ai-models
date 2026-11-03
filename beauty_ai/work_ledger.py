"""Project-scoped, durable advertisement work and immutable receipt ledger.

Each mutation is one short SQLite transaction. Provider calls and artifact IO
belong to callers and never run while this ledger holds its write lock.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import time

from .errors import GatewayError
from . import work_contracts as contracts


_SCHEMA_VERSION = 1
_CODE = re.compile(r"WORK_[A-Z_]+\Z")


def _error(code: str) -> GatewayError:
    return GatewayError(code, "Advertisement work operation could not be completed.",
                        retryable=code == "WORK_STORAGE_ERROR")


def _validate(function, *args):
    try:
        return function(*args)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        code = str(exc)
        raise _error(code if _CODE.fullmatch(code) else "WORK_INVALID") from None


def _json(value) -> str:
    return contracts.canonical_bytes(value).decode("utf-8")


def _decode(value: str, normalizer):
    try:
        return normalizer(json.loads(value))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise _error("WORK_STORAGE_ERROR") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class AdWorkLedger:
    """Durable work ownership, expected jobs, attempts, and first receipts."""

    def __init__(self, path: str | Path):
        try:
            source = Path(path).expanduser()
            if str(path) == ":memory:" or source.is_symlink():
                raise _error("WORK_STORAGE_ERROR")
            self.path = source.resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection() as db:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        db.execute("PRAGMA journal_mode=WAL")
                        break
                    except sqlite3.OperationalError as exc:
                        if (getattr(exc, "sqlite_errorcode", None) not in
                                {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                                or time.monotonic() >= deadline):
                            raise
                        time.sleep(0.01)
            with self._connection(write=True) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, _SCHEMA_VERSION):
                    raise _error("WORK_STORAGE_ERROR")
                db.execute("""CREATE TABLE IF NOT EXISTS ad_works (
                    project_id TEXT NOT NULL, work_id TEXT NOT NULL,
                    task_pair_id TEXT, arm TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(project_id,work_id))""")
                db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ad_work_pair
                    ON ad_works(project_id,task_pair_id,arm)
                    WHERE task_pair_id IS NOT NULL""")
                db.execute("""CREATE TABLE IF NOT EXISTS ad_attempts (
                    project_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    work_id TEXT NOT NULL, operation TEXT NOT NULL,
                    provider TEXT NOT NULL, request_hash TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(project_id,attempt_id),
                    FOREIGN KEY(project_id,work_id) REFERENCES ad_works(project_id,work_id))""")
                db.execute("""CREATE INDEX IF NOT EXISTS ad_attempt_work
                    ON ad_attempts(project_id,work_id)""")
                db.execute("""CREATE TABLE IF NOT EXISTS ad_receipts (
                    project_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    work_id TEXT NOT NULL, provider TEXT NOT NULL,
                    provider_request_id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(project_id,attempt_id),
                    FOREIGN KEY(project_id,attempt_id) REFERENCES ad_attempts(project_id,attempt_id))""")
                db.execute("""CREATE INDEX IF NOT EXISTS ad_receipt_work
                    ON ad_receipts(project_id,work_id)""")
                db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ad_provider_request
                    ON ad_receipts(project_id,provider,provider_request_id)
                    WHERE provider_request_id IS NOT NULL""")
                db.execute("""CREATE TABLE IF NOT EXISTS ad_image_bindings (
                    project_id TEXT NOT NULL, job_id TEXT NOT NULL,
                    work_id TEXT NOT NULL, attempt_id TEXT,
                    PRIMARY KEY(project_id,job_id),
                    FOREIGN KEY(project_id,work_id) REFERENCES ad_works(project_id,work_id),
                    FOREIGN KEY(project_id,attempt_id) REFERENCES ad_attempts(project_id,attempt_id))""")
                db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ad_bound_attempt
                    ON ad_image_bindings(project_id,attempt_id) WHERE attempt_id IS NOT NULL""")
                db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise _error("WORK_STORAGE_ERROR") from None

    @contextmanager
    def _connection(self, *, write: bool = False):
        db = None
        try:
            db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except (OSError, sqlite3.Error):
            if db is not None and db.in_transaction:
                db.rollback()
            raise _error("WORK_STORAGE_ERROR") from None
        except BaseException:
            if db is not None and db.in_transaction:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _ids(project_id, work_id):
        return (_validate(contracts.normalize_uuid, project_id),
                _validate(contracts.normalize_uuid, work_id))

    @staticmethod
    def _work(db, project_id, work_id):
        row = db.execute("SELECT data FROM ad_works WHERE project_id=? AND work_id=?",
                         (project_id, work_id)).fetchone()
        if row is None:
            raise _error("WORK_NOT_FOUND")
        work = _decode(row["data"], contracts.normalize_work)
        if work["project_id"] != project_id or work["work_id"] != work_id:
            raise _error("WORK_STORAGE_ERROR")
        return work

    @staticmethod
    def _open(work):
        if work["status"] != "OPEN":
            raise _error("WORK_CLOSED")

    def create_work(self, project_id, work_id, product_ref, task_pair_id=None,
                    arm=None, required_stages=None) -> dict:
        project_id, work_id = self._ids(project_id, work_id)
        spec = _validate(contracts.normalize_create_spec, {
            "product_ref": product_ref, "task_pair_id": task_pair_id,
            "arm": arm, "required_stages": required_stages})
        with self._connection(write=True) as db:
            row = db.execute("SELECT data FROM ad_works WHERE project_id=? AND work_id=?",
                             (project_id, work_id)).fetchone()
            if row is not None:
                work = _decode(row["data"], contracts.normalize_work)
                if any(work[key] != value for key, value in spec.items()):
                    raise _error("WORK_SPEC_CONFLICT")
                return work
            if spec["task_pair_id"] is not None and db.execute(
                    "SELECT 1 FROM ad_works WHERE project_id=? AND task_pair_id=? AND arm IS ?",
                    (project_id, spec["task_pair_id"], spec["arm"])).fetchone():
                raise _error("WORK_PAIR_CONFLICT")
            work = _validate(contracts.normalize_work, {
                "project_id": project_id, "work_id": work_id, **spec,
                "status": "OPEN", "created_at": _now(), "closed_at": None,
                "completion": None})
            db.execute("INSERT INTO ad_works VALUES (?,?,?,?,?)", (
                project_id, work_id, spec["task_pair_id"], spec["arm"], _json(work)))
            return work

    def get_work(self, project_id, work_id) -> dict:
        project_id, work_id = self._ids(project_id, work_id)
        with self._connection() as db:
            return self._work(db, project_id, work_id)

    def require_open(self, project_id, work_id) -> dict:
        work = self.get_work(project_id, work_id)
        self._open(work)
        return work

    @staticmethod
    def _attempt_spec(identity):
        return {key: value for key, value in identity.items()
                if key not in {"project_id", "work_id", "started_at"}}

    def _start(self, db, project_id, work_id, spec, *, allow_closed=False, started_at=None):
        work = self._work(db, project_id, work_id)
        row = db.execute("SELECT work_id,data FROM ad_attempts WHERE project_id=? AND attempt_id=?",
                         (project_id, spec["attempt_id"])).fetchone()
        if row is not None:
            identity = _decode(row["data"], contracts.normalize_attempt)
            if row["work_id"] != work_id or self._attempt_spec(identity) != spec:
                raise _error("WORK_ATTEMPT_CONFLICT")
            return
        if not allow_closed:
            self._open(work)
        parent = None
        if spec["parent_attempt_id"] is not None:
            row = db.execute("SELECT data FROM ad_attempts WHERE project_id=? AND work_id=? AND attempt_id=?",
                             (project_id, work_id, spec["parent_attempt_id"])).fetchone()
            if row is None:
                raise _error("WORK_PARENT_CONFLICT")
            parent = _decode(row["data"], contracts.normalize_attempt)
        count = db.execute("SELECT count(*) FROM ad_attempts WHERE project_id=? AND work_id=?",
                           (project_id, work_id)).fetchone()[0]
        if count >= contracts.MAX_ATTEMPTS_PER_WORK:
            raise _error("WORK_LIMIT")
        if spec["provider"] != "local" and spec["stage"] != "image_generation":
            for pending in db.execute("""SELECT a.data,r.data AS receipt FROM ad_attempts a
                    LEFT JOIN ad_receipts r USING(project_id,attempt_id)
                    WHERE a.project_id=? AND a.work_id=? AND a.operation=? AND a.request_hash=?""",
                    (project_id, work_id, spec["operation"], spec["request_hash"])):
                receipt = (None if pending["receipt"] is None else
                           _decode(pending["receipt"], contracts.normalize_receipt))
                if receipt is None or receipt["status"] == "UNKNOWN":
                    raise _error("WORK_PENDING")
        identity = _validate(contracts.normalize_attempt, {
            **spec, "project_id": project_id, "work_id": work_id,
            "started_at": started_at if started_at is not None else _now()})
        if datetime.fromisoformat(identity["started_at"]) < datetime.fromisoformat(work["created_at"]):
            raise _error("WORK_TIMESTAMP_INVALID")
        if parent is not None and (datetime.fromisoformat(parent["started_at"]) >
                                   datetime.fromisoformat(identity["started_at"])):
            raise _error("WORK_PARENT_CONFLICT")
        db.execute("INSERT INTO ad_attempts VALUES (?,?,?,?,?,?,?)", (
            project_id, spec["attempt_id"], work_id, spec["operation"], spec["provider"],
            spec["request_hash"], _json(identity)))

    def start_attempt(self, project_id, work_id, attempt_id, stage, operation, provider,
                      configured_model, prompt_version, request_hash, parent_attempt_id=None) -> None:
        project_id, work_id = self._ids(project_id, work_id)
        spec = _validate(contracts.normalize_attempt_spec, {
            "attempt_id": _validate(contracts.normalize_uuid, attempt_id),
            "stage": stage, "operation": operation,
            "provider": provider, "configured_model": configured_model,
            "prompt_version": prompt_version, "request_hash": request_hash,
            "parent_attempt_id": (None if parent_attempt_id is None else
                                  _validate(contracts.normalize_uuid, parent_attempt_id))})
        with self._connection(write=True) as db:
            self._open(self._work(db, project_id, work_id))
            self._start(db, project_id, work_id, spec)

    def _record(self, db, project_id, work_id, receipt):
        work = self._work(db, project_id, work_id)
        row = db.execute("SELECT work_id,data FROM ad_attempts WHERE project_id=? AND attempt_id=?",
                         (project_id, receipt["attempt_id"])).fetchone()
        if row is None or row["work_id"] != work_id:
            raise _error("WORK_NOT_FOUND")
        existing = db.execute("SELECT data FROM ad_receipts WHERE project_id=? AND attempt_id=?",
                              (project_id, receipt["attempt_id"])).fetchone()
        if existing is not None:
            if _decode(existing["data"], contracts.normalize_receipt) != receipt:
                raise _error("WORK_RECEIPT_CONFLICT")
            return
        if work["status"] == "COMPLETED":
            raise _error("WORK_CLOSED")
        identity = _decode(row["data"], contracts.normalize_attempt)
        if identity["provider"] == "local":
            usage = receipt["usage"]
            if (receipt["model"] != "local" or usage is None or
                    usage.get("input_tokens") != 0 or usage.get("output_tokens") != 0 or
                    any(value not in (None, 0) for value in usage.values()) or
                    receipt["estimated_cost_usd"] != "0.000000000" or
                    receipt["rate_version"] != "local-api-zero-v1"):
                raise _error("WORK_RECEIPT_INVALID")
        elif receipt["model"] == "local":
            raise _error("WORK_RECEIPT_INVALID")
        rid = receipt["provider_request_id"]
        if rid is not None and db.execute("""SELECT 1 FROM ad_receipts
                WHERE project_id=? AND provider=? AND provider_request_id=?""",
                (project_id, identity["provider"], rid)).fetchone():
            raise _error("WORK_PROVIDER_CONFLICT")
        db.execute("INSERT INTO ad_receipts VALUES (?,?,?,?,?,?)", (
            project_id, receipt["attempt_id"], work_id, identity["provider"], rid, _json(receipt)))

    def record_receipt(self, project_id, work_id, receipt) -> None:
        project_id, work_id = self._ids(project_id, work_id)
        receipt = _validate(contracts.normalize_receipt, receipt)
        with self._connection(write=True) as db:
            self._record(db, project_id, work_id, receipt)

    def bind_image_job(self, project_id, work_id, job_id) -> None:
        project_id, work_id = self._ids(project_id, work_id)
        job_id = _validate(contracts.normalize_uuid, job_id)
        with self._connection(write=True) as db:
            work = self._work(db, project_id, work_id)
            row = db.execute("SELECT work_id FROM ad_image_bindings WHERE project_id=? AND job_id=?",
                             (project_id, job_id)).fetchone()
            if row is not None:
                if row["work_id"] != work_id:
                    raise _error("WORK_BINDING_CONFLICT")
                return
            self._open(work)
            db.execute("INSERT INTO ad_image_bindings VALUES (?,?,?,NULL)",
                       (project_id, job_id, work_id))

    def get_image_binding(self, project_id, job_id) -> str | None:
        project_id, job_id = self._ids(project_id, job_id)
        with self._connection() as db:
            row = db.execute("SELECT work_id FROM ad_image_bindings WHERE project_id=? AND job_id=?",
                             (project_id, job_id)).fetchone()
            return row["work_id"] if row is not None else None

    def sync_image_attempt(self, project_id, job_id, identity, receipt_or_none) -> None:
        project_id, job_id = self._ids(project_id, job_id)
        if type(identity) is not dict:
            raise _error("WORK_INVALID")
        full = "started_at" in identity or "project_id" in identity or "work_id" in identity
        identity = _validate(contracts.normalize_attempt if full else
                             contracts.normalize_attempt_spec, identity)
        receipt = (None if receipt_or_none is None else
                   _validate(contracts.normalize_receipt, receipt_or_none))
        if identity["stage"] != "image_generation" or identity["provider"] == "local" or (receipt is not None and
                receipt["attempt_id"] != identity["attempt_id"]):
            raise _error("WORK_BINDING_CONFLICT")
        with self._connection(write=True) as db:
            binding = db.execute("SELECT work_id,attempt_id FROM ad_image_bindings WHERE project_id=? AND job_id=?",
                                 (project_id, job_id)).fetchone()
            if binding is None:
                raise _error("WORK_NOT_FOUND")
            work_id = binding["work_id"]
            if full and (identity["project_id"] != project_id or identity["work_id"] != work_id):
                raise _error("WORK_BINDING_CONFLICT")
            if binding["attempt_id"] not in (None, identity["attempt_id"]):
                raise _error("WORK_BINDING_CONFLICT")
            work = self._work(db, project_id, work_id)
            if work["status"] == "COMPLETED" and binding["attempt_id"] is None:
                raise _error("WORK_CLOSED")
            other = db.execute("SELECT job_id FROM ad_image_bindings WHERE project_id=? AND attempt_id=?",
                               (project_id, identity["attempt_id"])).fetchone()
            if other is not None and other["job_id"] != job_id:
                raise _error("WORK_BINDING_CONFLICT")
            self._start(db, project_id, work_id, self._attempt_spec(identity),
                        allow_closed=work["status"] == "CANCELLED",
                        started_at=identity.get("started_at"))
            db.execute("UPDATE ad_image_bindings SET attempt_id=? WHERE project_id=? AND job_id=?",
                       (identity["attempt_id"], project_id, job_id))
            if receipt is not None:
                self._record(db, project_id, work_id, receipt)

    def complete(self, project_id, work_id, completion) -> None:
        project_id, work_id = self._ids(project_id, work_id)
        completion = _validate(contracts.normalize_completion, completion)
        with self._connection(write=True) as db:
            work = self._work(db, project_id, work_id)
            if work["status"] == "COMPLETED" and work["completion"] == completion:
                return
            self._open(work)
            rows = db.execute("""SELECT a.data,r.data AS receipt FROM ad_attempts a
                LEFT JOIN ad_receipts r USING(project_id,attempt_id)
                WHERE a.project_id=? AND a.work_id=?""", (project_id, work_id)).fetchall()
            identities = [_decode(row["data"], contracts.normalize_attempt) for row in rows]
            if set(completion["expected_attempt_ids"]) != {item["attempt_id"] for item in identities}:
                raise _error("WORK_COMPLETION_INVALID")
            if any(row["receipt"] is None for row in rows) or db.execute("""SELECT 1
                    FROM ad_image_bindings b LEFT JOIN ad_receipts r
                    ON r.project_id=b.project_id AND r.attempt_id=b.attempt_id
                    WHERE b.project_id=? AND b.work_id=? AND r.attempt_id IS NULL LIMIT 1""",
                    (project_id, work_id)).fetchone():
                raise _error("WORK_PENDING")
            receipts = [_decode(row["receipt"], contracts.normalize_receipt) for row in rows]
            successful = [(identity, receipt) for identity, receipt in zip(identities, receipts, strict=True)
                          if receipt["status"] == "SUCCEEDED"]
            if not set(work["required_stages"]).issubset({item[0]["stage"] for item in successful}):
                raise _error("WORK_MISSING_STAGE")
            if not any(identity["stage"] in {"image_generation", "composition"} and
                       receipt["artifact_sha256"] == completion["artifact_sha256"]
                       for identity, receipt in successful):
                raise _error("WORK_COMPLETION_INVALID")
            work.update(status="COMPLETED", closed_at=_now(), completion=completion)
            db.execute("UPDATE ad_works SET data=? WHERE project_id=? AND work_id=?",
                       (_json(work), project_id, work_id))

    def cancel(self, project_id, work_id) -> None:
        project_id, work_id = self._ids(project_id, work_id)
        with self._connection(write=True) as db:
            work = self._work(db, project_id, work_id)
            self._open(work)
            work.update(status="CANCELLED", closed_at=_now())
            db.execute("UPDATE ad_works SET data=? WHERE project_id=? AND work_id=?",
                       (_json(work), project_id, work_id))

    def export_dataset(self, project_id, rate_book=None, data_kind="DECLARED_DATA") -> dict:
        project_id = _validate(contracts.normalize_uuid, project_id)
        rate_book = _validate(contracts.normalize_rate_book, rate_book)
        with self._connection() as db:
            db.execute("BEGIN")
            return _export(db, project_id, rate_book, data_kind)


def _export(db, project_id, rate_book, data_kind):
    if db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
        raise _error("WORK_STORAGE_ERROR")
    work_count = db.execute("SELECT count(*) FROM ad_works WHERE project_id=?", (project_id,)).fetchone()[0]
    receipt_count = db.execute("SELECT count(*) FROM ad_receipts WHERE project_id=?", (project_id,)).fetchone()[0]
    if work_count > contracts.MAX_WORKS or receipt_count > contracts.MAX_RECEIPTS:
        raise _error("WORK_LIMIT")
    def items(table, order, normalizer):
        return [_decode(row["data"], normalizer) for row in db.execute(
            f"SELECT data FROM {table} WHERE project_id=? ORDER BY {order}", (project_id,))]
    dataset = {"schema_version": contracts.DATASET_SCHEMA_VERSION, "data_kind": data_kind,
               "works": items("ad_works", "work_id", contracts.normalize_work),
               "attempts": items("ad_attempts", "attempt_id", contracts.normalize_attempt),
               "receipts": items("ad_receipts", "attempt_id", contracts.normalize_receipt),
               "image_bindings": [dict(row) for row in db.execute("""SELECT project_id,work_id,job_id,attempt_id
                    FROM ad_image_bindings WHERE project_id=? ORDER BY job_id""", (project_id,))],
               "rate_book": rate_book}
    if any(work["project_id"] != project_id for work in dataset["works"]):
        raise _error("WORK_STORAGE_ERROR")
    return _validate(contracts.normalize_work_dataset, dataset)


def read_work_dataset(path: str | Path, project_id, rate_book=None,
                      data_kind="DECLARED_DATA") -> dict:
    """Read an existing ledger snapshot without creating tables or migrations."""
    project_id = _validate(contracts.normalize_uuid, project_id)
    rate_book = _validate(contracts.normalize_rate_book, rate_book)
    db = None
    try:
        source = Path(path).expanduser()
        if source.is_symlink() or not source.is_file():
            raise _error("WORK_STORAGE_ERROR")
        db = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True,
                             isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return _export(db, project_id, rate_book, data_kind)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise _error("WORK_STORAGE_ERROR") from None
    finally:
        if db is not None:
            db.close()
