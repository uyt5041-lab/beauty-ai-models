"""Stage 3: durable experiment claims and reserved submission-slot limits."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from beauty_ai.contracts import AssetRef, ExternalImageResult
from beauty_ai.errors import GatewayError
from beauty_ai.provider_attempts import ProviderAttemptStore


PLAN = "a" * 64
MODEL = "gpt-image-2.5-sunburst-2026-09-08"


def configured(tmp_path, *, count=2, cap=None):
    path = tmp_path / "attempts.sqlite3"
    store = ProviderAttemptStore(path)
    project = uuid4()
    jobs = {uuid4(): f"{index + 1:064x}" for index in range(count)}
    store.configure_experiment(plan_sha256=PLAN, project_id=project, jobs=jobs,
                               max_attempts=count if cap is None else cap)
    return path, store, project, jobs


def begin(store, project, job, digest):
    return store.begin(job, project, digest, budget_limit_usd=100, reservation_usd=1)


def success(store, project, job, attempt):
    result = ExternalImageResult(operation="edit", model=MODEL, attempt_id=attempt,
        image=AssetRef(asset_id=uuid4(), sha256="d" * 64, width=1152, height=1440, mime_type="image/png"),
        warnings=["GENERATED_IMAGE_REQUIRES_REVIEW"])
    store.finish(job, project, attempt, state="succeeded", result=result)


def stopped(action, *, code=None):
    with pytest.raises(GatewayError) as caught:
        action()
    if code is not None:
        assert caught.value.code == code
    else:
        assert caught.value.code.startswith("EXPERIMENT_") or caught.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    return caught.value.code


def test_configuration_is_immutable_and_identical_reconfiguration_does_not_reset(tmp_path):
    path, store, project, jobs = configured(tmp_path)
    assert store.experiment_status() == {"plan_sha256": PLAN, "project_id": str(project),
                                        "max_attempts": 2, "state": "ready", "attempt_slots": 0}
    store.configure_experiment(plan_sha256=PLAN, project_id=project,
                               jobs=dict(reversed(list(jobs.items()))), max_attempts=2)
    store.claim_experiment(PLAN)
    job, digest = next(iter(jobs.items()))
    begin(store, project, job, digest)
    reopened = ProviderAttemptStore(path)
    reopened.configure_experiment(plan_sha256=PLAN, project_id=project, jobs=jobs, max_attempts=2)
    assert reopened.experiment_status()["state"] == "running"
    assert reopened.experiment_status()["attempt_slots"] == 1
    stopped(lambda: reopened.claim_experiment(PLAN))


@pytest.mark.parametrize("change", ["plan", "project", "jobs", "cap"])
def test_different_configuration_never_rebinds_existing_plan(tmp_path, change):
    _, store, project, jobs = configured(tmp_path)
    options = dict(plan_sha256=PLAN, project_id=project, jobs=jobs, max_attempts=2)
    if change == "plan":
        options["plan_sha256"] = "b" * 64
    elif change == "project":
        options["project_id"] = uuid4()
    elif change == "jobs":
        options["jobs"] = {uuid4(): "c" * 64}
    else:
        options["max_attempts"] = 3
    stopped(lambda: store.configure_experiment(**options), code="EXPERIMENT_PLAN_CONFLICT")
    assert store.experiment_status()["state"] == "ready"


@pytest.mark.parametrize("cap", [0, 31, True, 1.0, "1"])
def test_cap_requires_strict_integer_between_one_and_thirty(tmp_path, cap):
    store = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        store.configure_experiment(plan_sha256=PLAN, project_id=uuid4(), jobs={uuid4(): "b" * 64}, max_attempts=cap)
    assert store.experiment_status() is None


@pytest.mark.parametrize("change", ["empty_jobs", "too_many_jobs", "string_job", "invalid_hash", "invalid_plan", "invalid_project"])
def test_malformed_configuration_is_rejected_without_binding(tmp_path, change):
    store = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    options = dict(plan_sha256=PLAN, project_id=uuid4(), jobs={uuid4(): "b" * 64}, max_attempts=30)
    if change == "empty_jobs":
        options["jobs"] = {}
    elif change == "too_many_jobs":
        options["jobs"] = {uuid4(): "b" * 64 for _ in range(31)}
    elif change == "string_job":
        options["jobs"] = {str(uuid4()): "b" * 64}
    elif change == "invalid_hash":
        options["jobs"] = {uuid4(): "secret-invalid"}
    elif change == "invalid_plan":
        options["plan_sha256"] = "secret-invalid"
    else:
        options["project_id"] = "secret-invalid"
    with pytest.raises(ValueError, match="^EXPERIMENT_") as caught:
        store.configure_experiment(**options)
    assert "secret" not in str(caught.value)
    assert store.experiment_status() is None


def test_store_allows_plan_longer_than_cap_but_rejects_next_slot_at_cap(tmp_path):
    path, store, project, jobs = configured(tmp_path, count=2, cap=1)
    store.claim_experiment(PLAN)
    first, second = list(jobs)
    attempt = begin(store, project, first, jobs[first])
    success(store, project, first, attempt)
    reopened = ProviderAttemptStore(path)
    stopped(lambda: begin(reopened, project, second, jobs[second]), code="EXPERIMENT_CALL_LIMIT")
    assert reopened.experiment_status()["attempt_slots"] == 1
    stopped(lambda: reopened.finish_experiment(PLAN, succeeded=True))


def test_first_configuration_rejects_ledger_with_existing_provider_attempts(tmp_path):
    store = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    project, job = uuid4(), uuid4()
    attempt = begin(store, project, job, "b" * 64)
    stopped(lambda: store.configure_experiment(plan_sha256=PLAN, project_id=project,
                                              jobs={job: "b" * 64}, max_attempts=1),
            code="EXPERIMENT_PLAN_CONFLICT")
    assert store.experiment_status() is None
    assert store.get(job, project)["attempt_id"] == str(attempt)


def test_attempt_cannot_start_before_claim(tmp_path):
    _, store, project, jobs = configured(tmp_path)
    job, digest = next(iter(jobs.items()))
    stopped(lambda: begin(store, project, job, digest), code="EXPERIMENT_NOT_RUNNING")
    assert store.experiment_status()["attempt_slots"] == 0


@pytest.mark.parametrize("mismatch", ["job", "project", "request_hash"])
def test_attempt_must_match_frozen_job_project_and_request(tmp_path, mismatch):
    _, store, project, jobs = configured(tmp_path)
    store.claim_experiment(PLAN)
    job, digest = next(iter(jobs.items()))
    if mismatch == "job":
        job = uuid4()
    elif mismatch == "project":
        project = uuid4()
    else:
        digest = "f" * 64
    stopped(lambda: begin(store, project, job, digest), code="EXPERIMENT_REQUEST_NOT_IN_PLAN")
    assert store.experiment_status()["attempt_slots"] == 0


def test_concurrent_claim_has_one_winner(tmp_path):
    path, _, _, _ = configured(tmp_path)
    def claim(_):
        try:
            ProviderAttemptStore(path).claim_experiment(PLAN)
            return "claimed"
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert results.count("claimed") == 1
    assert all(r == "claimed" or r.startswith("EXPERIMENT_") for r in results)
    assert ProviderAttemptStore(path).experiment_status()["attempt_slots"] == 0


def test_concurrent_jobs_cannot_pass_an_unresolved_attempt(tmp_path):
    path, store, project, jobs = configured(tmp_path, count=8)
    store.claim_experiment(PLAN)
    def reserve(item):
        job, digest = item
        try:
            return begin(ProviderAttemptStore(path), project, job, digest)
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, jobs.items()))
    assert sum(isinstance(r, UUID) for r in results) == 1
    assert all(isinstance(r, UUID) or r == "EXPERIMENT_PREVIOUS_ATTEMPT_UNRESOLVED" for r in results)
    assert ProviderAttemptStore(path).experiment_status()["attempt_slots"] == 1


def test_claimed_crash_cannot_be_reclaimed_even_before_any_post(tmp_path):
    path, store, _, _ = configured(tmp_path)
    store.claim_experiment(PLAN)
    reopened = ProviderAttemptStore(path)
    stopped(lambda: reopened.claim_experiment(PLAN))
    assert reopened.experiment_status()["state"] == "running"
    assert reopened.experiment_status()["attempt_slots"] == 0


def test_pending_attempt_survives_restart_and_blocks_same_and_next_job(tmp_path):
    path, store, project, jobs = configured(tmp_path)
    store.claim_experiment(PLAN)
    first, second = list(jobs)
    begin(store, project, first, jobs[first])
    reopened = ProviderAttemptStore(path)
    stopped(lambda: begin(reopened, project, first, jobs[first]))
    stopped(lambda: begin(reopened, project, second, jobs[second]))
    assert reopened.get(first, project)["state"] == "submitting"
    assert reopened.experiment_status()["attempt_slots"] == 1


@pytest.mark.parametrize("state,error_code", [("failed", "PROVIDER_AUTH"), ("unknown", "PROVIDER_SUBMISSION_UNKNOWN")])
def test_failed_or_unknown_completion_halts_plan_across_restart(tmp_path, state, error_code):
    path, store, project, jobs = configured(tmp_path)
    store.claim_experiment(PLAN)
    first, second = list(jobs)
    attempt = begin(store, project, first, jobs[first])
    store.finish(first, project, attempt, state=state, error_code=error_code)
    reopened = ProviderAttemptStore(path)
    assert reopened.experiment_status()["state"] == "halted"
    assert reopened.experiment_status()["attempt_slots"] == 1
    assert reopened.get(first, project)["state"] == state
    stopped(lambda: reopened.claim_experiment(PLAN))
    stopped(lambda: begin(reopened, project, second, jobs[second]))
    stopped(lambda: reopened.finish_experiment(PLAN, succeeded=True))


def test_success_requires_every_planned_job_and_remains_terminal(tmp_path):
    path, store, project, jobs = configured(tmp_path)
    store.claim_experiment(PLAN)
    stopped(lambda: store.finish_experiment(PLAN, succeeded=True), code="EXPERIMENT_INCOMPLETE")
    for job, digest in jobs.items():
        attempt = begin(store, project, job, digest)
        success(store, project, job, attempt)
    store.finish_experiment(PLAN, succeeded=True)
    reopened = ProviderAttemptStore(path)
    assert reopened.experiment_status()["state"] == "succeeded"
    assert reopened.experiment_status()["attempt_slots"] == len(jobs)
    reopened.finish_experiment(PLAN, succeeded=True)
    stopped(lambda: reopened.claim_experiment(PLAN))
    stopped(lambda: reopened.finish_experiment(PLAN, succeeded=False))
    job, digest = next(iter(jobs.items()))
    stopped(lambda: begin(reopened, project, job, digest))


def test_runner_failure_halts_without_refunding_existing_reservation(tmp_path):
    path, store, project, jobs = configured(tmp_path)
    store.claim_experiment(PLAN)
    job, digest = next(iter(jobs.items()))
    begin(store, project, job, digest)
    store.finish_experiment(PLAN, succeeded=False)
    reopened = ProviderAttemptStore(path)
    assert reopened.experiment_status()["state"] == "halted"
    assert reopened.experiment_status()["attempt_slots"] == 1
    stopped(lambda: reopened.claim_experiment(PLAN))


def test_unconfigured_service_ledger_retains_existing_independent_job_behavior(tmp_path):
    store = ProviderAttemptStore(tmp_path / "ordinary.sqlite3")
    project = uuid4()
    first, second = uuid4(), uuid4()
    begin(store, project, first, "b" * 64)
    begin(store, project, second, "c" * 64)
    assert store.experiment_status() is None
    assert store.get(first, project)["state"] == store.get(second, project)["state"] == "submitting"
    with pytest.raises(GatewayError) as caught:
        begin(store, project, first, "b" * 64)
    assert caught.value.code == "PROVIDER_ATTEMPT_CONFLICT"
