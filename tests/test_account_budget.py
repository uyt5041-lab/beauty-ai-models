from concurrent.futures import ProcessPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timezone
from decimal import localcontext
from hashlib import sha256
import json
import multiprocessing
import sqlite3
from uuid import uuid4

import pytest

from beauty_ai.account_budget import AccountBudgetPolicy, AccountBudgetStore, load_account_budget
from beauty_ai.errors import GatewayError
from beauty_ai.provider_attempts import ProviderAttemptStore


NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
REQUEST = "a" * 64
EVIDENCE = "b" * 64


def policy(**changes):
    values = dict(account_ref="team-image", period_id="2026-11", starts_at="2026-11-01T00:00:00Z",
                  ends_at="2026-12-01T00:00:00Z", hard_limit_usd="10", opening_spend_usd="0",
                  opening_evidence_ref="operator:opening-20261101")
    values.update(changes)
    return AccountBudgetPolicy(**values)


def make_store(tmp_path, **changes):
    return AccountBudgetStore(tmp_path / "account.sqlite3", policy(**changes), clock=lambda: NOW)


def snapshot(store):
    return store.snapshot(store.policy.account_ref, store.policy.period_id)


def rejected(code):
    return pytest.raises(GatewayError, match="^ACCOUNT_BUDGET_" + code + "$")


def reserve(store, amount="1", *, project=None, job=None):
    return store.reserve(project or uuid4(), job or uuid4(), REQUEST, amount)


def submit(store, amount="1"):
    reservation = reserve(store, amount)
    attempt = uuid4()
    permit = store.bind_attempt(reservation, attempt)
    permit.claim(REQUEST, attempt)
    return reservation, permit, attempt


def db_rows(store, table):
    # Only these test-owned constant table names are accepted.
    assert table in {"reservations", "settlements", "budgets"}
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]


def _reserve_process(args):
    path, job, same_job = args
    try:
        store = AccountBudgetStore(path, policy(hard_limit_usd="1"), clock=lambda: NOW)
        return store.reserve("parallel-project", "one-job" if same_job else job, REQUEST, "0.1")
    except GatewayError as error:
        return error.code


def _claim_process(args):
    path, reservation, attempt = args
    try:
        store = AccountBudgetStore(path, policy(), clock=lambda: NOW)
        store.bind_attempt(reservation, attempt).claim(REQUEST, attempt)
        return "CLAIMED"
    except GatewayError as error:
        return error.code


def _settle_process(args):
    path, reservation, index = args
    try:
        store = AccountBudgetStore(path, policy(), clock=lambda: NOW)
        store.settle(reservation, f"parallel-settlement-{index}", "1", EVIDENCE)
        return "SETTLED"
    except GatewayError as error:
        return error.code


def _race_process(args):
    path, reservation, attempt, operation = args
    try:
        store = AccountBudgetStore(path, policy(), clock=lambda: NOW)
        if operation == "claim":
            store.bind_attempt(reservation, attempt).claim(REQUEST, attempt)
        else:
            store.settle(reservation, "unsent-" + reservation, "0", sha256(reservation.encode()).hexdigest())
        return operation
    except GatewayError as error:
        return error.code


def _correction_process(args):
    path, reservation, index = args
    try:
        store = AccountBudgetStore(path, policy(), clock=lambda: NOW)
        store.settle(reservation, f"correction-{index}", "0.5", sha256(str(index).encode()).hexdigest(),
                     supersedes_id="original")
        return "CORRECTED"
    except GatewayError as error:
        return error.code


@pytest.mark.parametrize("changes", [
    {"account_ref": ""}, {"account_ref": "a@b.com"}, {"account_ref": "a" * 201},
    {"period_id": "../ secret"}, {"opening_evidence_ref": ""}, {"opening_evidence_ref": None},
    {"schema_version": "v2"}, {"starts_at": "2026-11-01T00:00:00"},
    {"starts_at": "invalid-secret-value"}, {"ends_at": "2026-11-01T00:00:00Z"},
    {"starts_at": "2026-12-02T00:00:00Z"}, {"hard_limit_usd": 10},
    {"hard_limit_usd": 10.0}, {"hard_limit_usd": True}, {"hard_limit_usd": "NaN"},
    {"hard_limit_usd": "Infinity"}, {"hard_limit_usd": "-1"}, {"hard_limit_usd": "1e1"},
    {"hard_limit_usd": "1000000000.000000001"}, {"hard_limit_usd": "0.0000000001"},
    {"opening_spend_usd": "-0.1"}, {"opening_spend_usd": None},
    {"warning_ratio": 0.8}, {"warning_ratio": "0.95"}, {"stop_ratio": "1.1"},
    {"warning_ratio": "-0.1"}, {"stop_ratio": "NaN"},
])
def test_policy_rejects_invalid_or_untrusted_values_safely(changes):
    with rejected("CONFIG_INVALID") as error:
        policy(**changes)
    assert error.value.code == "ACCOUNT_BUDGET_CONFIG_INVALID"
    assert "secret" not in str(error.value)


def test_policy_is_immutable_and_opening_is_declared(tmp_path):
    store = make_store(tmp_path, opening_spend_usd="1.000000001")
    with pytest.raises(FrozenInstanceError):
        store.policy.hard_limit_usd = "999"
    result = snapshot(store)
    assert result["opening_balance_status"] == "DECLARED_OPENING_BALANCE"
    assert result["raw_confirmed_spend_usd"] == "1.000000001"
    assert result["calculated_spend_usd"] == "1.000001000"
    assert result["status"] == "OK"


def test_loader_resolves_relative_path_and_requires_explicit_opening(tmp_path):
    config = tmp_path / "config" / "budget.json"
    config.parent.mkdir()
    data = {**asdict(policy()), "database_path": "../data/account.sqlite3"}
    config.write_text(json.dumps(data), encoding="utf-8")
    store = load_account_budget(config)
    assert store.path == tmp_path / "data" / "account.sqlite3"
    assert store.policy == policy()
    assert len(db_rows(store, "budgets")) == 1
    del data["opening_spend_usd"]
    config.write_text(json.dumps(data), encoding="utf-8")
    with rejected("CONFIG_INVALID"):
        load_account_budget(config)


@pytest.mark.parametrize("content", ["bad-secret-json", "[]", "null", '{"database_path":"key-secret"}'])
def test_loader_bad_json_never_exposes_content(tmp_path, content):
    config = tmp_path / "budget.json"
    config.write_text(content, encoding="utf-8")
    with rejected("CONFIG_INVALID"):
        load_account_budget(config)


def test_missing_loader_config_and_unknown_fields(tmp_path):
    with rejected("REQUIRED"):
        load_account_budget(None)
    with rejected("CONFIG_INVALID"):
        load_account_budget(tmp_path / "secret-missing.json")
    config = tmp_path / "budget.json"
    config.write_text(json.dumps({**asdict(policy()), "database_path": "db", "api_key": "secret"}))
    with rejected("CONFIG_INVALID"):
        load_account_budget(config)
    assert not (tmp_path / "db").exists()


def test_loader_defaults_ratios_and_rejects_duplicate_keys_and_large_files(tmp_path):
    config = tmp_path / "budget.json"
    data = {**asdict(policy()), "database_path": "account.sqlite3"}
    del data["warning_ratio"]
    del data["stop_ratio"]
    config.write_text(json.dumps(data))
    loaded = load_account_budget(config)
    assert loaded.policy.warning_ratio == "0.8"
    assert loaded.policy.stop_ratio == "0.9"
    encoded = json.dumps(data)
    config.write_text(encoded[:-1] + ', "hard_limit_usd":"999"}')
    with rejected("CONFIG_INVALID"):
        load_account_budget(config)
    config.write_text(encoded + " " * 65536)
    with rejected("CONFIG_INVALID"):
        load_account_budget(config)


def test_persisted_policy_is_immutable_and_separate_accounts_do_not_overlap(tmp_path):
    store = make_store(tmp_path)
    restarted = AccountBudgetStore(store.path, policy(), clock=lambda: NOW)
    restarted.configure()
    assert len(db_rows(store, "budgets")) == 1
    for change in ({"hard_limit_usd": "11"}, {"opening_spend_usd": "1"},
                   {"opening_evidence_ref": "operator:other"}, {"warning_ratio": "0.7"},
                   {"stop_ratio": "1"}, {"starts_at": "2026-11-02T00:00:00Z"}):
        with rejected("POLICY_CONFLICT"):
            AccountBudgetStore(store.path, policy(**change))
    other = AccountBudgetStore(store.path, policy(account_ref="other-account"), clock=lambda: NOW)
    reserve(other, "9")
    assert snapshot(store)["held_reservations_usd"] == "0.000000000"
    assert snapshot(other)["held_reservations_usd"] == "9.000000000"
    with rejected("POLICY_CONFLICT"):
        store.snapshot("other-account", "2026-11")


@pytest.mark.parametrize("start,end", [
    ("2026-10-31T00:00:00Z", "2026-11-02T00:00:00Z"),
    ("2026-11-30T00:00:00Z", "2026-12-02T00:00:00Z"),
    ("2026-10-01T00:00:00Z", "2027-01-01T00:00:00Z"),
    ("2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"),
    ("2026-11-01T09:00:00+09:00", "2026-12-01T09:00:00+09:00"),
])
def test_overlapping_periods_rejected_after_timezone_normalization(tmp_path, start, end):
    store = make_store(tmp_path)
    with rejected("POLICY_CONFLICT"):
        AccountBudgetStore(store.path, policy(period_id="overlap", starts_at=start, ends_at=end))
    assert len(db_rows(store, "budgets")) == 1


def test_adjacent_periods_are_allowed_and_time_window_is_half_open(tmp_path):
    store = make_store(tmp_path)
    next_policy = policy(period_id="2026-12", starts_at="2026-12-01T00:00:00Z", ends_at="2027-01-01T00:00:00Z")
    future = AccountBudgetStore(store.path, next_policy, clock=lambda: NOW)
    with rejected("POLICY_CONFLICT"):
        reserve(future)
    clock = [datetime(2026, 11, 1, tzinfo=timezone.utc)]
    active = AccountBudgetStore(store.path, policy(), clock=lambda: clock[0])
    reservation = reserve(active)
    attempt = uuid4()
    permit = active.bind_attempt(reservation, attempt)
    clock[0] = datetime(2026, 12, 1, tzinfo=timezone.utc)
    for operation in (lambda: reserve(active), lambda: active.bind_attempt(reservation, attempt),
                      lambda: permit.claim(REQUEST, attempt),
                      lambda: active.settle(reservation, "late", "0", EVIDENCE)):
        with rejected("POLICY_CONFLICT"):
            operation()
    assert snapshot(active)["reservation_counts"] == {"HELD": 1, "SUBMITTED": 0, "SETTLED": 0}


def test_identity_hash_and_duplicate_conflicts_survive_restart(tmp_path):
    store = make_store(tmp_path)
    project, job = uuid4(), uuid4()
    reservation = reserve(store, project=project, job=job)
    expected = sha256(json.dumps(["team-image", str(project), str(job)], separators=(",", ":")).encode()).hexdigest()
    assert reservation == expected
    restarted = AccountBudgetStore(store.path, policy(), clock=lambda: NOW)
    for request, amount in [(REQUEST, "1"), ("c" * 64, "1"), (REQUEST, "2")]:
        with rejected("RESERVATION_CONFLICT"):
            restarted.reserve(project, job, request, amount)
    assert reserve(store, project=uuid4(), job=job) != reservation
    assert len(db_rows(store, "reservations")) == 2
    assert snapshot(store)["held_reservations_usd"] == "2.000000000"


@pytest.mark.parametrize("amount", ["0", "-1", "0.0000001", "NaN", "Infinity", "1000001", True, None])
def test_reservations_reuse_strict_microdollar_validation(tmp_path, amount):
    store = make_store(tmp_path)
    with rejected("CONFIG_INVALID"):
        reserve(store, amount)
    assert db_rows(store, "reservations") == []


@pytest.mark.parametrize("project,job,request_hash", [
    ("", "job", REQUEST), ("project", "", REQUEST), ("a@b.com", "job", REQUEST),
    ("p", "j" * 201, REQUEST), ("p", "j", "secret"), (None, "j", REQUEST),
])
def test_invalid_reservation_identifiers_fail_safely(tmp_path, project, job, request_hash):
    store = make_store(tmp_path)
    with rejected("RESERVATION_CONFLICT"):
        store.reserve(project, job, request_hash, "1")


def test_zero_limit_stop_threshold_warning_and_declared_opening(tmp_path):
    zero = make_store(tmp_path, hard_limit_usd="0")
    with rejected("EXCEEDED"):
        reserve(zero, "0.000001")
    assert db_rows(zero, "reservations") == []
    store = AccountBudgetStore(tmp_path / "limited.sqlite3", policy(opening_spend_usd="7.999999"), clock=lambda: NOW)
    reserve(store, "0.000001")
    assert snapshot(store)["warning"] is True
    reserve(store, "1")
    assert snapshot(store)["calculated_spend_usd"] == "9.000000000"
    assert snapshot(store)["exceeded"] is False
    with rejected("EXCEEDED"):
        reserve(store, "0.000001")


def test_permit_is_bound_and_single_use_including_restart(tmp_path):
    store = make_store(tmp_path)
    reservation = reserve(store)
    attempt = uuid4()
    permit = store.bind_attempt(reservation, attempt)
    assert store.bind_attempt(reservation, attempt) == permit
    with rejected("RESERVATION_CONFLICT"):
        store.bind_attempt(reservation, uuid4())
    with rejected("RESERVATION_CONFLICT"):
        permit.claim("c" * 64, attempt)
    with rejected("RESERVATION_CONFLICT"):
        permit.claim(REQUEST, uuid4())
    other_reservation = reserve(store)
    with rejected("RESERVATION_CONFLICT"):
        store.bind_attempt(other_reservation, attempt)
    permit.claim(REQUEST, attempt)
    with rejected("RESERVATION_CONFLICT"):
        permit.claim(REQUEST, attempt)
    restarted = AccountBudgetStore(store.path, policy(), clock=lambda: NOW)
    with rejected("RESERVATION_CONFLICT"):
        restarted.bind_attempt(reservation, attempt)
    assert snapshot(store)["reservation_counts"] == {"HELD": 1, "SUBMITTED": 1, "SETTLED": 0}
    assert snapshot(store)["held_reservations_usd"] == "2.000000000"


def test_settlement_before_claim_is_terminal_and_only_confirmed_zero(tmp_path):
    store = make_store(tmp_path)
    reservation = reserve(store)
    attempt = uuid4()
    permit = store.bind_attempt(reservation, attempt)
    with rejected("RESERVATION_CONFLICT"):
        store.settle(reservation, "invalid", "0.01", EVIDENCE)
    store.settle(reservation, "not-sent", "0", EVIDENCE)
    with rejected("RESERVATION_CONFLICT"):
        permit.claim(REQUEST, attempt)
    with rejected("RESERVATION_CONFLICT"):
        store.bind_attempt(reservation, attempt)
    with rejected("RESERVATION_CONFLICT"):
        store.settle(reservation, "invalid-correction", "1", EVIDENCE, supersedes_id="not-sent")
    store.settle(reservation, "confirmed-not-sent", "0", "c" * 64, supersedes_id="not-sent")
    assert snapshot(store)["calculated_spend_usd"] == "0.000000000"
    assert snapshot(store)["reservation_counts"]["SETTLED"] == 1


def test_settlement_preserves_nine_decimals_and_rounds_aggregate_only(tmp_path):
    store = make_store(tmp_path)
    for index in range(2):
        reservation, _, _ = submit(store)
        store.settle(reservation, f"actual-{index}", "0.000000001", sha256(str(index).encode()).hexdigest())
    result = snapshot(store)
    assert result["settlement_delta_usd"] == "0.000000002"
    assert result["confirmed_spend_usd"] == "0.000000002"
    assert result["held_reservations_usd"] == "0.000000000"
    assert result["calculated_spend_usd"] == "0.000001000"
    assert [row["actual_cost_nanos"] for row in db_rows(store, "settlements")] == [1, 1]


def test_money_precision_is_independent_of_callers_decimal_context(tmp_path):
    with localcontext() as context:
        context.prec = 2
        store = make_store(tmp_path, opening_spend_usd="0.123456789")
        reservation, _, _ = submit(store, "0.123456")
        assert snapshot(store)["held_reservations_usd"] == "0.123456000"
        store.settle(reservation, "precise", "0.123450001", EVIDENCE)
        result = snapshot(store)
        assert result["confirmed_spend_usd"] == "0.246906790"
        assert result["calculated_spend_usd"] == "0.246907000"


@pytest.mark.parametrize("actual", ["-1", "NaN", "Infinity", 0.1, 0, True, "1e-9", "0.0000000001", "1000000001"])
def test_invalid_actual_cost_never_releases_hold(tmp_path, actual):
    store = make_store(tmp_path)
    reservation, _, _ = submit(store)
    with rejected("CONFIG_INVALID"):
        store.settle(reservation, "bad-actual", actual, EVIDENCE)
    assert snapshot(store)["held_reservations_usd"] == "1.000000000"
    assert not db_rows(store, "settlements")


def test_settlement_overrun_is_preserved_and_blocks_reservations(tmp_path):
    store = make_store(tmp_path)
    reservation, _, _ = submit(store)
    store.settle(reservation, "overrun", "1.000000001", EVIDENCE)
    result = snapshot(store)
    assert result["status"] == "EXCEEDED"
    assert result["reservation_overrun"] is True
    assert result["confirmed_spend_usd"] == "1.000000001"
    with rejected("EXCEEDED"):
        reserve(store, "0.000001")
    store.settle(reservation, "corrected", "0.9", "c" * 64, supersedes_id="overrun")
    assert snapshot(store)["exceeded"] is False
    reserve(store, "1")


def test_idempotent_settlement_and_append_only_latest_corrections(tmp_path):
    store = make_store(tmp_path)
    reservation, _, _ = submit(store, "5")
    store.settle(reservation, "first", "4", EVIDENCE)
    original = db_rows(store, "settlements")[0]
    store.settle(reservation, "first", "4.000000000", EVIDENCE)
    for args in [("first", "3", EVIDENCE, None), ("first", "4", "c" * 64, None),
                 ("first", "4", EVIDENCE, "first"), ("second", "3", EVIDENCE, None),
                 ("second", "3", EVIDENCE, "missing")]:
        with rejected("RESERVATION_CONFLICT"):
            store.settle(reservation, *args)
    store.settle(reservation, "second", "5", "c" * 64, supersedes_id="first")
    with rejected("RESERVATION_CONFLICT"):
        store.settle(reservation, "fork", "3", EVIDENCE, supersedes_id="first")
    store.settle(reservation, "third", "3", "d" * 64, supersedes_id="second")
    rows = db_rows(store, "settlements")
    assert rows[0] == original
    assert [row["delta_nanos"] for row in rows] == [4_000_000_000, 1_000_000_000, -2_000_000_000]
    assert snapshot(store)["confirmed_spend_usd"] == "3.000000000"
    assert snapshot(store)["held_reservations_usd"] == "0.000000000"
    store.settle(reservation, "first", "4", EVIDENCE)  # historical exact replay remains a no-op
    assert len(db_rows(store, "settlements")) == 3


def test_evidence_hash_cannot_be_reused_for_conflicting_facts(tmp_path):
    store = make_store(tmp_path)
    first, _, _ = submit(store)
    second, _, _ = submit(store)
    store.settle(first, "first", "1", EVIDENCE)
    with rejected("RESERVATION_CONFLICT"):
        store.settle(second, "stolen-evidence", "1", EVIDENCE)
    with rejected("RESERVATION_CONFLICT"):
        store.settle(first, "changed-facts", "0.5", EVIDENCE, supersedes_id="first")
    store.settle(first, "same-facts", "1", EVIDENCE, supersedes_id="first")
    assert len(db_rows(store, "settlements")) == 2
    assert snapshot(store)["held_reservations_usd"] == "1.000000000"


def december(store, opening="0", hard_limit="10"):
    next_policy = policy(period_id="2026-12", starts_at="2026-12-01T00:00:00Z", ends_at="2027-01-01T00:00:00Z",
                         opening_spend_usd=opening, hard_limit_usd=hard_limit)
    return AccountBudgetStore(store.path, next_policy, clock=lambda: datetime(2026, 12, 2, tzinfo=timezone.utc))


def test_rollover_settles_old_hold_and_records_only_correction_deltas(tmp_path):
    november = make_store(tmp_path)
    reservation, _, _ = submit(november, "5")
    current = december(november)
    assert snapshot(current)["calculated_spend_usd"] == "5.000000000"
    current.settle(reservation, "dec-first", "5", EVIDENCE)
    assert snapshot(current)["calculated_spend_usd"] == "5.000000000"
    current.settle(reservation, "dec-second", "6", "c" * 64, supersedes_id="dec-first")
    assert snapshot(current)["calculated_spend_usd"] == "6.000000000"
    current.settle(reservation, "dec-third", "4", "d" * 64, supersedes_id="dec-second")
    assert snapshot(current)["calculated_spend_usd"] == "4.000000000"
    assert all(row["period_id"] == "2026-12" for row in db_rows(current, "settlements"))
    assert snapshot(november)["settlement_delta_usd"] == "0.000000000"


def test_prior_confirmed_is_not_double_counted_and_negative_delta_is_clamped(tmp_path):
    old = make_store(tmp_path)
    reservation, _, _ = submit(old, "5")
    old.settle(reservation, "old-actual", "5", EVIDENCE)
    current = december(old, opening="1")
    held = reserve(current, "1")
    assert snapshot(current)["calculated_spend_usd"] == "2.000000000"
    current.settle(reservation, "refund", "0", "c" * 64, supersedes_id="old-actual")
    result = snapshot(current)
    assert result["raw_confirmed_spend_usd"] == "-4.000000000"
    assert result["confirmed_spend_usd"] == "0.000000000"
    assert result["held_reservations_usd"] == "1.000000000"
    assert result["calculated_spend_usd"] == "1.000000000"
    reserve(current, "8")
    with rejected("EXCEEDED"):
        reserve(current, "0.000001")
    current.settle(held, "unsent", "0", "d" * 64)


def test_rollover_cannot_claim_old_held_or_clear_prior_overrun(tmp_path):
    old = make_store(tmp_path)
    reservation = reserve(old)
    attempt = uuid4()
    permit = old.bind_attempt(reservation, attempt)
    current = december(old)
    with rejected("RESERVATION_CONFLICT"):
        current.bind_attempt(reservation, attempt)
    current.settle(reservation, "unsent", "0", EVIDENCE)
    with rejected("RESERVATION_CONFLICT"):
        permit.claim(REQUEST, attempt)
    submitted, _, _ = submit(old)
    old.settle(submitted, "old-overrun", "2", "c" * 64)
    assert snapshot(current)["calculated_spend_usd"] == "0.000000000"
    assert snapshot(current)["exceeded"] is True
    with rejected("EXCEEDED"):
        reserve(current)


def test_accounts_cannot_bind_claim_or_settle_each_others_reservations(tmp_path):
    first = make_store(tmp_path)
    second = AccountBudgetStore(first.path, policy(account_ref="account-two"), clock=lambda: NOW)
    reservation, permit, attempt = submit(first)
    for operation in (lambda: second.bind_attempt(reservation, attempt),
                      lambda: second.settle(reservation, "stolen", "1", EVIDENCE),
                      lambda: replace(permit, _store=second).claim(REQUEST, attempt)):
        with rejected("RESERVATION_CONFLICT"):
            operation()
    assert snapshot(first)["held_reservations_usd"] == "1.000000000"
    assert snapshot(second)["held_reservations_usd"] == "0.000000000"


@pytest.mark.parametrize("same_job,expected_success", [(False, 9), (True, 1)])
def test_file_sqlite_serializes_multiple_processes(tmp_path, same_job, expected_success):
    store = make_store(tmp_path, hard_limit_usd="1")
    args = [(str(store.path), f"job-{index}", same_job) for index in range(16)]
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_reserve_process, args))
    successes = [value for value in results if not value.startswith("ACCOUNT_BUDGET_")]
    assert len(successes) == expected_success
    code = "RESERVATION_CONFLICT" if same_job else "EXCEEDED"
    assert results.count("ACCOUNT_BUDGET_" + code) == 16 - expected_success
    assert len(db_rows(store, "reservations")) == expected_success
    assert snapshot(store)["held_reservations_usd"] == ("0.100000000" if same_job else "0.900000000")


def test_multiple_processes_only_one_claim_and_one_first_settlement(tmp_path):
    store = make_store(tmp_path)
    reservation = reserve(store)
    attempt = str(uuid4())
    args = [(str(store.path), reservation, attempt)] * 8
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        claims = list(pool.map(_claim_process, args))
        settlements = list(pool.map(_settle_process, [(str(store.path), reservation, i) for i in range(8)]))
    assert claims.count("CLAIMED") == 1
    assert claims.count("ACCOUNT_BUDGET_RESERVATION_CONFLICT") == 7
    assert settlements.count("SETTLED") == 1
    assert settlements.count("ACCOUNT_BUDGET_RESERVATION_CONFLICT") == 7
    assert len(db_rows(store, "settlements")) == 1
    assert snapshot(store)["held_reservations_usd"] == "0.000000000"


def test_claim_and_zero_settlement_race_never_reopens_settled_reservation(tmp_path):
    store = make_store(tmp_path)
    reservation = reserve(store)
    attempt = str(uuid4())
    store.bind_attempt(reservation, attempt)
    args = [(str(store.path), reservation, attempt, action) for action in ("settle", "claim")]
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_race_process, args))
    assert results[0] == "settle"
    assert results[1] in {"claim", "ACCOUNT_BUDGET_RESERVATION_CONFLICT"}
    row = db_rows(store, "reservations")[0]
    assert row["state"] == "SETTLED"
    assert (row["submitted_at"] is not None) == (results[1] == "claim")
    with rejected("RESERVATION_CONFLICT"):
        store.bind_attempt(reservation, attempt)


def test_concurrent_corrections_require_current_latest_settlement(tmp_path):
    store = make_store(tmp_path)
    reservation, _, _ = submit(store)
    store.settle(reservation, "original", "1", EVIDENCE)
    original = db_rows(store, "settlements")[0]
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_correction_process, [(str(store.path), reservation, i) for i in range(8)]))
    assert results.count("CORRECTED") == 1
    assert results.count("ACCOUNT_BUDGET_RESERVATION_CONFLICT") == 7
    rows = db_rows(store, "settlements")
    assert len(rows) == 2 and rows[0] == original
    assert snapshot(store)["confirmed_spend_usd"] == "0.500000000"


def test_explicit_separate_database_preserves_experiment_state(tmp_path):
    experiments = [ProviderAttemptStore(tmp_path / f"experiment-{index}.sqlite3") for index in range(2)]
    for local in experiments:
        with rejected("CONFIG_INVALID"):
            AccountBudgetStore(local.path, policy())
        with sqlite3.connect(local.path) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "budgets" not in names
            assert names == {"provider_attempts", "experiment_limits", "provider_reuse_events"}
    shared = make_store(tmp_path)
    with sqlite3.connect(shared.path) as db:
        assert {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "budgets", "reservations", "settlements"}


def test_storage_errors_are_safe_for_constructor_and_all_operations(tmp_path, monkeypatch):
    with rejected("STORAGE_ERROR"):
        AccountBudgetStore(tmp_path, policy())
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_text("secret database payload")
    with rejected("STORAGE_ERROR"):
        AccountBudgetStore(corrupt, policy())
    store = make_store(tmp_path)
    reservation = reserve(store)
    attempt = uuid4()
    permit = store.bind_attempt(reservation, attempt)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("secret disk path or contents")

    monkeypatch.setattr(sqlite3, "connect", fail)
    for operation in (store.configure, lambda: reserve(store), lambda: store.bind_attempt(reservation, attempt),
                      lambda: permit.claim(REQUEST, attempt),
                      lambda: store.settle(reservation, "unsent", "0", EVIDENCE), lambda: snapshot(store)):
        with rejected("STORAGE_ERROR") as error:
            operation()
        assert error.value.__cause__ is None


def test_failed_settlement_rolls_back_hold_and_short_transactions_release_lock(tmp_path):
    store = make_store(tmp_path)
    reservation, permit, attempt = submit(store)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER reject_settlement BEFORE INSERT ON settlements BEGIN SELECT RAISE(ABORT,'secret'); END")
    with rejected("STORAGE_ERROR"):
        store.settle(reservation, "failed", "1", EVIDENCE)
    assert snapshot(store)["held_reservations_usd"] == "1.000000000"
    assert db_rows(store, "settlements") == []
    # In the real caller the network happens here, after claim returned.
    with sqlite3.connect(store.path, timeout=0) as db:
        db.execute("BEGIN IMMEDIATE")
        db.rollback()
    with rejected("RESERVATION_CONFLICT"):
        permit.claim(REQUEST, attempt)


def test_corrupt_policy_snapshot_is_safe_storage_error(tmp_path):
    store = make_store(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE budgets SET policy_json='secret-corrupted-json'")
    with rejected("STORAGE_ERROR"):
        snapshot(store)
