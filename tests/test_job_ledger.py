import multiprocessing
import sqlite3
from uuid import UUID, uuid4

import pytest

from beauty_ai.contracts import AssetRef, BackgroundResult, CutoutRequest, CutoutResult, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.job_ledger import JobLedger


ASSET = AssetRef(asset_id=UUID("00000000-0000-4000-8000-000000000001"), sha256="a" * 64,
                 width=256, height=256, mime_type="image/png")


def request(profile="cutout"):
    return CutoutRequest(source=ASSET, pipeline_profile_id=profile, pipeline_revision="1")


def result():
    return CutoutResult(product_rgba=ASSET, preserve_mask=ASSET,
                        needs_manual_check=True, warnings=["MANUAL_CHECK_REQUIRED"])


def _submit_process(path, job, project, barrier, queue):
    ledger = JobLedger(path)
    barrier.wait(timeout=20)
    try:
        row = ledger.submit(UUID(job), UUID(project), request())
        queue.put((row.status, row.request_hash))
    except GatewayError as exc:
        queue.put(("error", exc.code))


def _claim_process(path, owner, barrier, queue):
    ledger = JobLedger(path)
    barrier.wait(timeout=20)
    row = ledger.claim_next(owner)
    queue.put(str(row.job.job_id) if row else None)


def _run_processes(context, target, arguments):
    barrier = context.Barrier(len(arguments))
    queue = context.Queue()
    processes = [context.Process(target=target, args=(*args, barrier, queue)) for args in arguments]
    try:
        for process in processes:
            process.start()
        values = [queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode == 0
        return values
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        queue.close()
        queue.join_thread()


def test_submit_is_durable_idempotent_and_snapshots_mutable_input(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    project, job_id = uuid4(), uuid4()
    ledger = JobLedger(path)
    original = request()
    queued = ledger.submit(job_id, project, original)
    assert ledger.path == path.resolve()
    assert queued.status == "queued"
    assert queued.request_hash == content_hash(original)
    assert ledger.submit(job_id, project, request()) == queued
    original.pipeline_profile_id = "mutated-after-submit"
    restarted = JobLedger(path)
    assert restarted.get(job_id, project) == queued
    claimed = restarted.claim_next("worker-a")
    assert claimed.project_id == project
    assert claimed.request == request()
    assert claimed.job.status == "running"
    assert restarted.submit(job_id, project, request()).status == "running"


def test_project_scope_and_different_input_conflict(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    job_id, project = uuid4(), uuid4()
    ledger.submit(job_id, project, request())
    for operation in [lambda: ledger.get(job_id, uuid4()),
                      lambda: ledger.submit(job_id, uuid4(), request("secret")),
                      lambda: ledger.get(uuid4(), project)]:
        with pytest.raises(GatewayError) as raised:
            operation()
        assert raised.value.code == "IMAGE_JOB_NOT_FOUND"
    with pytest.raises(GatewayError) as raised:
        ledger.submit(job_id, project, request("different"))
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    assert ledger.get(job_id, project).status == "queued"


def test_queued_work_is_claimed_fifo_once(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, first, second = uuid4(), uuid4(), uuid4()
    ledger.submit(first, project, request())
    ledger.submit(second, project, request())
    assert ledger.claim_next("one").job.job_id == first
    assert ledger.claim_next("two").job.job_id == second
    assert ledger.claim_next("three") is None


def test_finish_requires_current_owner_and_terminal_states_are_immutable(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, job_id = uuid4(), uuid4()
    ledger.submit(job_id, project, request())
    with pytest.raises(GatewayError) as raised:
        ledger.finish(job_id, "worker", result=result())
    assert raised.value.code == "JOB_STATE_CONFLICT"
    ledger.claim_next("worker")
    with pytest.raises(GatewayError) as raised:
        ledger.finish(job_id, "someone-else", result=result())
    assert raised.value.code == "JOB_STATE_CONFLICT"
    succeeded = ledger.finish(job_id, "worker", result=result())
    assert succeeded.status == "succeeded"
    assert succeeded.result == result()
    assert ledger.submit(job_id, project, request()) == succeeded
    with pytest.raises(GatewayError) as raised:
        ledger.finish(job_id, "worker", error_code="LATE_FAILURE")
    assert raised.value.code == "JOB_STATE_CONFLICT"
    assert ledger.get(job_id, project) == succeeded


@pytest.mark.parametrize("completion", [
    {}, {"result": result(), "error_code": "BOOM"},
    {"error_code": "Traceback: secret-token"}, {"error_code": ""},
    {"result": {"kind": "cutout"}},
    {"result": BackgroundResult(scene_underlay=ASSET, product_rgba=ASSET,
                                composed_preview=ASSET, warnings=[])},
])
def test_invalid_completion_rolls_back_and_preserves_running(tmp_path, completion):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, job_id = uuid4(), uuid4()
    ledger.submit(job_id, project, request())
    ledger.claim_next("worker")
    with pytest.raises(GatewayError) as raised:
        ledger.finish(job_id, "worker", **completion)
    assert raised.value.code == "JOB_STATE_CONFLICT"
    assert ledger.get(job_id, project).status == "running"
    assert ledger.finish(job_id, "worker", error_code="PROCESSOR_ERROR").status == "failed"


def test_recovery_does_not_run_automatically_and_only_changes_running(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    ledger = JobLedger(path)
    project = uuid4()
    ids = [uuid4() for _ in range(4)]
    for job_id in ids:
        ledger.submit(job_id, project, request())
    ledger.claim_next("old-owner")
    ledger.claim_next("old-owner")
    ledger.finish(ids[1], "old-owner", result=result())
    ledger.claim_next("old-owner")
    ledger.finish(ids[2], "old-owner", error_code="MODEL_OOM")
    restarted = JobLedger(path)
    assert restarted.get(ids[0], project).status == "running"
    # The worker is responsible for proving the old processor terminated and
    # owning the single-host execution lock before this explicit recovery call.
    assert restarted.recover_interrupted() == 1
    assert restarted.recover_interrupted() == 0
    interrupted = restarted.get(ids[0], project)
    assert interrupted.status == "interrupted"
    assert interrupted.error_code == "WORKER_INTERRUPTED"
    assert restarted.get(ids[1], project).status == "succeeded"
    assert restarted.get(ids[2], project).status == "failed"
    assert restarted.get(ids[3], project).status == "queued"
    assert restarted.claim_next("new-owner").job.job_id == ids[3]
    with pytest.raises(GatewayError) as raised:
        restarted.finish(ids[0], "old-owner", result=result())
    assert raised.value.code == "JOB_STATE_CONFLICT"


def test_multiple_processes_submit_same_job_once(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    JobLedger(path)
    job_id, project = uuid4(), uuid4()
    arguments = [(str(path), str(job_id), str(project))] * 4
    values = _run_processes(multiprocessing.get_context("spawn"), _submit_process, arguments)
    assert values == [("queued", content_hash(request()))] * 4
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert JobLedger(path).get(job_id, project).request_hash == content_hash(request())


def test_multiple_processes_claim_a_job_once(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    ledger = JobLedger(path)
    job_id, project = uuid4(), uuid4()
    ledger.submit(job_id, project, request())
    arguments = [(str(path), f"owner-{index}") for index in range(4)]
    values = _run_processes(multiprocessing.get_context("spawn"), _claim_process, arguments)
    assert values.count(str(job_id)) == 1
    assert values.count(None) == 3
    assert ledger.get(job_id, project).status == "running"


def test_corrupt_snapshot_hash_does_not_execute_or_return_a_job(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    ledger = JobLedger(path)
    job_id, project = uuid4(), uuid4()
    ledger.submit(job_id, project, request())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE jobs SET request_hash=? WHERE job_id=?", ("f" * 64, str(job_id)))
    for operation in [lambda: ledger.get(job_id, project), lambda: ledger.claim_next("worker")]:
        with pytest.raises(GatewayError) as raised:
            operation()
        assert raised.value.code == "JOB_STORAGE_ERROR"
        assert not raised.value.retryable
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM jobs").fetchone()[0] == "queued"


def test_invalid_owner_and_identifiers_are_rejected(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    for owner in ["", " ", "a\nsecret", "x" * 129]:
        with pytest.raises(ValueError):
            ledger.claim_next(owner)
    with pytest.raises(ValueError):
        ledger.submit("not-a-uuid", uuid4(), request())
    with pytest.raises(GatewayError) as raised:
        ledger.finish(uuid4(), "owner", result=result())
    assert raised.value.code == "IMAGE_JOB_NOT_FOUND"


def test_storage_failure_uses_stable_code(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.write_text("file, not directory")
    with pytest.raises(GatewayError) as raised:
        JobLedger(occupied / "jobs.sqlite3")
    assert raised.value.code == "JOB_STORAGE_ERROR"
    assert str(occupied) not in str(raised.value)
