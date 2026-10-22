from io import BytesIO
import sqlite3
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import CutoutRequest, CutoutResult, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.execution_trace import phase
from beauty_ai.inference_api import create_app
from beauty_ai.job_ledger import JobLedger


TOKEN = "test-only-execution-project-token-00001"
OTHER_TOKEN = "test-only-execution-other-token-00002"
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.mark.parametrize("fail", [False, True])
def test_execution_endpoint_returns_actual_worker_record_without_private_input(tmp_path, fail):
    assets = LocalAssetStore(tmp_path / "assets")
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, other, job_id = uuid4(), uuid4(), uuid4()
    raw = BytesIO()
    Image.new("RGBA", (16, 16), (30, 50, 90, 255)).save(raw, "PNG")
    source = assets.put_upload(raw.getvalue(), project_id=project)
    request = CutoutRequest(source=source, pipeline_profile_id="fixture", pipeline_revision="1")

    def processor(payload, project_id):
        with phase("preprocess"):
            if fail:
                raise GatewayError("IMAGE_PROCESSING_FAILED", "private exception must not escape")
            mask = assets.put_image(Image.new("L", (16, 16), 255), project_id=project_id)
        return CutoutResult(product_rgba=payload.source, preserve_mask=mask,
                            needs_manual_check=True, warnings=["FIXTURE_NOT_A_MODEL"])

    app = create_app(ledger, assets, {TOKEN: project, OTHER_TOKEN: other}, processor)
    with TestClient(app) as client:
        submitted = client.put(f"/internal/v1/jobs/{job_id}",
            headers=HEADERS, json=request.model_dump(mode="json"))
        assert submitted.status_code == 202
        url = f"/internal/v1/jobs/{job_id}/execution"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": "Bearer " + OTHER_TOKEN}).status_code == 404
        deadline = time.monotonic() + 5
        while True:
            response = client.get(url, headers=HEADERS)
            assert response.status_code == 200
            body = response.json()
            if body["recording_status"] == "RECORDED":
                break
            assert body["execution"] is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert body["status"] == ("failed" if fail else "succeeded")
        assert body["request_hash"] == content_hash(request)
        record = body["execution"]
        assert record["outcome"] == body["status"]
        assert record["job_id"] == str(job_id)
        assert record["project_id"] == str(project)
        assert record["worker_elapsed_ms"] >= record["processor_elapsed_ms"] >= 0
        assert record["queue_elapsed_ms"] >= 0
        assert record["phases"][0]["name"] == "preprocess"
        assert record["phases"][0]["outcome"] == ("failed" if fail else "completed")
        assert "private exception" not in response.text
        assert str(source.asset_id) not in response.text
        assert TOKEN not in response.text
        assert "peak" not in response.text


def test_execution_endpoint_preserves_unrecorded_jobs_and_sanitizes_corrupt_storage(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, job_id = uuid4(), uuid4()
    raw = BytesIO()
    Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(raw, "PNG")
    source = assets.put_upload(raw.getvalue(), project_id=project)
    request = CutoutRequest(source=source, pipeline_profile_id="fixture", pipeline_revision="1")
    ledger.submit(job_id, project, request)
    app = create_app(ledger, assets, {TOKEN: project})
    with TestClient(app) as client:
        url = f"/internal/v1/jobs/{job_id}/execution"
        queued = client.get(url, headers=HEADERS).json()
        assert queued["recording_status"] == "NOT_RECORDED" and queued["execution"] is None
        ledger.claim_next("fixture")
        ledger.finish(job_id, "fixture", error_code="PROCESSOR_ERROR")
        legacy = client.get(url, headers=HEADERS).json()
        assert legacy["status"] == "failed"
        assert legacy["recording_status"] == "NOT_RECORDED" and legacy["execution"] is None
        assert client.get(f"/internal/v1/jobs/{uuid4()}/execution", headers=HEADERS).status_code == 404
        with sqlite3.connect(ledger.path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(job_executions)")]
            assert len(columns) == 5
            # A legacy job has no observation. Malformed stored evidence must not
            # turn into an invented record or disclose its contents to callers.
            db.execute("INSERT INTO job_executions VALUES (?, ?, ?, ?, ?)",
                       (str(job_id), str(project), content_hash(request), "failed", "private corrupt body"))
        broken = client.get(url, headers=HEADERS)
        assert broken.status_code == 503
        assert "JOB_STORAGE_ERROR" in broken.text
        assert "private corrupt body" not in broken.text
