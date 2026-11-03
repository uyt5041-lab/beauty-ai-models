"""Metadata slots are reserved before calls and preserve paid late receipts."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from beauty_ai import work_contracts
from beauty_ai.assets import LocalAssetStore
from beauty_ai.errors import GatewayError
from beauty_ai.inference_api import create_app
from beauty_ai.job_ledger import JobLedger
from beauty_ai.work_ledger import AdWorkLedger, read_work_dataset
from beauty_ai.work_tracking import track_ad_work
from test_work_ledger import receipt, start


TOKEN = "test-capacity-project-token-000000001"
AUTH = {"Authorization": "Bearer " + TOKEN}


def image_identity(attempt=None):
    attempt = attempt or uuid4()
    return {"attempt_id": str(attempt), "stage": "image_generation", "operation": "generate",
            "provider": "openai", "configured_model": "model-v1", "prompt_version": "v1",
            "request_hash": attempt.hex * 2, "parent_attempt_id": None}


def workspace(tmp_path):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work = uuid4(), uuid4()
    ledger.create_work(project, work, "product-1")
    return ledger, project, work


def test_work_admission_limit_keeps_existing_http_reads_and_export_available(tmp_path, monkeypatch):
    monkeypatch.setattr(work_contracts, "MAX_WORKS", 2)
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, first, second = uuid4(), uuid4(), uuid4()
    app = create_app(JobLedger(tmp_path / "jobs.sqlite3"), LocalAssetStore(tmp_path / "assets"),
                     {TOKEN: project}, work_ledger=ledger)
    with TestClient(app) as client:
        for work in (first, second):
            assert client.put(f"/internal/v1/ad-works/{work}", json={"product_ref": "product-1"},
                              headers=AUTH).status_code == 200
        rejected = client.put(f"/internal/v1/ad-works/{uuid4()}", json={"product_ref": "product-1"},
                              headers=AUTH)
        assert rejected.status_code == 400 and rejected.json()["detail"]["code"] == "WORK_LIMIT"
        for work in (first, second):
            assert client.get(f"/internal/v1/ad-works/{work}", headers=AUTH).status_code == 200
        exported = client.get("/internal/v1/ad-works", headers=AUTH)
        assert exported.status_code == 200 and len(exported.json()["works"]) == 2
        # Replays and another project do not consume this project's capacity.
        assert client.put(f"/internal/v1/ad-works/{first}", json={"product_ref": "product-1"},
                          headers=AUTH).status_code == 200
    other_project = uuid4()
    ledger.create_work(other_project, uuid4(), "product-2")
    assert len(ledger.export_dataset(project)["works"]) == 2


@pytest.mark.parametrize("boundary", ["project", "work"])
def test_text_and_image_binding_compete_atomically_for_last_slot(tmp_path, monkeypatch, boundary):
    ledger, project, work = workspace(tmp_path)
    other = uuid4()
    ledger.create_work(project, other, "product-2")
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 3 if boundary == "project" else 10)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 3 if boundary == "work" else 10)
    start(ledger, project, work)
    start(ledger, project, work)
    barrier, job = Barrier(2), uuid4()
    binding_work = other if boundary == "project" else work

    def contender(kind):
        independent = AdWorkLedger(ledger.path)
        barrier.wait(timeout=5)
        try:
            if kind == "text":
                start(independent, project, work)
            else:
                independent.bind_image_job(project, binding_work, job)
            return "OK"
        except GatewayError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(contender, ("text", "image"))) == ["OK", "WORK_LIMIT"]
    exported = ledger.export_dataset(project)
    slots = len(exported["attempts"]) + sum(row["attempt_id"] is None for row in exported["image_bindings"])
    assert slots == 3
    if boundary == "work":
        assert all(row["work_id"] == str(work) for row in exported["attempts"] + exported["image_bindings"])


def test_reserved_last_slot_survives_cancel_and_late_image_receipt(tmp_path, monkeypatch):
    ledger, project, work = workspace(tmp_path)
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 2)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 2)
    text, job = start(ledger, project, work), uuid4()
    ledger.bind_image_job(project, work, job)
    ledger.bind_image_job(project, work, job)  # Binding replay is free at capacity.
    with pytest.raises(GatewayError) as error:
        start(ledger, project, work)
    assert error.value.code == "WORK_LIMIT"
    ledger.cancel(project, work)
    identity = image_identity()
    reopened = AdWorkLedger(ledger.path)
    reopened.sync_image_attempt(project, job, identity, None)
    reopened.sync_image_attempt(project, job, identity, receipt(identity["attempt_id"], artifact="a" * 64))
    reopened.record_receipt(project, work, receipt(text, status="FAILED"))
    exported = reopened.export_dataset(project)
    assert len(exported["attempts"]) == len(exported["receipts"]) == 2
    assert exported["works"][0]["status"] == "CANCELLED"
    assert exported["image_bindings"][0]["attempt_id"] == identity["attempt_id"]
    assert read_work_dataset(reopened.path, project) == exported


def test_zero_growth_late_capture_is_retained_after_capacity_is_lowered(tmp_path, monkeypatch):
    ledger, project, work = workspace(tmp_path)
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 3)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 3)
    text = [start(ledger, project, work) for _ in range(2)]
    job = uuid4()
    ledger.bind_image_job(project, work, job)
    ledger.cancel(project, work)
    # Existing admitted/charged work must remain reconcilable after a lower limit.
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 2)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 2)
    identity = image_identity()
    ledger.bind_image_job(project, work, job)
    ledger.sync_image_attempt(project, job, identity, receipt(identity["attempt_id"]))
    for attempt in text:
        ledger.record_receipt(project, work, receipt(attempt, status="FAILED"))
    # Replays remain valid; every fact stays stored even when export is too large.
    ledger.sync_image_attempt(project, job, identity, receipt(identity["attempt_id"]))
    ledger.record_receipt(project, work, receipt(text[0], status="FAILED"))
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT count(*) FROM ad_attempts").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM ad_receipts").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM ad_image_bindings WHERE attempt_id IS NULL").fetchone()[0] == 0
    for read in (lambda: ledger.export_dataset(project), lambda: read_work_dataset(ledger.path, project)):
        with pytest.raises(GatewayError) as error:
            read()
        assert error.value.code == "WORK_LIMIT"


def test_same_identity_and_receipt_replay_are_free_and_default_known_call_stays_allowed(tmp_path, monkeypatch):
    ledger, project, work = workspace(tmp_path)
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 2)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 2)
    first = start(ledger, project, work, request_hash="a" * 64)
    ledger.record_receipt(project, work, receipt(first))
    # Default external registration still permits a deliberate new known attempt.
    second = start(ledger, project, work, request_hash="a" * 64)
    ledger.record_receipt(project, work, receipt(second))
    start(ledger, project, work, attempt=first, request_hash="a" * 64)
    ledger.record_receipt(project, work, receipt(first))
    with pytest.raises(GatewayError) as error:
        start(ledger, project, work)
    assert error.value.code == "WORK_LIMIT"
    exported = ledger.export_dataset(project)
    assert len(exported["attempts"]) == len(exported["receipts"]) == 2


def test_new_binding_capacity_failure_occurs_before_any_provider_call(tmp_path, monkeypatch):
    from test_work_image import request, setup
    calls = []
    processor, ledger, project, work = setup(tmp_path, lambda request: calls.append(request))
    monkeypatch.setattr(work_contracts, "MAX_RECEIPTS", 1)
    monkeypatch.setattr(work_contracts, "MAX_ATTEMPTS_PER_WORK", 1)
    start(ledger, project, work)
    job = uuid4()
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError) as error:
            processor.process_job(request(), project, job)
    assert error.value.code == "WORK_LIMIT"
    assert calls == []
    assert processor.attempts.get(job, project) is None
    assert ledger.get_image_binding(project, job) is None
