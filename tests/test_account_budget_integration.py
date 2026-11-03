"""Paid-call permission and two-ledger crash behavior with zero real HTTP."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
import sqlite3
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from beauty_ai.account_budget import AccountBudgetPolicy, AccountBudgetStore, BudgetPermit
from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError
from beauty_ai.provider_attempts import ProviderAttemptStore


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **kw: pytest.fail("No real transport"))


def budget(tmp_path, *, limit="10", stop="0.9"):
    return AccountBudgetStore(tmp_path / "account.sqlite3", AccountBudgetPolicy(
        account_ref="team", period_id="2026-11", starts_at="2026-11-01T00:00:00Z",
        ends_at="2026-12-01T00:00:00Z", hard_limit_usd=limit, opening_spend_usd="0",
        opening_evidence_ref="operator:explicit-zero", stop_ratio=stop),
        clock=lambda: datetime(2026, 11, 3, tzinfo=timezone.utc))


def request():
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
        prompt=ImagePrompt(positive="밝은 제품 배경", prompt_version="test-v1", source_revision=1))


def success():
    output = BytesIO()
    with Image.new("RGB", (1024, 1024), "white") as image:
        image.save(output, format="PNG")
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(output.getvalue()).decode()}],
                                    "usage": {"total_tokens": 2}})


def processor(tmp_path, shared, calls, *, local_limit="10", handler=None):
    def handle(req):
        calls.append(req)
        return handler(req) if handler else success()
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle))
    return ExternalImageProcessor(LocalAssetStore(tmp_path / "assets"), adapter,
        ProviderAttemptStore(tmp_path / "attempts.sqlite3"), budget_limit_usd=local_limit,
        account_budget=shared)


def snapshot(shared):
    return shared.snapshot("team", "2026-11")


def test_direct_live_adapter_requires_real_permit_before_key_or_client():
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(live_calls=True))
    fake = BudgetPermit(object(), "team", "a" * 64, content_hash(request()), str(uuid4()))
    for permit in (None, object(), fake):
        with pytest.raises(OpenAIImagesError, match="^ACCOUNT_BUDGET_REQUIRED$"):
            adapter.execute(request(), [], [], client_request_id=uuid4(), budget_permit=permit)


def test_shared_database_cannot_be_reopened_as_experiment_ledger(tmp_path):
    shared = budget(tmp_path)
    with pytest.raises(GatewayError) as error:
        ProviderAttemptStore(shared.path)
    assert error.value.code == "PROVIDER_ATTEMPT_STORAGE_ERROR"
    with sqlite3.connect(shared.path) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_attempts'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='experiment_limits'").fetchone() is None
    assert snapshot(shared)["held_reservations_usd"] == "0.000000000"


def test_live_processor_without_budget_has_no_attempt_even_with_missing_key(tmp_path):
    attempts = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    proc = ExternalImageProcessor(LocalAssetStore(tmp_path / "assets"),
        OpenAIImagesAdapter(OpenAIImagesConfig(live_calls=True)), attempts, budget_limit_usd="10")
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "ACCOUNT_BUDGET_REQUIRED" and attempts.get(job, project) is None


def test_shared_mock_follows_reserved_bound_submitted_then_local_finish(tmp_path):
    shared, calls = budget(tmp_path), []
    proc = processor(tmp_path / "experiment", shared, calls)
    project, job, payload = uuid4(), uuid4(), request()
    result = proc.process_job(payload, project, job)
    receipt = proc.attempts.get(job, project)
    with sqlite3.connect(shared.path) as db:
        row = db.execute("SELECT state,attempt_id,request_hash FROM reservations WHERE reservation_id=?",
                         (receipt["account_reservation_id"],)).fetchone()
    assert row == ("SUBMITTED", str(result.attempt_id), content_hash(payload))
    before = snapshot(shared)
    assert proc.process_job(payload, project, job) == result
    assert proc.process_job(payload, project, job) == result
    assert snapshot(shared) == before and len(calls) == 1
    assert before["reservation_counts"] == {"HELD": 0, "SUBMITTED": 1, "SETTLED": 0}
    assert before["held_reservations_usd"] == "1.000000000"
    assert len(proc.attempts.get_reuse_events(job, project)) == 2


def test_input_failure_does_not_reserve_shared_or_local(tmp_path, monkeypatch):
    shared, calls = budget(tmp_path), []
    proc = processor(tmp_path / "experiment", shared, calls)
    def fail(*a, **kw):
        raise OpenAIImagesError("PROVIDER_INPUT_INVALID")
    monkeypatch.setattr(proc.adapter, "validate_input", fail)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_INPUT_INVALID" and calls == []
    assert proc.attempts.get(job, project) is None and snapshot(shared)["held_reservations_usd"] == "0.000000000"


def test_shared_denial_precedes_local_begin(tmp_path):
    shared, calls = budget(tmp_path, limit="0"), []
    proc = processor(tmp_path / "experiment", shared, calls)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "ACCOUNT_BUDGET_EXCEEDED"
    assert proc.attempts.get(job, project) is None and calls == []


@pytest.mark.parametrize("failure", ["local_budget", "local_storage"])
def test_shared_hold_survives_local_begin_failure_and_restart(tmp_path, monkeypatch, failure):
    shared, calls = budget(tmp_path), []
    proc = processor(tmp_path / "experiment", shared, calls, local_limit="0" if failure == "local_budget" else "10")
    if failure == "local_storage":
        def fail(*a, **kw):
            raise GatewayError("PROVIDER_ATTEMPT_STORAGE_ERROR", "safe storage failure")
        monkeypatch.setattr(proc.attempts, "begin", fail)
    project, job, payload = uuid4(), uuid4(), request()
    with pytest.raises(GatewayError):
        proc.process_job(payload, project, job)
    assert calls == [] and snapshot(shared)["reservation_counts"]["HELD"] == 1
    assert proc.attempts.get(job, project) is None
    restarted = processor(tmp_path / "experiment", shared, calls)
    with pytest.raises(GatewayError) as error:
        restarted.process_job(payload, project, job)
    assert error.value.code == "ACCOUNT_BUDGET_RESERVATION_CONFLICT" and calls == []


def test_bind_failure_keeps_held_and_safe_error_in_local_receipt(tmp_path, monkeypatch):
    shared, calls = budget(tmp_path), []
    proc = processor(tmp_path / "experiment", shared, calls)
    def fail(*a, **kw):
        raise GatewayError("ACCOUNT_BUDGET_STORAGE_ERROR", "private database path")
    monkeypatch.setattr(shared, "bind_attempt", fail)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    receipt = proc.attempts.get(job, project)
    assert error.value.code == receipt["error_code"] == "ACCOUNT_BUDGET_STORAGE_ERROR"
    assert "private" not in str(error.value)
    assert receipt["state"] == "failed" and receipt["elapsed_seconds"] is None
    assert calls == [] and snapshot(shared)["reservation_counts"]["HELD"] == 1


def test_claim_after_zero_settlement_blocks_post_and_preserves_error(tmp_path, monkeypatch):
    shared, calls = budget(tmp_path), []
    proc = processor(tmp_path / "experiment", shared, calls)
    original = shared.bind_attempt
    def settle_before_execute(reservation, attempt):
        permit = original(reservation, attempt)
        shared.settle(reservation, "unsubmitted", "0", "e" * 64)
        return permit
    monkeypatch.setattr(shared, "bind_attempt", settle_before_execute)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "ACCOUNT_BUDGET_RESERVATION_CONFLICT"
    assert proc.attempts.get(job, project)["error_code"] == error.value.code and calls == []
    assert snapshot(shared)["reservation_counts"]["SETTLED"] == 1


@pytest.mark.parametrize("mismatch", ["request", "attempt", "replay"])
def test_permit_binds_hash_attempt_and_one_submission(tmp_path, mismatch):
    shared, calls = budget(tmp_path), []
    payload, attempt = request(), uuid4()
    reservation = shared.reserve(uuid4(), uuid4(), content_hash(payload), "1")
    permit = shared.bind_attempt(reservation, attempt)
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda req: (calls.append(req), success())[1]))
    if mismatch == "request":
        payload = payload.model_copy(update={"quality": "high"})
    if mismatch == "attempt":
        attempt = uuid4()
    if mismatch == "replay":
        adapter.execute(payload, [], [], client_request_id=attempt, budget_permit=permit)
    before = len(calls)
    with pytest.raises(OpenAIImagesError, match="^ACCOUNT_BUDGET_RESERVATION_CONFLICT$"):
        adapter.execute(payload, [], [], client_request_id=attempt, budget_permit=permit)
    assert len(calls) == before


def test_response_loss_never_refunds_or_resubmits_after_restart(tmp_path):
    shared, calls = budget(tmp_path), []
    def timeout(req):
        raise httpx.ReadTimeout("private raw response")
    proc = processor(tmp_path / "experiment", shared, calls, handler=timeout)
    project, job, payload = uuid4(), uuid4(), request()
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, project, job)
    assert error.value.code == "PROVIDER_SUBMISSION_UNKNOWN"
    before = snapshot(shared)
    assert before["reservation_counts"]["SUBMITTED"] == 1
    restarted = processor(tmp_path / "experiment", shared, calls)
    with pytest.raises(GatewayError):
        restarted.process_job(payload, project, job)
    assert snapshot(shared) == before and len(calls) == 1


def test_separate_experiment_ledgers_compete_only_in_shared_budget(tmp_path):
    shared, calls = budget(tmp_path, limit="1", stop="1"), []
    processors = [processor(tmp_path / str(index), shared, calls) for index in range(4)]
    jobs = [(uuid4(), uuid4()) for _ in processors]
    def run(index):
        project, job = jobs[index]
        try:
            processors[index].process_job(request(), project, job)
            return "success"
        except GatewayError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert results.count("success") == 1 and results.count("ACCOUNT_BUDGET_EXCEEDED") == 3
    assert len(calls) == 1
    assert sum(proc.attempts.get(job, project) is not None for proc, (project, job) in zip(processors, jobs)) == 1
    assert all(proc.attempts.experiment_status() is None for proc in processors)
