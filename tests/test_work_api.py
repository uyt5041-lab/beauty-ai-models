from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import CutoutRequest, ExternalImageRequest, ImagePrompt
from beauty_ai.errors import GatewayError
from beauty_ai.inference_api import create_app
from beauty_ai.inference_runtime import build_app
from beauty_ai.job_ledger import JobLedger
from beauty_ai.work_contracts import MAX_JSON_BYTES
from beauty_ai.work_ledger import AdWorkLedger


TOKEN_A = "test-work-project-a-token-00000000001"
TOKEN_B = "test-work-project-b-token-00000000002"


def auth(token=TOKEN_A, work=None):
    result = {"Authorization": "Bearer " + token}
    if work is not None:
        result["X-Ad-Work-Id"] = str(work)
    return result


@pytest.fixture
def service(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    jobs = JobLedger(tmp_path / "jobs.sqlite3")
    works = AdWorkLedger(tmp_path / "ad_works.sqlite3")
    a, b = uuid4(), uuid4()
    app = create_app(jobs, assets, {TOKEN_A: a, TOKEN_B: b}, work_ledger=works)
    with TestClient(app) as client:
        yield client, works, jobs, assets, a, b


def create(client, work=None, **values):
    work = work or uuid4()
    response = client.put(f"/internal/v1/ad-works/{work}", headers=auth(),
                          json={"product_ref": "product-1", **values})
    assert response.status_code == 200, response.text
    return work


def attempt_spec(attempt=None, *, local=False, stage="prompt_build"):
    attempt = attempt or uuid4()
    return {"attempt_id": str(attempt), "stage": stage, "operation": stage,
            "provider": "local" if local else "openai", "configured_model": "local" if local else "model-v1",
            "prompt_version": "prompt-v1", "request_hash": attempt.hex * 2,
            "parent_attempt_id": None}


def observed(attempt, *, local=False, status="SUCCEEDED"):
    return {"schema_version": "beautyai-work-receipt-v1", "attempt_id": str(attempt),
            "provider_request_id": None, "model": "local" if local else "model-v1",
            "usage": {"input_tokens": 0, "output_tokens": 0} if local else None,
            "elapsed_seconds": None, "status": status,
            "error_code": None if status == "SUCCEEDED" else "MODEL_FAILED", "artifact_sha256": "a" * 64,
            "estimated_cost_usd": "0.000000000" if local else None,
            "rate_version": "local-api-zero-v1" if local else None,
            "actual_cost_usd": None, "billing_evidence_ref": None,
            "source_ref": f"work-receipt:{attempt}", "source_sha256": "b" * 64}


def completion(attempts):
    return {"artifact_sha256": "a" * 64, "reviewer_ref": "reviewer-1",
            "quality_policy_version": "quality-v1", "quality_pass": True,
            "expected_attempt_ids": [str(attempt) for attempt in attempts]}


def image_request():
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
                                prompt=ImagePrompt(positive="Bright background and product space.",
                                                   prompt_version="v1", source_revision=1)).model_dump(mode="json")


def png():
    output = BytesIO()
    Image.new("RGBA", (32, 32), (25, 70, 100, 255)).save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("method,suffix", [
    ("PUT", ""), ("GET", ""), ("POST", "/attempts"), ("POST", "/receipts"),
    ("POST", "/complete"), ("POST", "/cancel"),
])
def test_auth_is_checked_before_body_and_identifier_validation(service, method, suffix):
    client, *_ = service
    result = client.request(method, "/internal/v1/ad-works/not-an-id" + suffix,
                            content=b"not JSON")
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert client.get("/internal/v1/ad-works").status_code == 401


def test_create_get_metrics_export_and_project_isolation(service):
    client, ledger, _, _, project, _ = service
    work = create(client, task_pair_id="pair-1", arm="baseline")
    url = f"/internal/v1/ad-works/{work}"
    assert client.put(url, headers=auth(), json={"product_ref": "product-1", "task_pair_id": "pair-1",
                                              "arm": "baseline"}).status_code == 200
    response = client.get(url, headers=auth())
    assert response.status_code == 200
    assert response.json()["work"]["project_id"] == str(project)
    assert response.json()["metrics"]["input_tokens"] == 0
    assert response.json()["metrics"]["known_usage_count"] == 0
    assert client.get(url, headers=auth(TOKEN_B)).status_code == 404
    foreign = client.get("/internal/v1/ad-works", headers=auth(TOKEN_B)).json()
    assert foreign["works"] == foreign["attempts"] == foreign["receipts"] == []
    exported = client.get("/internal/v1/ad-works", headers=auth()).json()
    assert exported == ledger.export_dataset(project)


@pytest.mark.parametrize("body", [
    b'{"product_ref":"first","product_ref":"second"}',
    b'{"product_ref":NaN}', b'{"product_ref":"safe","extra":1}',
    b'{"product_ref":"safe","project_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}',
    b'{"product_ref":"safe","api_key":"private-value"}', b'[]', b'null',
])
def test_strict_work_json_never_echoes_input(service, body):
    client, *_ = service
    response = client.put(f"/internal/v1/ad-works/{uuid4()}", content=body, headers=auth())
    assert response.status_code == 400
    assert set(response.json()["detail"]) == {"code"}
    assert response.json()["detail"]["code"].startswith("WORK_")
    assert "private-value" not in response.text


def test_work_body_is_bounded_and_path_uuid_is_strict(service):
    client, *_ = service
    response = client.put(f"/internal/v1/ad-works/{uuid4()}", content=b" " * (MAX_JSON_BYTES + 1),
                          headers=auth())
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "WORK_INPUT_TOO_LARGE"
    assert client.get("/internal/v1/ad-works/not-an-id", headers=auth()).status_code == 400


def test_attempt_receipt_completion_and_closed_state_routes(service):
    client, _, _, _, _, _ = service
    work = create(client, required_stages=["composition"])
    url = f"/internal/v1/ad-works/{work}"
    spec = attempt_spec(local=True, stage="composition")
    attempt = spec["attempt_id"]
    assert client.post(url + "/attempts", json=spec, headers=auth()).json() == {
        "attempt_id": attempt, "status": "REGISTERED"}
    response = client.post(url + "/complete", json=completion([attempt]), headers=auth())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "WORK_PENDING"
    receipt = observed(attempt, local=True)
    assert client.post(url + "/receipts", json=receipt, headers=auth()).status_code == 200
    assert client.post(url + "/receipts", json=receipt, headers=auth()).status_code == 200
    response = client.post(url + "/complete", json=completion([attempt]), headers=auth())
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    assert client.post(url + "/complete", json=completion([attempt]), headers=auth()).status_code == 200
    assert client.post(url + "/cancel", json={}, headers=auth()).status_code == 409
    assert client.post(url + "/attempts", json=attempt_spec(), headers=auth()).status_code == 409
    metrics = client.get(url, headers=auth()).json()["metrics"]
    assert metrics["estimated_total_usd"] == "0.000000000"
    assert metrics["actual_total_usd"] is None


def test_missing_stage_invalid_completion_and_receipt_conflict_are_409(service):
    client, *_ = service
    work = create(client)
    url = f"/internal/v1/ad-works/{work}"
    spec = attempt_spec(local=True, stage="composition")
    attempt = spec["attempt_id"]
    assert client.post(url + "/attempts", json=spec, headers=auth()).status_code == 200
    receipt = observed(attempt, local=True)
    assert client.post(url + "/receipts", json=receipt, headers=auth()).status_code == 200
    changed = {**receipt, "elapsed_seconds": 0.5}
    assert client.post(url + "/receipts", json=changed, headers=auth()).status_code == 409
    assert client.post(url + "/complete", json=completion([]), headers=auth()).status_code == 409
    response = client.post(url + "/complete", json=completion([attempt]), headers=auth())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "WORK_MISSING_STAGE"


def test_cancel_empty_object_only_and_late_receipt_preserved(service):
    client, ledger, _, _, project, _ = service
    work = create(client)
    url = f"/internal/v1/ad-works/{work}"
    spec = attempt_spec()
    assert client.post(url + "/attempts", json=spec, headers=auth()).status_code == 200
    assert client.post(url + "/cancel", json={"reason": "safe"}, headers=auth()).status_code == 400
    assert client.post(url + "/cancel", json=[], headers=auth()).status_code == 400
    assert client.post(url + "/cancel", json={}, headers=auth()).json()["status"] == "CANCELLED"
    assert client.post(url + "/receipts", json=observed(spec["attempt_id"], status="FAILED"),
                       headers=auth()).status_code == 200
    assert len(ledger.export_dataset(project)["receipts"]) == 1


@pytest.mark.parametrize("forbidden", ["project_id", "work_id", "started_at", "reject_repeated_request"])
def test_attempt_rejects_server_owned_fields(service, forbidden):
    client, *_ = service
    work = create(client)
    spec = {**attempt_spec(), forbidden: str(uuid4())}
    response = client.post(f"/internal/v1/ad-works/{work}/attempts", json=spec, headers=auth())
    assert response.status_code == 400


def test_work_errors_are_safe_and_storage_failure_is_503(service, monkeypatch):
    client, ledger, *_ = service
    def fail(*args, **kwargs):
        raise GatewayError("WORK_STORAGE_ERROR", "private raw database path")
    monkeypatch.setattr(ledger, "export_dataset", fail)
    response = client.get("/internal/v1/ad-works", headers=auth())
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "WORK_STORAGE_ERROR"}}
    assert "private" not in response.text


def test_already_recorded_capture_is_a_safe_state_conflict(service, monkeypatch):
    client, ledger, *_ = service
    work = create(client)
    def fail(*args, **kwargs):
        raise GatewayError("WORK_ALREADY_RECORDED", "private raw text")
    monkeypatch.setattr(ledger, "start_attempt", fail)
    response = client.post(f"/internal/v1/ad-works/{work}/attempts", json=attempt_spec(), headers=auth())
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "WORK_ALREADY_RECORDED"}}


def test_missing_work_store_is_503_and_existing_job_route_is_unchanged(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    jobs = JobLedger(tmp_path / "jobs.sqlite3")
    project = uuid4()
    app = create_app(jobs, assets, {TOKEN_A: project})
    assert app.state.work_ledger is None
    with TestClient(app) as client:
        assert client.get("/internal/v1/ad-works", headers=auth()).status_code == 503
        assert client.put(f"/internal/v1/ad-works/{uuid4()}", json={"product_ref": "safe"},
                          headers=auth()).status_code == 503
        job = uuid4()
        assert client.put(f"/internal/v1/jobs/{job}", json=image_request(), headers=auth()).status_code == 202
        assert client.put(f"/internal/v1/jobs/{uuid4()}", json=image_request(),
                          headers=auth(work=uuid4())).status_code == 503


def test_job_header_binds_before_queue_and_replay_cannot_move_work(service):
    client, ledger, jobs, _, project, _ = service
    first, second, job = create(client), create(client), uuid4()
    url, payload = f"/internal/v1/jobs/{job}", image_request()
    response = client.put(url, json=payload, headers=auth(work=first))
    assert response.status_code == 202
    assert ledger.get_image_binding(project, job) == str(first)
    assert jobs.get(job, project).status == "queued"
    assert client.put(url, json=payload, headers=auth(work=first)).json() == response.json()
    conflict = client.put(url, json=payload, headers=auth(work=second))
    assert conflict.status_code == 409
    assert ledger.get_image_binding(project, job) == str(first)


def test_job_header_validates_auth_uuid_project_and_open_before_queue(service):
    client, ledger, jobs, _, project, _ = service
    work, payload = create(client), image_request()
    invalids = [("private-not-a-uuid", auth(), 400),
                (str(uuid4()), auth(), 404),
                (str(work), auth(TOKEN_B), 404)]
    for value, headers, expected in invalids:
        job = uuid4()
        headers["X-Ad-Work-Id"] = value
        response = client.put(f"/internal/v1/jobs/{job}", json=payload, headers=headers)
        assert response.status_code == expected
        assert "private-not-a-uuid" not in response.text
        with pytest.raises(GatewayError):
            jobs.get(job, project)
    unauthorized = client.put(f"/internal/v1/jobs/{uuid4()}", content=b"not JSON",
                              headers={"X-Ad-Work-Id": "bad"})
    assert unauthorized.status_code == 401
    ledger.cancel(project, work)
    job = uuid4()
    assert client.put(f"/internal/v1/jobs/{job}", json=payload, headers=auth(work=work)).status_code == 409
    assert ledger.get_image_binding(project, job) is None


def test_invalid_replay_does_not_create_a_work_binding(service):
    client, ledger, _, _, project, _ = service
    work, job = create(client), uuid4()
    url, payload = f"/internal/v1/jobs/{job}", image_request()
    assert client.put(url, json=payload, headers=auth()).status_code == 202
    changed = {**payload, "quality": "high"}
    assert client.put(url, json=changed, headers=auth(work=work)).status_code == 409
    assert ledger.get_image_binding(project, job) is None


def test_foreign_work_header_fails_before_asset_validation(service, monkeypatch):
    client, _, _, assets, project, _ = service
    ref = assets.put_upload(png(), project_id=project)
    payload = ExternalImageRequest(operation="cutout", product_images=[ref], width=1024,
                                   height=1024).model_dump(mode="json")
    def forbidden(*args, **kwargs):
        pytest.fail("Foreign work must be rejected before reading assets")
    monkeypatch.setattr(assets, "read_bytes", forbidden)
    response = client.put(f"/internal/v1/jobs/{uuid4()}", json=payload,
                          headers=auth(work=uuid4()))
    assert response.status_code == 404


def test_tracked_replay_preserves_file_independent_existing_flow(service):
    client, ledger, _, assets, project, _ = service
    ref = assets.put_upload(png(), project_id=project)
    payload = ExternalImageRequest(operation="cutout", product_images=[ref], width=1024,
                                   height=1024).model_dump(mode="json")
    work, job = create(client), uuid4()
    url = f"/internal/v1/jobs/{job}"
    first = client.put(url, json=payload, headers=auth(work=work))
    assert first.status_code == 202
    assets._file_path(project, ref.asset_id).unlink()
    replay = client.put(url, json=payload, headers=auth(work=work))
    assert replay.status_code == 202 and replay.json() == first.json()
    assert ledger.get_image_binding(project, job) == str(work)


def test_legacy_local_profile_header_is_rejected_without_binding_or_queue(service):
    client, ledger, jobs, assets, project, _ = service
    ref = assets.put_upload(png(), project_id=project)
    payload = CutoutRequest(source=ref, pipeline_profile_id="fixture", pipeline_revision="1")
    work, job = create(client), uuid4()
    response = client.put(f"/internal/v1/jobs/{job}", json=payload.model_dump(mode="json"),
                          headers=auth(work=work))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "WORK_IMAGE_PROFILE_INVALID"
    assert ledger.get_image_binding(project, job) is None
    with pytest.raises(GatewayError):
        jobs.get(job, project)


def test_runtime_uses_one_work_ledger_and_restart_keeps_work(tmp_path):
    project, work = uuid4(), uuid4()
    app = build_app(tmp_path, project, TOKEN_A)
    assert app.state.work_ledger.path == (tmp_path / "ad_works.sqlite3").resolve()
    assert app.state.worker.processor.external.work_ledger is app.state.work_ledger
    with TestClient(app) as client:
        create(client, work=work)
    restarted = build_app(tmp_path, project, TOKEN_A)
    with TestClient(restarted) as client:
        response = client.get(f"/internal/v1/ad-works/{work}", headers=auth())
        assert response.status_code == 200
        assert response.json()["work"]["work_id"] == str(work)
