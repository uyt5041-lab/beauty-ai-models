from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
from threading import Barrier
from uuid import uuid4

import pytest

from beauty_ai.errors import GatewayError
from beauty_ai.work_ledger import AdWorkLedger, read_work_dataset


def receipt(attempt_id, *, status="SUCCEEDED", model="model-v1", provider_request_id=None,
            artifact=None, usage=None, local=False):
    return {"schema_version": "beautyai-work-receipt-v1", "attempt_id": str(attempt_id),
            "provider_request_id": provider_request_id, "model": "local" if local else model,
            "usage": {"input_tokens": 0, "output_tokens": 0} if local else usage,
            "elapsed_seconds": 0.25, "status": status,
            "error_code": None if status == "SUCCEEDED" else "MODEL_FAILED",
            "artifact_sha256": artifact, "estimated_cost_usd": "0.000000000" if local else None,
            "rate_version": "local-api-zero-v1" if local else None,
            "actual_cost_usd": None, "billing_evidence_ref": None,
            "source_ref": f"work-receipt:{attempt_id}", "source_sha256": "b" * 64}


def start(ledger, project, work, *, attempt=None, stage="prompt_build", operation=None,
          local=False, request_hash=None, parent=None):
    attempt = attempt or uuid4()
    ledger.start_attempt(project, work, attempt, stage, operation or stage,
                         "local" if local else "openai", "local" if local else "model-v1",
                         "prompt-v1", request_hash or attempt.hex * 2, parent)
    return attempt


def completion(attempts, artifact="a" * 64):
    return {"artifact_sha256": artifact, "reviewer_ref": "reviewer-1",
            "quality_policy_version": "quality-v1", "quality_pass": True,
            "expected_attempt_ids": [str(value) for value in attempts]}


@pytest.fixture
def setup(tmp_path):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work = uuid4(), uuid4()
    ledger.create_work(project, work, "product-1")
    return ledger, project, work


def test_create_restart_idempotency_and_detached_results(setup):
    ledger, project, work = setup
    original = ledger.get_work(project, work)
    original["required_stages"].clear()
    reopened = AdWorkLedger(ledger.path)
    assert reopened.path.is_absolute()
    assert reopened.create_work(project, work, "product-1")["required_stages"]
    with pytest.raises(GatewayError, match="Advertisement") as error:
        reopened.create_work(project, work, "different-product")
    assert error.value.code == "WORK_SPEC_CONFLICT"


def test_every_read_and_write_is_project_scoped(setup):
    ledger, project, work = setup
    other = uuid4()
    attempt = start(ledger, project, work)
    job = uuid4()
    ledger.bind_image_job(project, work, job)
    for action in (lambda: ledger.get_work(other, work),
                   lambda: ledger.cancel(other, work),
                   lambda: start(ledger, other, work),
                   lambda: ledger.record_receipt(other, work, receipt(attempt)),
                   lambda: ledger.bind_image_job(other, work, job)):
        with pytest.raises(GatewayError) as error:
            action()
        assert error.value.code == "WORK_NOT_FOUND"
    assert ledger.get_image_binding(other, job) is None
    assert ledger.export_dataset(other)["works"] == []
    assert len(ledger.export_dataset(project)["attempts"]) == 1


def test_receipt_replay_is_immutable_and_counts_once(setup):
    ledger, project, work = setup
    attempt = start(ledger, project, work)
    observed = receipt(attempt, usage={"input_tokens": 3, "output_tokens": 5})
    ledger.record_receipt(project, work, observed)
    ledger.record_receipt(project, work, deepcopy(observed))
    changed = {**observed, "elapsed_seconds": 1.25}
    with pytest.raises(GatewayError) as error:
        ledger.record_receipt(project, work, changed)
    assert error.value.code == "WORK_RECEIPT_CONFLICT"
    exported = ledger.export_dataset(project)
    assert len(exported["receipts"]) == 1
    assert exported["receipts"][0]["usage"]["input_tokens"] == 3


def test_receipt_requires_attempt_and_same_work(setup):
    ledger, project, work = setup
    attempt = start(ledger, project, work)
    other_work = uuid4()
    ledger.create_work(project, other_work, "product-2")
    for owner, value in ((work, uuid4()), (other_work, attempt)):
        with pytest.raises(GatewayError) as error:
            ledger.record_receipt(project, owner, receipt(value))
        assert error.value.code == "WORK_NOT_FOUND"


def test_provider_id_unique_per_project_and_provider(setup):
    ledger, project, work = setup
    first, second = start(ledger, project, work), start(ledger, project, work)
    ledger.record_receipt(project, work, receipt(first, provider_request_id="req-1"))
    with pytest.raises(GatewayError) as error:
        ledger.record_receipt(project, work, receipt(second, provider_request_id="req-1"))
    assert error.value.code == "WORK_PROVIDER_CONFLICT"
    other = uuid4()
    ledger.create_work(other, work, "product-1")
    third = start(ledger, other, work)
    ledger.record_receipt(other, work, receipt(third, provider_request_id="req-1"))


def test_concurrent_start_same_attempt_and_receipt_are_idempotent(setup):
    ledger, project, work = setup
    attempt, barrier = uuid4(), Barrier(2)
    def run():
        other = AdWorkLedger(ledger.path)
        barrier.wait()
        start(other, project, work, attempt=attempt)
        other.record_receipt(project, work, receipt(attempt))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    dataset = ledger.export_dataset(project)
    assert len(dataset["attempts"]) == len(dataset["receipts"]) == 1


def test_provider_id_race_commits_only_one_receipt(setup):
    ledger, project, work = setup
    attempts, barrier = [start(ledger, project, work) for _ in range(2)], Barrier(2)
    def run(attempt):
        other = AdWorkLedger(ledger.path)
        barrier.wait()
        try:
            other.record_receipt(project, work, receipt(attempt, provider_request_id="same-request"))
            return "OK"
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, attempts)) == ["OK", "WORK_PROVIDER_CONFLICT"]
    assert len(ledger.export_dataset(project)["receipts"]) == 1


def test_pending_or_unknown_text_request_blocks_recall(setup):
    ledger, project, work = setup
    first = start(ledger, project, work, request_hash="c" * 64)
    for terminal in (None, "UNKNOWN"):
        if terminal:
            ledger.record_receipt(project, work, receipt(first, status=terminal))
        with pytest.raises(GatewayError) as error:
            start(ledger, project, work, request_hash="c" * 64)
        assert error.value.code == "WORK_PENDING"
    assert len(ledger.export_dataset(project)["attempts"]) == 1


def test_parent_must_belong_to_same_work(setup):
    ledger, project, work = setup
    parent = start(ledger, project, work)
    other = uuid4()
    ledger.create_work(project, other, "product-2")
    with pytest.raises(GatewayError) as error:
        start(ledger, project, other, parent=parent)
    assert error.value.code == "WORK_PARENT_CONFLICT"


def test_image_sync_parent_timestamp_failure_rolls_back_attempt_and_binding(setup):
    ledger, project, work = setup
    parent = start(ledger, project, work)
    job = uuid4()
    ledger.bind_image_job(project, work, job)
    identity = {"project_id": str(project), "work_id": str(work), "attempt_id": str(uuid4()),
                "stage": "image_generation", "operation": "generate", "provider": "openai",
                "configured_model": "model-v1", "prompt_version": "prompt-v1",
                "request_hash": "d" * 64, "parent_attempt_id": str(parent),
                "started_at": ledger.get_work(project, work)["created_at"]}
    with pytest.raises(GatewayError) as error:
        ledger.sync_image_attempt(project, job, identity, None)
    assert error.value.code == "WORK_PARENT_CONFLICT"
    dataset = ledger.export_dataset(project)
    assert len(dataset["attempts"]) == 1
    assert dataset["image_bindings"][0]["attempt_id"] is None


def test_cancel_accepts_late_receipt_but_no_new_attempt_or_job(setup):
    ledger, project, work = setup
    pending = start(ledger, project, work)
    ledger.cancel(project, work)
    ledger.record_receipt(project, work, receipt(pending, status="FAILED"))
    for action in (lambda: start(ledger, project, work),
                   lambda: start(ledger, project, work, attempt=pending),
                   lambda: ledger.bind_image_job(project, work, uuid4()),
                   lambda: ledger.cancel(project, work),
                   lambda: ledger.complete(project, work, completion([pending]))):
        with pytest.raises(GatewayError) as error:
            action()
        assert error.value.code == "WORK_CLOSED"
    assert ledger.get_work(project, work)["status"] == "CANCELLED"
    assert ledger.export_dataset(project)["receipts"][0]["status"] == "FAILED"


def test_cancel_late_bound_image_sync_persists_and_is_one_attempt(setup):
    ledger, project, work = setup
    job, attempt = uuid4(), uuid4()
    ledger.bind_image_job(project, work, job)
    ledger.cancel(project, work)
    spec = {"attempt_id": str(attempt), "stage": "image_generation", "operation": "generate",
            "provider": "openai", "configured_model": "model-v1", "prompt_version": "prompt-v1",
            "request_hash": "d" * 64, "parent_attempt_id": None}
    observed = receipt(attempt, artifact="a" * 64)
    ledger.sync_image_attempt(project, job, spec, None)
    reopened = AdWorkLedger(ledger.path)
    reopened.sync_image_attempt(project, job, spec, observed)
    reopened.sync_image_attempt(project, job, spec, observed)
    assert reopened.get_image_binding(project, job) == str(work)
    dataset = reopened.export_dataset(project)
    assert len(dataset["attempts"]) == len(dataset["receipts"]) == 1
    assert dataset["image_bindings"][0]["attempt_id"] == str(attempt)
    with pytest.raises(GatewayError) as error:
        reopened.sync_image_attempt(project, job, {**spec, "attempt_id": str(uuid4())}, None)
    assert error.value.code == "WORK_BINDING_CONFLICT"


def test_job_binding_cannot_move_between_works(setup):
    ledger, project, work = setup
    other, job = uuid4(), uuid4()
    ledger.create_work(project, other, "product-2")
    ledger.bind_image_job(project, work, job)
    with pytest.raises(GatewayError) as error:
        ledger.bind_image_job(project, other, job)
    assert error.value.code == "WORK_BINDING_CONFLICT"


def test_completion_requires_exact_attempts_stages_and_final_hash(setup):
    ledger, project, work = setup
    attempts = []
    for stage in ("prompt_build", "image_generation", "review", "composition"):
        local = stage == "composition"
        attempt = start(ledger, project, work, stage=stage, local=local)
        attempts.append(attempt)
        if stage == "review":
            with pytest.raises(GatewayError) as error:
                ledger.complete(project, work, completion(attempts))
            assert error.value.code == "WORK_PENDING"
        ledger.record_receipt(project, work, receipt(attempt, artifact="a" * 64 if local else None,
                                                     local=local))
    for value in (completion(attempts[:-1]), completion(attempts, artifact="e" * 64)):
        with pytest.raises(GatewayError) as error:
            ledger.complete(project, work, value)
        assert error.value.code == "WORK_COMPLETION_INVALID"
    ledger.complete(project, work, completion(attempts))
    ledger.complete(project, work, completion(list(reversed(attempts))))
    assert ledger.get_work(project, work)["status"] == "COMPLETED"
    assert ledger.export_dataset(project)["works"][0]["completion"]["quality_pass"] is True
    ledger.record_receipt(project, work, receipt(attempts[-1], artifact="a" * 64, local=True))
    for action in (lambda: start(ledger, project, work),
                   lambda: ledger.cancel(project, work),
                   lambda: ledger.complete(project, work, completion(attempts, "f" * 64))):
        with pytest.raises(GatewayError) as error:
            action()
        assert error.value.code == "WORK_CLOSED"


def test_unobserved_bound_job_prevents_completion(setup):
    ledger, project, work = setup
    minimal = uuid4()
    ledger.create_work(project, minimal, "product-2", required_stages=["composition"])
    attempt = start(ledger, project, minimal, stage="composition", local=True)
    ledger.record_receipt(project, minimal, receipt(attempt, local=True, artifact="a" * 64))
    ledger.bind_image_job(project, minimal, uuid4())
    with pytest.raises(GatewayError) as error:
        ledger.complete(project, minimal, completion([attempt]))
    assert error.value.code == "WORK_PENDING"


def test_missing_successful_stage_prevents_completion(setup):
    ledger, project, work = setup
    attempt = start(ledger, project, work, stage="composition", local=True)
    ledger.record_receipt(project, work, receipt(attempt, local=True, artifact="a" * 64))
    with pytest.raises(GatewayError) as error:
        ledger.complete(project, work, completion([attempt]))
    assert error.value.code == "WORK_MISSING_STAGE"


def test_pair_and_attempt_bounds_are_enforced(setup, monkeypatch):
    from beauty_ai import work_contracts
    ledger, project, work = setup
    ledger.create_work(project, uuid4(), "product-1", task_pair_id="pair-1", arm="baseline")
    with pytest.raises(GatewayError) as error:
        ledger.create_work(project, uuid4(), "product-1", task_pair_id="pair-1", arm="baseline")
    assert error.value.code == "WORK_PAIR_CONFLICT"
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 1)
    start(ledger, project, work)
    with pytest.raises(GatewayError) as error:
        start(ledger, project, work)
    assert error.value.code == "WORK_LIMIT"


def test_export_and_readonly_reader_are_consistent_and_do_not_create_schema(setup, tmp_path):
    ledger, project, work = setup
    attempt = start(ledger, project, work)
    ledger.record_receipt(project, work, receipt(attempt, status="FAILED"))
    expected = ledger.export_dataset(project, data_kind="SYNTHETIC_FIXTURE")
    before = ledger.path.read_bytes()
    assert read_work_dataset(ledger.path, project, data_kind="SYNTHETIC_FIXTURE") == expected
    assert ledger.path.read_bytes() == before
    absent = tmp_path / "absent.sqlite3"
    with pytest.raises(GatewayError) as error:
        read_work_dataset(absent, project)
    assert error.value.code == "WORK_STORAGE_ERROR"
    assert not absent.exists()
    incompatible = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(incompatible) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    before = incompatible.read_bytes()
    with pytest.raises(GatewayError):
        read_work_dataset(incompatible, project)
    assert incompatible.read_bytes() == before


def test_local_stage_requires_explicit_zero_api_evidence(setup):
    ledger, project, work = setup
    attempt = start(ledger, project, work, stage="composition", local=True)
    with pytest.raises(GatewayError) as error:
        ledger.record_receipt(project, work, receipt(attempt, model="local"))
    assert error.value.code == "WORK_RECEIPT_INVALID"
    assert ledger.export_dataset(project)["receipts"] == []


def test_export_limit_raises_instead_of_returning_partial_dataset(setup, monkeypatch):
    from beauty_ai import work_contracts
    ledger, project, work = setup
    monkeypatch.setattr(work_contracts, "MAX_WORKS", 0)
    with pytest.raises(GatewayError) as error:
        ledger.export_dataset(project)
    assert error.value.code == "WORK_LIMIT"
