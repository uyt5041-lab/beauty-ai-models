from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from beauty_ai.errors import GatewayError
from beauty_ai.provider_attempts import ProviderAttemptStore, money_units


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", "1000000.01", "0.0000001", True, "secret"])
def test_money_rejects_unbounded_values(value):
    with pytest.raises(ValueError):
        money_units(value)


def test_reservations_are_exact_and_missing_receipts_are_private(tmp_path):
    store = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    job, project = uuid4(), uuid4()
    attempt = store.begin(job, project, "a" * 64, budget_limit_usd="0.3", reservation_usd="0.1")
    receipt = store.get(job, project)
    assert receipt["attempt_id"] == str(attempt)
    assert receipt["reservation_usd"] == "0.100000"
    assert receipt["actual_cost_usd"] is None
    assert receipt["submission_status"] == "UNKNOWN"
    assert receipt["billing_status"] == "UNRECONCILED"
    assert store.get(job, uuid4()) is None


def test_budget_is_serialized_across_store_instances(tmp_path):
    path, project = tmp_path / "attempts.sqlite3", uuid4()
    ProviderAttemptStore(path)

    def reserve(_):
        try:
            return ProviderAttemptStore(path).begin(uuid4(), project, "a" * 64,
                                                    budget_limit_usd="0.3", reservation_usd="0.1")
        except GatewayError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(12)))
    assert results.count("PROVIDER_BUDGET_EXCEEDED") == 9


def test_restart_does_not_reserve_same_job_twice(tmp_path):
    path, job, project = tmp_path / "attempts.sqlite3", uuid4(), uuid4()
    ProviderAttemptStore(path).begin(job, project, "a" * 64, budget_limit_usd=5, reservation_usd=1)
    with pytest.raises(GatewayError) as error:
        ProviderAttemptStore(path).begin(job, project, "a" * 64, budget_limit_usd=5, reservation_usd=1)
    assert error.value.code == "PROVIDER_ATTEMPT_CONFLICT"


@pytest.mark.parametrize("state,code", [("failed", "PROVIDER_AUTH"), ("unknown", "PROVIDER_SUBMISSION_UNKNOWN")])
def test_failed_and_unknown_attempts_keep_budget(tmp_path, state, code):
    store, project, job = ProviderAttemptStore(tmp_path / "attempts.sqlite3"), uuid4(), uuid4()
    attempt = store.begin(job, project, "b" * 64, budget_limit_usd=1, reservation_usd=1)
    store.finish(job, project, attempt, state=state, error_code=code)
    with pytest.raises(GatewayError) as error:
        store.begin(uuid4(), project, "b" * 64, budget_limit_usd=1, reservation_usd=1)
    assert error.value.code == "PROVIDER_BUDGET_EXCEEDED"
    assert store.get(job, project)["state"] == state


def test_zero_budget_does_not_create_attempt(tmp_path):
    store, project, job = ProviderAttemptStore(tmp_path / "attempts.sqlite3"), uuid4(), uuid4()
    with pytest.raises(GatewayError):
        store.begin(job, project, "c" * 64, budget_limit_usd=0, reservation_usd=1)
    assert store.get(job, project) is None


def test_completion_requires_owner_and_single_transition(tmp_path):
    store, project, job = ProviderAttemptStore(tmp_path / "attempts.sqlite3"), uuid4(), uuid4()
    attempt = store.begin(job, project, "a" * 64, budget_limit_usd=1, reservation_usd=1)
    with pytest.raises(GatewayError):
        store.finish(job, uuid4(), attempt, state="failed", error_code="PROVIDER_AUTH")
    store.finish(job, project, attempt, state="failed", error_code="PROVIDER_AUTH")
    with pytest.raises(GatewayError):
        store.finish(job, project, attempt, state="failed", error_code="PROVIDER_AUTH")
