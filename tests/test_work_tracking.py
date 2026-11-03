import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest

from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.work_contracts import digest
from beauty_ai.work_ledger import AdWorkLedger
from beauty_ai.work_tracking import (TextWorkCapture, WorkCapturePending, current_ad_work,
    make_work_receipt, record_local_stage, reconcile_work_receipts, resolve_image_work,
    sync_image_receipt, track_ad_work)


@pytest.fixture
def workspace(tmp_path):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work = uuid4(), uuid4()
    ledger.create_work(project, work, "test-product")
    return ledger, project, work


async def test_context_isolation_and_reset_across_concurrent_tasks(workspace):
    ledger, project, first = workspace
    second = uuid4()
    ledger.create_work(project, second, "other-product")

    async def run(work):
        with track_ad_work(ledger, project, work):
            await asyncio.sleep(0)
            assert current_ad_work().work_id == work
            with track_ad_work(ledger, project, work):
                assert current_ad_work().work_id == work
            assert current_ad_work().work_id == work
        assert current_ad_work() is None

    await asyncio.gather(run(first), run(second))
    assert current_ad_work() is None
    with track_ad_work(ledger, project, first):
        with pytest.raises(GatewayError, match="Work") as error:
            with track_ad_work(ledger, project, second):
                pass
        assert error.value.code == "WORK_SCOPE_CONFLICT"
        assert current_ad_work().work_id == first
    assert current_ad_work() is None


def test_image_bindings_reconcile_cancelled_work_and_stable_provenance(workspace):
    ledger, project, work = workspace
    job = uuid4()
    request = ExternalImageRequest(operation="generate", prompt=ImagePrompt(
        positive="cosmetics", prompt_version="v1", source_revision=1))
    resolve_image_work(ledger, project, job, work)
    record = {"job_id": str(job), "attempt_id": str(uuid4()), "request_hash": content_hash(request),
        "created_at": datetime.now(timezone.utc).isoformat(), "state": "submitting",
        "usage": None, "raw_usage": None, "metadata": None, "result": None}
    ledger.cancel(project, work)
    sync_image_receipt(ledger, project, job, record, request)
    assert ledger.export_dataset(project)["receipts"] == []
    record.update(state="succeeded", provider_request_id="request-test", elapsed_seconds=1.25,
        usage={"input_tokens": 10, "output_tokens": 4},
        raw_usage={"input_tokens": 999, "output_tokens": 4, "input_tokens_details": {"cached_tokens": 3}},
        result={"image": {"sha256": "a" * 64}})
    sync_image_receipt(ledger, project, job, record, request)
    receipt = ledger.export_dataset(project)["receipts"][0]
    assert receipt["usage"]["input_tokens"] == 10  # normalized wins
    assert receipt["usage"]["cached_input_tokens"] == 3
    assert receipt["usage"]["input_text_tokens"] is None
    assert receipt["artifact_sha256"] == "a" * 64
    assert receipt["estimated_cost_usd"] is None
    record.update(updated_at="2099-01-01", reuse_events=[{"event": "different"}])
    sync_image_receipt(ledger, project, job, record, request)
    assert ledger.export_dataset(project)["receipts"] == [receipt]
    facts = {k: v for k, v in receipt.items() if k not in {"source_ref", "source_sha256"}}
    assert receipt["source_sha256"] == digest(facts)
    assert len(ledger.export_dataset(project)["attempts"]) == 1


def test_local_step_records_explicit_zero_api_cost_not_cpu_or_invoice(workspace):
    ledger, project, work = workspace
    attempt = record_local_stage(ledger, project, work, artifact_sha256="b" * 64, elapsed_seconds=0.125)
    receipt = ledger.export_dataset(project)["receipts"][0]
    assert receipt["attempt_id"] == attempt
    assert receipt["usage"]["input_tokens"] == receipt["usage"]["output_tokens"] == 0
    assert receipt["estimated_cost_usd"] == "0.000000000"
    assert receipt["rate_version"] == "local-api-zero-v1"
    assert receipt["actual_cost_usd"] is None


@pytest.mark.parametrize("changes", [{"artifact_sha256": "wrong"}, {"elapsed_seconds": -1},
    {"status": "PENDING"}, {"error_code": "private error"}])
def test_invalid_local_observation_does_not_leave_a_pending_attempt(workspace, changes):
    ledger, project, work = workspace
    with pytest.raises(ValueError, match="WORK_"):
        record_local_stage(ledger, project, work, **changes)
    assert ledger.export_dataset(project)["attempts"] == []


def test_durable_inbox_recovers_without_sdk_and_rejects_repeat(workspace, monkeypatch):
    ledger, project, work = workspace
    with track_ad_work(ledger, project, work) as scope:
        capture = TextWorkCapture(scope, operation="review_copy", model="model-v1",
                                  prompt_version="p1", request_payload={"input": "private"})
        capture.start()
        capture.observe(SimpleNamespace(http_response=httpx.Response(200,
            headers={"x-request-id": "req-one"}, json={"model": "model-v1",
                "usage": {"input_tokens": 20, "output_tokens": 5}})))
        real_record = ledger.record_receipt
        monkeypatch.setattr(ledger, "record_receipt", lambda *a: (_ for _ in ()).throw(
            GatewayError("WORK_STORAGE_ERROR", "private error")))
        with pytest.raises(WorkCapturePending) as error:
            capture.finish("SUCCEEDED")
        assert error.value.recovery_saved is True
        saved = list(ledger.path.parent.glob(".cache/work-receipts/*/*/*.json"))
        assert len(saved) == 1 and saved[0].stat().st_mode & 0o777 == 0o600
        assert "private" not in saved[0].read_text()
        monkeypatch.setattr(ledger, "record_receipt", real_record)
    restarted = AdWorkLedger(ledger.path)
    with track_ad_work(restarted, project, work) as scope:
        assert len(restarted.export_dataset(project)["receipts"]) == 1
        again = TextWorkCapture(scope, operation="review_copy", model="model-v1",
                                prompt_version="p1", request_payload={"input": "private"})
        with pytest.raises(GatewayError) as error:
            again.start()
        assert error.value.code == "WORK_ALREADY_RECORDED"
    assert not list(ledger.path.parent.glob(".cache/work-receipts/*/*/*.json"))


def test_inbox_and_database_failure_preserve_safe_manual_receipt(workspace, monkeypatch):
    import beauty_ai.work_tracking as tracking
    ledger, project, work = workspace
    with track_ad_work(ledger, project, work) as scope:
        capture = TextWorkCapture(scope, operation="review_copy", model="model-v1",
                                  prompt_version="p1", request_payload={"prompt": "private"})
        capture.start()
        monkeypatch.setattr(tracking, "_save_receipt", lambda *a: (_ for _ in ()).throw(OSError("private")))
        with pytest.raises(WorkCapturePending) as error:
            capture.finish("UNKNOWN", "TEXT_DEADLINE")
        assert error.value.recovery_saved is False
        assert "private" not in json.dumps(error.value.receipt)
        ledger.record_receipt(project, work, error.value.receipt)
        assert ledger.export_dataset(project)["receipts"][0]["status"] == "UNKNOWN"


def test_response_object_id_is_not_mislabelled_as_a_provider_request_id(workspace):
    ledger, project, work = workspace
    with track_ad_work(ledger, project, work) as scope:
        capture = TextWorkCapture(scope, operation="review_copy", model="model-v1",
                                  prompt_version="p1", request_payload={"input": "test"})
        capture.start()
        capture.observe(SimpleNamespace(http_response=httpx.Response(200, json={
            "id": "resp-safe-1", "model": "model-v1", "usage": {"input_tokens": 3, "output_tokens": 2}})))
        capture.finish("SUCCEEDED")
    receipt = ledger.export_dataset(project)["receipts"][0]
    assert receipt["provider_request_id"] is None
    assert receipt["usage"]["input_tokens"] == 3


def test_strict_text_registration_is_atomic_and_default_api_still_allows_new_known_attempts(workspace):
    ledger, project, work = workspace
    def start(attempt, strict=True):
        ledger.start_attempt(project, work, attempt, "review", "review_copy", "openai", "model-v1",
                             "v1", "a" * 64, reject_repeated_request=strict)

    def contender(_):
        attempt = uuid4()
        try:
            start(attempt)
            ledger.record_receipt(project, work, make_work_receipt(attempt, model="model-v1", status="SUCCEEDED"))
            return str(attempt)
        except GatewayError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(contender, range(8)))
    ids = [value for value in results if not value.startswith("WORK_")]
    assert len(ids) == 1
    assert all(value in {"WORK_PENDING", "WORK_ALREADY_RECORDED"} for value in results if value not in ids)
    start(UUID(ids[0]))  # exact same identity replay
    start(uuid4(), strict=False)  # explicit external caller retains original contract


def test_conflicting_scope_does_not_create_binding_in_another_ledger(workspace, tmp_path):
    ledger, project, work = workspace
    other = AdWorkLedger(tmp_path / "other.sqlite3")
    other.create_work(project, work, "test-product")
    job = uuid4()
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError) as error:
            resolve_image_work(other, project, job)
        assert error.value.code == "WORK_SCOPE_CONFLICT"
    assert other.get_image_binding(project, job) is None


def test_two_receipt_collectors_tolerate_another_consumer_removing_the_file(workspace, monkeypatch):
    import beauty_ai.work_tracking as tracking
    ledger, project, work = workspace
    attempt = uuid4()
    ledger.start_attempt(project, work, attempt, "review", "review_copy", "openai", "model",
                         "v1", "a" * 64)
    receipt = make_work_receipt(attempt, model="model", status="SUCCEEDED")
    saved = tracking._save_receipt(ledger, project, work, receipt)
    barrier, consumed, lock = Barrier(2), Event(), Lock()
    reader_count = 0
    read_bytes, unlink = Path.read_bytes, Path.unlink

    def read(path):
        nonlocal reader_count
        if path != saved:
            return read_bytes(path)
        with lock:
            reader_count += 1
            first = reader_count == 1
        barrier.wait(timeout=5)
        if not first:
            assert consumed.wait(5)
        return read_bytes(path)

    def remove(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path == saved:
            consumed.set()
        return result

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "unlink", remove)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile_work_receipts(ledger, project, work), range(2)))
    assert sorted(results) == [0, 1]
    assert ledger.export_dataset(project)["receipts"] == [receipt]
