"""Single-host account allowances, independent of experiment attempt ledgers.

This ledger covers declared opening spend and calls passing through its gate.
Reservations are allowances, never a guarantee about the provider's invoice.
No failure, restart, or period rollover automatically releases a reservation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable
from uuid import UUID

from .errors import GatewayError
from .provider_attempts import money_units


SCHEMA_VERSION = "beautyai-account-budget-v1"
_NANOS = 1_000_000_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,9})?\Z")


def _error(reason: str) -> GatewayError:
    code = "ACCOUNT_BUDGET_" + reason
    return GatewayError(code, code)


def _identifier(value, reason="CONFIG_INVALID") -> str:
    if isinstance(value, UUID):
        value = str(value)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise _error(reason)
    return value


def _digest(value) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise _error("RESERVATION_CONFLICT")
    return value


def _nanos(value: str) -> int:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise _error("CONFIG_INVALID")
    whole, _, fraction = value.partition(".")
    amount = int(whole) * _NANOS + int(fraction.ljust(9, "0"))
    if amount > 1_000_000_000 * _NANOS:
        raise _error("CONFIG_INVALID")
    return amount


def _usd(nanos: int) -> str:
    # Integer formatting keeps all nine places independent of Decimal context.
    sign = "-" if nanos < 0 else ""
    whole, fraction = divmod(abs(nanos), _NANOS)
    return f"{sign}{whole}.{fraction:09d}"


def _datetime(value: str) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise _error("CONFIG_INVALID") from None


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class AccountBudgetPolicy:
    account_ref: str
    period_id: str
    starts_at: str
    ends_at: str
    hard_limit_usd: str
    opening_spend_usd: str
    opening_evidence_ref: str
    warning_ratio: str = "0.8"
    stop_ratio: str = "0.9"
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        for value in (self.account_ref, self.period_id, self.opening_evidence_ref):
            if not isinstance(value, str):
                raise _error("CONFIG_INVALID")
            _identifier(value)
        if self.schema_version != SCHEMA_VERSION or _datetime(self.starts_at) >= _datetime(self.ends_at):
            raise _error("CONFIG_INVALID")
        _nanos(self.hard_limit_usd)
        _nanos(self.opening_spend_usd)
        warning, stop = _nanos(self.warning_ratio), _nanos(self.stop_ratio)
        if not 0 <= warning <= stop <= _NANOS:
            raise _error("CONFIG_INVALID")


@dataclass(frozen=True)
class BudgetPermit:
    """A bound capability whose only successful claim is persisted atomically."""

    _store: AccountBudgetStore = field(repr=False, compare=False)
    account_ref: str
    reservation_id: str
    request_hash: str
    attempt_id: str

    def claim(self, request_hash, attempt_id) -> None:
        self._store._claim(self, request_hash, attempt_id)


class AccountBudgetStore:
    def __init__(self, path: str | Path, policy: AccountBudgetPolicy, *,
                 clock: Callable[[], datetime] | None = None):
        if not isinstance(policy, AccountBudgetPolicy):
            raise _error("CONFIG_INVALID")
        self.policy = policy
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        try:
            self.path = Path(path).resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, TypeError, ValueError):
            raise _error("STORAGE_ERROR") from None
        self.configure()

    @contextmanager
    def _connection(self, *, write=False):
        db = None
        try:
            db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except (sqlite3.Error, OSError):
            raise _error("STORAGE_ERROR") from None
        finally:
            if db is not None:
                try:
                    try:
                        if db.in_transaction:
                            db.rollback()
                    finally:
                        db.close()
                except sqlite3.Error:
                    raise _error("STORAGE_ERROR") from None

    def configure(self) -> None:
        """Register this bound policy once; overlapping or changed policy fails."""
        policy = self.policy
        encoded = json.dumps(asdict(policy), sort_keys=True, separators=(",", ":"))
        start, end = _stamp(_datetime(policy.starts_at)), _stamp(_datetime(policy.ends_at))
        with self._connection(write=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                          "name IN ('provider_attempts','experiment_limits')").fetchone():
                raise _error("CONFIG_INVALID")
            db.execute("""CREATE TABLE IF NOT EXISTS budgets (
                account_ref TEXT NOT NULL, period_id TEXT NOT NULL, policy_json TEXT NOT NULL,
                starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
                PRIMARY KEY(account_ref,period_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY, account_ref TEXT NOT NULL,
                period_id TEXT NOT NULL, project_id TEXT NOT NULL, job_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, reservation_units INTEGER NOT NULL CHECK(reservation_units>0),
                state TEXT NOT NULL CHECK(state IN ('HELD','SUBMITTED','SETTLED')),
                attempt_id TEXT, created_at TEXT NOT NULL, submitted_at TEXT,
                UNIQUE(account_ref,project_id,job_id), UNIQUE(account_ref,attempt_id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS settlements (
                settlement_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL,
                account_ref TEXT NOT NULL, period_id TEXT NOT NULL,
                actual_cost_nanos INTEGER NOT NULL CHECK(actual_cost_nanos>=0),
                delta_nanos INTEGER NOT NULL, evidence_sha256 TEXT NOT NULL,
                supersedes_id TEXT UNIQUE, created_at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS budget_reservations ON reservations(account_ref,state)")
            db.execute("CREATE INDEX IF NOT EXISTS budget_settlements ON settlements(account_ref,period_id)")
            db.execute("CREATE INDEX IF NOT EXISTS reservation_settlements ON settlements(reservation_id)")
            found = db.execute("SELECT policy_json FROM budgets WHERE account_ref=? AND period_id=?",
                               (policy.account_ref, policy.period_id)).fetchone()
            if found:
                if found[0] != encoded:
                    raise _error("POLICY_CONFLICT")
                return
            if db.execute("SELECT 1 FROM budgets WHERE account_ref=? AND starts_at<? AND ends_at>?",
                          (policy.account_ref, end, start)).fetchone():
                raise _error("POLICY_CONFLICT")
            db.execute("INSERT INTO budgets VALUES (?,?,?,?,?)",
                       (policy.account_ref, policy.period_id, encoded, start, end))

    def _active(self) -> str:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise _error("CONFIG_INVALID")
        if not _datetime(self.policy.starts_at) <= now < _datetime(self.policy.ends_at):
            raise _error("POLICY_CONFLICT")
        return _stamp(now)

    @staticmethod
    def _latest(db, reservation_id):
        return db.execute("""SELECT s.* FROM settlements s WHERE reservation_id=?
            AND NOT EXISTS (SELECT 1 FROM settlements next WHERE next.supersedes_id=s.settlement_id)""",
                          (reservation_id,)).fetchone()

    @staticmethod
    def _totals(db, policy):
        account = policy.account_ref
        # Python integers avoid SQLite SUM overflow after many exact postings.
        delta = sum(row[0] for row in db.execute(
            "SELECT delta_nanos FROM settlements WHERE account_ref=? AND period_id=?",
            (account, policy.period_id)))
        held = sum(row[0] for row in db.execute(
            "SELECT reservation_units FROM reservations WHERE account_ref=? AND state IN ('HELD','SUBMITTED')",
            (account,))) * 1000
        confirmed = _nanos(policy.opening_spend_usd) + delta
        raw = max(0, confirmed) + held
        calculated = ((raw + 999) // 1000) * 1000
        overrun = db.execute("""SELECT 1 FROM reservations r JOIN settlements s
            ON s.reservation_id=r.reservation_id WHERE r.account_ref=?
            AND s.actual_cost_nanos>r.reservation_units*1000
            AND NOT EXISTS (SELECT 1 FROM settlements next WHERE next.supersedes_id=s.settlement_id)
            LIMIT 1""", (account,)).fetchone() is not None
        limit = _nanos(policy.hard_limit_usd)
        warning = calculated * _NANOS >= limit * _nanos(policy.warning_ratio)
        exceeded = overrun or calculated * _NANOS > limit * _nanos(policy.stop_ratio)
        return delta, held, confirmed, calculated, warning, exceeded, overrun

    def reserve(self, project_id, job_id, request_hash, amount_usd) -> str:
        project = _identifier(project_id, "RESERVATION_CONFLICT")
        job = _identifier(job_id, "RESERVATION_CONFLICT")
        request_hash = _digest(request_hash)
        try:
            with localcontext() as context:
                context.prec = 40
                units = money_units(amount_usd, positive=True)
        except (TypeError, ValueError):
            raise _error("CONFIG_INVALID") from None
        identity = json.dumps([self.policy.account_ref, project, job], separators=(",", ":"))
        reservation = sha256(identity.encode("utf-8")).hexdigest()
        with self._connection(write=True) as db:
            now = self._active()
            # A repeated reservation never provides another transmission permit.
            if db.execute("SELECT 1 FROM reservations WHERE reservation_id=?", (reservation,)).fetchone():
                raise _error("RESERVATION_CONFLICT")
            totals = self._totals(db, self.policy)
            if (totals[5] or (totals[3] + units * 1000) * _NANOS
                    > _nanos(self.policy.hard_limit_usd) * _nanos(self.policy.stop_ratio)):
                raise _error("EXCEEDED")
            db.execute("""INSERT INTO reservations
                (reservation_id,account_ref,period_id,project_id,job_id,request_hash,reservation_units,state,created_at)
                VALUES (?,?,?,?,?,?,?,'HELD',?)""",
                       (reservation, self.policy.account_ref, self.policy.period_id,
                        project, job, request_hash, units, now))
        return reservation

    def _held(self, db, reservation_id):
        row = db.execute("SELECT * FROM reservations WHERE reservation_id=? AND account_ref=?",
                         (reservation_id, self.policy.account_ref)).fetchone()
        if row is None or row["state"] != "HELD" or row["period_id"] != self.policy.period_id:
            raise _error("RESERVATION_CONFLICT")
        return row

    def bind_attempt(self, reservation_id, attempt_id) -> BudgetPermit:
        reservation_id = _digest(reservation_id)
        attempt = _identifier(attempt_id, "RESERVATION_CONFLICT")
        with self._connection(write=True) as db:
            self._active()
            row = self._held(db, reservation_id)
            if row["attempt_id"] is not None and row["attempt_id"] != attempt:
                raise _error("RESERVATION_CONFLICT")
            if db.execute("SELECT 1 FROM reservations WHERE account_ref=? AND attempt_id=? AND reservation_id<>?",
                          (self.policy.account_ref, attempt, reservation_id)).fetchone():
                raise _error("RESERVATION_CONFLICT")
            db.execute("UPDATE reservations SET attempt_id=? WHERE reservation_id=?", (attempt, reservation_id))
        return BudgetPermit(self, self.policy.account_ref, reservation_id, row["request_hash"], attempt)

    def _claim(self, permit, request_hash, attempt_id) -> None:
        request_hash = _digest(request_hash)
        attempt = _identifier(attempt_id, "RESERVATION_CONFLICT")
        with self._connection(write=True) as db:
            now = self._active()
            row = self._held(db, permit.reservation_id)
            if (permit.account_ref != self.policy.account_ref or permit.request_hash != request_hash
                    or permit.attempt_id != attempt or row["request_hash"] != request_hash
                    or row["attempt_id"] != attempt):
                raise _error("RESERVATION_CONFLICT")
            db.execute("UPDATE reservations SET state='SUBMITTED',submitted_at=? WHERE reservation_id=?",
                       (now, permit.reservation_id))

    def settle(self, reservation_id, settlement_id, actual_cost_usd, evidence_sha256,
               supersedes_id=None) -> None:
        reservation_id = _digest(reservation_id)
        settlement = _identifier(settlement_id, "RESERVATION_CONFLICT")
        evidence = _digest(evidence_sha256)
        supersedes = None if supersedes_id is None else _identifier(supersedes_id, "RESERVATION_CONFLICT")
        actual = _nanos(actual_cost_usd)
        with self._connection(write=True) as db:
            existing = db.execute("SELECT * FROM settlements WHERE settlement_id=?", (settlement,)).fetchone()
            wanted = (reservation_id, self.policy.account_ref, actual, evidence, supersedes)
            if existing:
                found = tuple(existing[key] for key in
                              ("reservation_id", "account_ref", "actual_cost_nanos", "evidence_sha256", "supersedes_id"))
                if found != wanted:
                    raise _error("RESERVATION_CONFLICT")
                return
            now = self._active()
            row = db.execute("SELECT * FROM reservations WHERE reservation_id=? AND account_ref=?",
                             (reservation_id, self.policy.account_ref)).fetchone()
            if row is None or (row["submitted_at"] is None and actual != 0):
                raise _error("RESERVATION_CONFLICT")
            latest = self._latest(db, reservation_id)
            if supersedes != (latest["settlement_id"] if latest else None):
                raise _error("RESERVATION_CONFLICT")
            if db.execute("""SELECT 1 FROM settlements WHERE evidence_sha256=?
                AND (reservation_id<>? OR actual_cost_nanos<>?)""",
                          (evidence, reservation_id, actual)).fetchone():
                raise _error("RESERVATION_CONFLICT")
            delta = actual - (latest["actual_cost_nanos"] if latest else 0)
            db.execute("""INSERT INTO settlements
                (settlement_id,reservation_id,account_ref,period_id,actual_cost_nanos,
                 delta_nanos,evidence_sha256,supersedes_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                       (settlement, reservation_id, self.policy.account_ref, self.policy.period_id,
                        actual, delta, evidence, supersedes, now))
            db.execute("UPDATE reservations SET state='SETTLED' WHERE reservation_id=?", (reservation_id,))

    def snapshot(self, account_ref: str, period_id: str) -> dict:
        account, period = _identifier(account_ref), _identifier(period_id)
        if account != self.policy.account_ref:
            raise _error("POLICY_CONFLICT")
        with self._connection() as db:
            row = db.execute("SELECT policy_json FROM budgets WHERE account_ref=? AND period_id=?",
                             (account, period)).fetchone()
            if row is None:
                raise _error("POLICY_CONFLICT")
            try:
                policy = AccountBudgetPolicy(**json.loads(row[0]))
            except (ValueError, TypeError, GatewayError):
                raise _error("STORAGE_ERROR") from None
            delta, held, confirmed, calculated, warning, exceeded, overrun = self._totals(db, policy)
            states = {state: 0 for state in ("HELD", "SUBMITTED", "SETTLED")}
            states.update(dict(db.execute("SELECT state,COUNT(*) FROM reservations WHERE account_ref=? GROUP BY state",
                                          (account,))))
        return {
            "account_ref": account, "period_id": period, "opening_balance_status": "DECLARED_OPENING_BALANCE",
            "opening_spend_usd": policy.opening_spend_usd, "opening_evidence_ref": policy.opening_evidence_ref,
            "hard_limit_usd": policy.hard_limit_usd, "warning_ratio": policy.warning_ratio,
            "stop_ratio": policy.stop_ratio, "settlement_delta_usd": _usd(delta),
            "raw_confirmed_spend_usd": _usd(confirmed), "confirmed_spend_usd": _usd(max(0, confirmed)),
            "held_reservations_usd": _usd(held), "calculated_spend_usd": _usd(calculated),
            "warning": warning, "exceeded": exceeded, "reservation_overrun": overrun,
            "status": "EXCEEDED" if exceeded else "WARNING" if warning else "OK",
            "reservation_counts": states,
        }


def load_account_budget(config_path: str | Path) -> AccountBudgetStore:
    """Read server-owned JSON; resolve its database path relative to that file."""
    if config_path is None:
        raise _error("REQUIRED")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _error("CONFIG_INVALID")
            result[key] = value
        return result

    try:
        path = Path(config_path).resolve()
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise _error("CONFIG_INVALID")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        expected = set(AccountBudgetPolicy.__dataclass_fields__) | {"database_path"}
        required = expected - {"warning_ratio", "stop_ratio"}
        if not isinstance(data, dict) or not required <= set(data) <= expected:
            raise _error("CONFIG_INVALID")
        database_path = data.pop("database_path")
        if not isinstance(database_path, str) or not database_path.strip() or "\x00" in database_path:
            raise _error("CONFIG_INVALID")
        policy = AccountBudgetPolicy(**data)
        database = (path.parent / database_path).resolve()
    except (OSError, ValueError, TypeError, UnicodeError):
        raise _error("CONFIG_INVALID") from None
    return AccountBudgetStore(database, policy)
