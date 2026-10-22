"""Durable telemetry is committed with completion and never inferred for old jobs."""
import json
import sqlite3
import time
from uuid import uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import AssetRef, CutoutRequest, CutoutResult, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.execution_trace import phase, recording, set_backend
from beauty_ai.image_worker import SingleHostWorker
from beauty_ai.job_ledger import JobLedger


def request():
    return CutoutRequest(source=AssetRef(asset_id=uuid4(), sha256="a" * 64, width=8,
                                       height=8, mime_type="image/png"),
                         pipeline_profile_id="test", pipeline_revision="1")


def result(req):
    return CutoutResult(product_rgba=req.source, preserve_mask=req.source,
                        needs_manual_check=True, warnings=["MANUAL_CHECK_REQUIRED"])


def setup(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    job, project, req = uuid4(), uuid4(), request()
    ledger.submit(job, project, req)
    return ledger, job, project, req


def packet(job, project, req, outcome="succeeded"):
    with recording(job_id=job, project_id=project, request=req) as recorder:
        with recorder.processor(), phase("preprocess"):
            pass
        return recorder.finish(outcome=outcome)


def assert_code(code, callback):
    with pytest.raises(GatewayError) as caught:
        callback()
    assert caught.value.code == code
    assert "secret" not in str(caught.value)
    return caught.value


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "failed", "interrupted"])
def test_unrecorded_jobs_remain_unrecorded_after_schema_migration(tmp_path, state):
    ledger, job, project, req = setup(tmp_path)
    if state != "queued":
        ledger.claim_next("worker")
    if state == "succeeded":
        ledger.finish(job, "worker", result=result(req))
    elif state == "failed":
        ledger.finish(job, "worker", error_code="IMAGE_PROCESSING_FAILED")
    elif state == "interrupted":
        ledger.recover_interrupted()
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TABLE job_executions")
    reopened = JobLedger(ledger.path)
    observed = reopened.execution(job, project)
    assert observed.status == state
    assert observed.request_hash == content_hash(req)
    assert observed.recording_status == "NOT_RECORDED"
    assert observed.execution is None
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT count(*) FROM job_executions").fetchone()[0] == 0


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_finish_persists_observation_with_terminal_state_and_scoped_read(tmp_path, outcome):
    ledger, job, project, req = setup(tmp_path)
    ledger.claim_next("worker")
    observation = packet(job, project, req, outcome)
    kwargs = {"result": result(req)} if outcome == "succeeded" else {"error_code": "IMAGE_PROCESSING_FAILED"}
    finished = ledger.finish(job, "worker", execution=observation, **kwargs)
    reopened = JobLedger(ledger.path)
    observed = reopened.execution(job, project)
    assert finished.status == observed.status == outcome
    assert observed.recording_status == "RECORDED"
    assert observed.execution == observation
    assert_code("IMAGE_JOB_NOT_FOUND", lambda: reopened.execution(job, uuid4()))
    assert_code("IMAGE_JOB_NOT_FOUND", lambda: reopened.execution(uuid4(), project))
    assert_code("JOB_STATE_CONFLICT", lambda: ledger.finish(job, "worker", execution=observation, **kwargs))
    assert reopened.execution(job, project) == observed


@pytest.mark.parametrize("change", [
    {"job_id": uuid4()}, {"project_id": uuid4()}, {"request_hash": "f" * 64},
    {"outcome": "failed"}, {"pipeline_profile_id": "different"},
    {"pipeline_revision": "2"}, {"seed": 7}, {"worker_elapsed_ms": -1},
])
def test_mismatched_packet_does_not_finish_job_or_leave_partial_observation(tmp_path, change):
    ledger, job, project, req = setup(tmp_path)
    ledger.claim_next("worker")
    observation = packet(job, project, req).model_copy(update=change)
    assert_code("JOB_STATE_CONFLICT", lambda: ledger.finish(job, "worker", result=result(req), execution=observation))
    assert ledger.get(job, project).status == "running"
    assert ledger.execution(job, project).recording_status == "NOT_RECORDED"


def test_failed_observation_insert_rolls_back_success_job_update(tmp_path):
    ledger, job, project, req = setup(tmp_path)
    ledger.claim_next("worker")
    observation = packet(job, project, req)
    with sqlite3.connect(ledger.path) as db:
        db.execute("""CREATE TRIGGER fail_observation BEFORE INSERT ON job_executions
                      BEGIN SELECT RAISE(ABORT, 'secret storage failure'); END""")
    assert_code("JOB_STORAGE_ERROR", lambda: ledger.finish(job, "worker", result=result(req), execution=observation))
    assert JobLedger(ledger.path).get(job, project).status == "running"
    assert ledger.execution(job, project).execution is None
    with sqlite3.connect(ledger.path) as db:
        db.execute("DROP TRIGGER fail_observation")
    ledger.finish(job, "worker", result=result(req), execution=observation)
    assert ledger.execution(job, project).execution == observation


@pytest.mark.parametrize("corruption", [
    "invalid_json", "extra_text", "nonfinite", "unknown_phase", "oversized", "wrong_project",
    "wrong_hash", "wrong_status", "packet_project", "packet_hash", "packet_profile", "packet_outcome",
])
def test_corrupt_stored_observation_is_safe_storage_error_not_success_or_missing(tmp_path, corruption):
    ledger, job, project, req = setup(tmp_path)
    ledger.claim_next("worker")
    observation = packet(job, project, req)
    ledger.finish(job, "worker", result=result(req), execution=observation)
    data = observation.model_dump(mode="json")
    column, value = "execution_json", None
    if corruption == "invalid_json":
        value = "secret malformed JSON"
    elif corruption == "oversized":
        value = "secret" * 12000
    elif corruption == "extra_text":
        data["raw_exception"] = "secret traceback"
    elif corruption == "nonfinite":
        data["worker_elapsed_ms"] = float("nan")
    elif corruption == "unknown_phase":
        data["phases"][0]["name"] = "secret prompt"
    elif corruption == "wrong_project":
        column, value = "project_id", str(uuid4())
    elif corruption == "wrong_hash":
        column, value = "request_hash", "f" * 64
    elif corruption == "wrong_status":
        column, value = "status", "failed"
    elif corruption == "packet_project":
        data["project_id"] = str(uuid4())
    elif corruption == "packet_hash":
        data["request_hash"] = "f" * 64
    elif corruption == "packet_profile":
        data["pipeline_profile_id"] = "wrong"
    elif corruption == "packet_outcome":
        data["outcome"] = "failed"
    if value is None:
        value = json.dumps(data)
    with sqlite3.connect(ledger.path) as db:
        db.execute(f"UPDATE job_executions SET {column}=? WHERE job_id=?", (value, str(job)))
    error = assert_code("JOB_STORAGE_ERROR", lambda: ledger.execution(job, project))
    assert error.retryable is False
    assert error.__cause__ is None
    assert ledger.get(job, project).status == "succeeded"
    assert_code("IMAGE_JOB_NOT_FOUND", lambda: ledger.execution(job, uuid4()))


def test_claim_carries_actual_queue_times_and_recovery_adds_no_observation(tmp_path, monkeypatch):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    stamps = iter(["2026-10-22T00:00:00+00:00", "2026-10-22T00:00:02+00:00",
                   "2026-10-22T00:00:05+00:00"])
    monkeypatch.setattr(ledger, "_now", lambda: next(stamps))
    job, project, req = uuid4(), uuid4(), request()
    ledger.submit(job, project, req)
    claimed = ledger.claim_next("worker")
    assert claimed.created_at == "2026-10-22T00:00:00+00:00"
    assert claimed.claimed_at == "2026-10-22T00:00:02+00:00"
    ledger.recover_interrupted()
    assert ledger.execution(job, project).execution is None
    assert ledger.timing(job, project).ledger_elapsed_ms == 5000


@pytest.mark.parametrize("failure", [None, "processor", "validation"])
def test_worker_commits_real_phase_and_queue_observations_with_result(tmp_path, monkeypatch, failure):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    assets = LocalAssetStore(tmp_path / "assets")
    project, job = uuid4(), uuid4()
    source = assets.put_image(Image.new("RGB", (8, 8), "blue"), project_id=project)
    req = CutoutRequest(source=source, pipeline_profile_id="test", pipeline_revision="1")
    product = assets.put_image(Image.new("RGBA", (8, 8), (10, 20, 30, 255)), project_id=project)
    mask = assets.put_image(Image.new("L", (8, 8), 255), project_id=project)
    output = CutoutResult(product_rgba=product, preserve_mask=mask,
                          needs_manual_check=True, warnings=["MANUAL_CHECK_REQUIRED"])
    # Startup recovery records its own timestamp even when no running rows exist.
    stamps = iter(["2026-10-22T00:00:00+00:00", "2026-10-22T00:00:01+00:00",
                   "2026-10-22T00:00:02+00:00", "2026-10-22T00:00:05+00:00"])
    monkeypatch.setattr(ledger, "_now", lambda: next(stamps))
    ledger.submit(job, project, req)

    def processor(request, received_project):
        assert received_project == project
        set_backend("cpu", "test-model", False)
        with phase("model_inference"):
            if failure == "processor":
                raise RuntimeError("secret processor exception")
        return {"secret": "invalid return"} if failure == "validation" else output

    worker = SingleHostWorker(ledger, assets, processor, poll_seconds=.01)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while ledger.get(job, project).status in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.01)
        assert ledger.get(job, project).status == ("failed" if failure else "succeeded")
    finally:
        assert worker.stop(timeout=5)
    assert worker.fatal_error_code is None
    observation = ledger.execution(job, project).execution
    assert observation.request_hash == content_hash(req)
    assert observation.queue_elapsed_ms == 2000
    assert observation.worker_elapsed_ms >= observation.processor_elapsed_ms >= 0
    assert observation.backend.device == "cpu"
    assert observation.processor_outcome == ("failed" if failure == "processor" else "completed")
    phases = {p.name: p.outcome for p in observation.phases}
    assert phases["model_inference"] == ("failed" if failure == "processor" else "completed")
    if failure == "processor":
        assert "result_validation" not in phases
    else:
        assert phases["result_validation"] == ("failed" if failure == "validation" else "completed")
    assert "secret" not in observation.model_dump_json()
    assert ledger.timing(job, project).ledger_elapsed_ms == 5000
