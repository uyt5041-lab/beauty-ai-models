from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from uuid import uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundResult, CutoutRequest, CutoutResult
from beauty_ai.errors import GatewayError
from beauty_ai.image_worker import SingleHostWorker
from beauty_ai.job_ledger import JobLedger


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail("Worker condition did not complete before the test deadline")


def setup_runtime(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    assets = LocalAssetStore(tmp_path / "assets")
    project = uuid4()
    source = assets.put_image(Image.new("RGB", (8, 8), "blue"), project_id=project)
    request = CutoutRequest(source=source, pipeline_profile_id="test", pipeline_revision="1")
    return ledger, assets, project, request


def valid_result(assets, project):
    product = assets.put_image(Image.new("RGBA", (8, 8), (20, 40, 60, 255)), project_id=project)
    mask = assets.put_image(Image.new("L", (8, 8), 255), project_id=project)
    return CutoutResult(product_rgba=product, preserve_mask=mask,
                        needs_manual_check=True, warnings=["Synthetic test processor"])


def test_queued_jobs_are_discovered_from_sqlite_without_an_in_memory_queue(tmp_path):
    ledger, assets, project, request = setup_runtime(tmp_path)
    job_id = uuid4()
    ledger.submit(job_id, project, request)
    result = valid_result(assets, project)
    calls = []

    def processor(received, received_project):
        calls.append((received, received_project))
        return result

    # A new ledger instance models an API restart after commit, before wake-up.
    reopened = JobLedger(ledger.path)
    worker = SingleHostWorker(reopened, assets, processor, poll_seconds=.01)
    try:
        assert worker.start()
        wait_for(lambda: ledger.get(job_id, project).status == "succeeded")
        assert calls == [(request, project)]
        assert ledger.get(job_id, project).result == result
    finally:
        assert worker.stop(10)
    assert not worker.is_running
    assert worker.fatal_error_code is None


def test_contender_cannot_recover_running_job_and_stop_timeout_retains_lock(tmp_path):
    ledger, assets, project, request = setup_runtime(tmp_path)
    job_id = uuid4()
    ledger.submit(job_id, project, request)
    entered, release = threading.Event(), threading.Event()
    result = valid_result(assets, project)

    def slow_processor(*_):
        entered.set()
        assert release.wait(10)
        return result

    worker = SingleHostWorker(ledger, assets, slow_processor, poll_seconds=.01)
    contender = SingleHostWorker(JobLedger(ledger.path), assets, lambda *_: result)
    try:
        assert worker.start()
        assert entered.wait(5)
        assert not contender.start()
        assert contender.fatal_error_code is None
        assert ledger.get(job_id, project).status == "running"
        assert not worker.stop(.01)
        assert worker.is_running
        assert not contender.start()
        assert ledger.get(job_id, project).status == "running"
        release.set()
        assert worker.stop(10)
        assert ledger.get(job_id, project).status == "succeeded"
        assert contender.start()
    finally:
        release.set()
        worker.stop(10)
        contender.stop(10)


@pytest.mark.parametrize("failure,code", [
    (RuntimeError("sensitive provider credentials should never be stored"), "IMAGE_PROCESSING_FAILED"),
    (MemoryError("sensitive allocation details"), "IMAGE_OUT_OF_MEMORY"),
    (RuntimeError("CUDA out of memory: sensitive model path"), "IMAGE_OUT_OF_MEMORY"),
    (GatewayError("DO_NOT_STORE_THIS_SECRET", "secret"), "IMAGE_PROCESSING_FAILED"),
])
def test_processor_failure_records_only_stable_error_code(tmp_path, failure, code):
    ledger, assets, project, request = setup_runtime(tmp_path)
    job_id = uuid4()
    ledger.submit(job_id, project, request)

    def processor(*_):
        raise failure

    worker = SingleHostWorker(ledger, assets, processor, poll_seconds=.01)
    try:
        assert worker.start()
        wait_for(lambda: ledger.get(job_id, project).status == "failed")
        job = ledger.get(job_id, project)
        assert job.error_code == code
        assert job.result is None
        assert worker.fatal_error_code is None
        assert "sensitive" not in job.model_dump_json()
        assert "SECRET" not in job.model_dump_json()
    finally:
        worker.stop(10)


@pytest.mark.parametrize("bad_result", ["missing", "foreign", "tampered", "kind", "untyped"])
def test_result_files_project_and_kind_are_verified_before_success(tmp_path, bad_result):
    ledger, assets, project, request = setup_runtime(tmp_path)
    job_id = uuid4()
    ledger.submit(job_id, project, request)
    result_project = uuid4() if bad_result == "foreign" else project
    result = valid_result(assets, result_project)
    if bad_result in ("missing", "tampered"):
        path = assets.files_dir / result_project.hex / f"{result.product_rgba.asset_id.hex}.png"
        if bad_result == "missing":
            path.unlink()
        else:
            path.write_bytes(b"different bytes")
    elif bad_result == "kind":
        result = BackgroundResult(scene_underlay=result.preserve_mask,
                                  product_rgba=result.product_rgba,
                                  composed_preview=result.product_rgba, warnings=[])
    elif bad_result == "untyped":
        result = result.model_dump()
    worker = SingleHostWorker(ledger, assets, lambda *_: result, poll_seconds=.01)
    try:
        assert worker.start()
        wait_for(lambda: ledger.get(job_id, project).status == "failed")
        job = ledger.get(job_id, project)
        assert job.result is None
        assert job.error_code in {
            "ASSET_NOT_FOUND", "ASSET_INTEGRITY_ERROR", "ASSET_STORAGE_ERROR", "IMAGE_RESULT_INVALID"}
    finally:
        worker.stop(10)


def test_ledger_error_stops_worker_and_releases_execution_lock(tmp_path, monkeypatch):
    ledger, assets, project, _ = setup_runtime(tmp_path)

    def broken_claim(_):
        raise sqlite3.OperationalError("private storage path")

    monkeypatch.setattr(ledger, "claim_next", broken_claim)
    worker = SingleHostWorker(ledger, assets, lambda *_: valid_result(assets, project), poll_seconds=.01)
    assert worker.start()
    wait_for(lambda: not worker.is_running)
    assert worker.fatal_error_code == "WORKER_LEDGER_ERROR"
    assert worker.stop(1)
    replacement = SingleHostWorker(JobLedger(ledger.path), assets, lambda *_: valid_result(assets, project))
    try:
        assert replacement.start()
    finally:
        replacement.stop(10)


def test_completion_commit_error_is_not_reexecuted_and_next_owner_recovers(tmp_path, monkeypatch):
    ledger, assets, project, request = setup_runtime(tmp_path)
    job_id = uuid4()
    ledger.submit(job_id, project, request)
    calls = []
    result = valid_result(assets, project)

    def processor(*_):
        calls.append(1)
        return result

    def broken_finish(*_, **__):
        raise sqlite3.OperationalError("commit failure")

    monkeypatch.setattr(ledger, "finish", broken_finish)
    worker = SingleHostWorker(ledger, assets, processor, poll_seconds=.01)
    assert worker.start()
    wait_for(lambda: not worker.is_running)
    assert worker.fatal_error_code == "WORKER_LEDGER_ERROR"
    assert calls == [1]
    assert ledger.get(job_id, project).status == "running"
    replacement = SingleHostWorker(JobLedger(ledger.path), assets, processor, poll_seconds=.01)
    try:
        assert replacement.start()
        wait_for(lambda: ledger.get(job_id, project).status == "interrupted")
        assert calls == [1]
    finally:
        replacement.stop(10)


def test_process_crash_recovery_and_cross_process_lock(tmp_path):
    ledger, assets, project, request = setup_runtime(tmp_path)
    first, second = uuid4(), uuid4()
    ledger.submit(first, project, request)
    ledger.submit(second, project, request)
    entered = tmp_path / "processor-entered"
    child_code = textwrap.dedent("""
        import sys, time
        from pathlib import Path
        from beauty_ai.assets import LocalAssetStore
        from beauty_ai.job_ledger import JobLedger
        from beauty_ai.image_worker import SingleHostWorker
        base = Path(sys.argv[1])
        def processor(request, project_id):
            (base / 'processor-entered').write_text(str(project_id))
            while True:
                time.sleep(1)
        worker = SingleHostWorker(JobLedger(base / 'jobs.sqlite3'),
                                  LocalAssetStore(base / 'assets'), processor,
                                  poll_seconds=.01)
        if not worker.start():
            raise SystemExit(2)
        while worker.is_running:
            time.sleep(.05)
    """)
    process = subprocess.Popen([sys.executable, "-c", child_code, str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[1],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    result = valid_result(assets, project)
    calls = []

    def processor(*_):
        calls.append(1)
        return result

    contender = SingleHostWorker(ledger, assets, processor, poll_seconds=.01)
    try:
        wait_for(lambda: entered.exists() or process.poll() is not None)
        assert entered.exists(), process.stderr.read()
        assert ledger.get(first, project).status == "running"
        assert ledger.get(second, project).status == "queued"
        assert not contender.start()
        assert ledger.get(first, project).status == "running"
        process.kill()
        process.wait(timeout=5)
        assert contender.start()
        wait_for(lambda: ledger.get(second, project).status == "succeeded")
        assert ledger.get(first, project).status == "interrupted"
        assert ledger.get(first, project).result is None
        assert calls == [1]
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stderr.close()
        contender.stop(10)


def test_lock_symlink_is_not_followed(tmp_path):
    ledger, assets, project, _ = setup_runtime(tmp_path)
    worker = SingleHostWorker(ledger, assets, lambda *_: valid_result(assets, project))
    other = tmp_path / "unrelated"
    other.write_text("unchanged")
    worker.lock_path.symlink_to(other)
    assert not worker.start()
    assert worker.fatal_error_code == "WORKER_LOCK_ERROR"
    assert other.read_text() == "unchanged"


def test_start_and_stop_are_idempotent_and_polling_limits_are_validated(tmp_path):
    ledger, assets, project, _ = setup_runtime(tmp_path)
    for interval in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            SingleHostWorker(ledger, assets, lambda *_: valid_result(assets, project), poll_seconds=interval)
    worker = SingleHostWorker(ledger, assets, lambda *_: valid_result(assets, project))
    assert worker.stop(0)
    try:
        assert worker.start()
        assert not worker.start()
        with pytest.raises(ValueError):
            worker.stop(-1)
        assert worker.stop(10)
        assert worker.stop(0)
        assert worker.start()
    finally:
        worker.stop(10)
