import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ExternalImageResult, ImagePrompt, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.image_worker import SingleHostWorker
from beauty_ai.inference_api import create_app
from beauty_ai.job_ledger import JobLedger


TOKEN_A = "offline-test-project-a-000000000001"
TOKEN_B = "offline-test-project-b-000000000002"


def auth(token=TOKEN_A):
    return {"Authorization": f"Bearer {token}"}


def request(**changes):
    return ExternalImageRequest.model_validate({"operation": "generate", "width": 1024,
        "height": 1024, "prompt": ImagePrompt(positive="A flower arrangement.",
            prompt_version="offline-v1", source_revision=1), **changes})


@pytest.fixture
def runtime(tmp_path):
    return (JobLedger(tmp_path / "jobs.sqlite3"), LocalAssetStore(tmp_path / "assets"), uuid4(), uuid4())


def image_asset(assets, project, size=(1024, 1024)):
    with Image.new("RGBA", size, (120, 70, 210, 128)) as image:
        return assets.put_image(image, project_id=project)


def result(assets, project, payload, **changes):
    return ExternalImageResult.model_validate({"operation": payload.operation,
        "image": image_asset(assets, project), "model": payload.model, "attempt_id": uuid4(),
        "warnings": ["GENERATIVE_CUTOUT_REQUIRES_REVIEW"] if payload.operation == "cutout" else [],
        **changes})


def run_one(ledger, assets, project, payload, processor):
    job_id = uuid4()
    ledger.submit(job_id, project, payload)
    claimed = ledger.claim_next("test-owner")
    assert claimed.job.job_id == job_id
    worker = SingleHostWorker(ledger, assets, processor)
    worker._execute(job_id, project, claimed.request, "test-owner",
                    created_at=claimed.created_at, claimed_at=claimed.claimed_at)
    return ledger.get(job_id, project)


@pytest.mark.parametrize("operation", ["generate", "edit", "cutout"])
def test_job_processor_receives_job_id_and_new_results_round_trip(runtime, operation):
    ledger, assets, project, _ = runtime
    source = image_asset(assets, project)
    payload = request(operation=operation, product_images=[] if operation == "generate" else [source],
                      **({"prompt": None} if operation == "cutout" else {}))
    calls = []

    class Processor:
        def process_job(self, received, received_project, job_id):
            calls.append((received, received_project, job_id))
            return result(assets, project, received)

    job = run_one(ledger, assets, project, payload, Processor())
    assert job.status == "succeeded"
    assert calls == [(payload, project, job.job_id)]
    assert isinstance(job.result, ExternalImageResult)
    assert JobLedger(ledger.path).get(job.job_id, project) == job
    assert ledger.submit(job.job_id, project, payload) == job
    assert ledger.claim_next("next-owner") is None
    execution = ledger.execution(job.job_id, project).execution
    assert execution.request_hash == content_hash(payload)
    assert execution.seed is None and execution.backend is None


@pytest.mark.parametrize("mismatch", ["operation", "model", "size", "foreign", "missing"])
def test_worker_does_not_publish_wrong_operation_or_invalid_result_asset(runtime, mismatch):
    ledger, assets, project, other = runtime
    payload = request()
    returned = result(assets, other if mismatch == "foreign" else project, payload)
    if mismatch == "operation":
        returned = returned.model_copy(update={"operation": "edit"})
    elif mismatch == "model":
        returned = returned.model_copy(update={"model": "gpt-image-2-2026-04-21"})
    elif mismatch == "size":
        returned = returned.model_copy(update={"image": image_asset(assets, project, (512, 512))})
    elif mismatch == "missing":
        assets._file_path(project, returned.image.asset_id).unlink()
    job = run_one(ledger, assets, project, payload, lambda *_: returned)
    assert job.status == "failed" and job.result is None
    assert job.error_code in {"IMAGE_RESULT_INVALID", "ASSET_NOT_FOUND", "ASSET_INTEGRITY_ERROR"}


def test_ledger_rejects_operation_mismatch_even_without_worker(runtime):
    ledger, assets, project, _ = runtime
    payload, job_id = request(), uuid4()
    ledger.submit(job_id, project, payload)
    ledger.claim_next("owner")
    wrong = result(assets, project, payload, operation="edit")
    with pytest.raises(GatewayError) as error:
        ledger.finish(job_id, "owner", result=wrong)
    assert error.value.code == "JOB_STATE_CONFLICT"
    assert ledger.get(job_id, project).status == "running"


def test_ledger_detects_stored_operation_corruption(runtime):
    ledger, assets, project, _ = runtime
    payload = request()
    job = run_one(ledger, assets, project, payload, lambda *_: result(assets, project, payload))
    wrong = job.result.model_copy(update={"operation": "edit"})
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE jobs SET result_json=? WHERE job_id=?", (wrong.model_dump_json(), str(job.job_id)))
    with pytest.raises(GatewayError) as error:
        ledger.get(job.job_id, project)
    assert error.value.code == "JOB_STORAGE_ERROR"


@pytest.mark.parametrize("code", [
    "PROVIDER_CALLS_DISABLED", "PROVIDER_SUBMISSION_UNKNOWN", "PROVIDER_QUOTA_EXCEEDED",
    "PROVIDER_RATE_LIMIT", "PROVIDER_RESPONSE_INVALID", "PROVIDER_RESULT_STORAGE_FAILED",
])
def test_provider_errors_are_stable_and_never_echo_private_detail(runtime, code):
    ledger, assets, project, _ = runtime

    def fail(*_):
        raise GatewayError(code, "secret-provider-body-do-not-log")

    job = run_one(ledger, assets, project, request(), fail)
    assert job.error_code == code and job.status == "failed"
    assert "secret-provider" not in job.model_dump_json()
    assert ledger.claim_next("next-owner") is None


def test_api_checks_every_input_asset_project_and_hash(runtime):
    ledger, assets, project, other = runtime
    own, foreign = image_asset(assets, project), image_asset(assets, other)
    app = create_app(ledger, assets, {TOKEN_A: project, TOKEN_B: other})
    with TestClient(app) as client:
        for changes in ({"product_images": [foreign]}, {"reference_images": [foreign]}):
            payload = request(operation="edit", product_images=[own], **(
                {"reference_images": []} if "product_images" in changes else {}))
            body = payload.model_dump(mode="json")
            body.update({key: [ref.model_dump(mode="json") for ref in refs] for key, refs in changes.items()})
            response = client.put(f"/internal/v1/jobs/{uuid4()}", headers=auth(), json=body)
            assert response.status_code == 404
        forged = own.model_copy(update={"sha256": "b" * 64})
        payload = request(operation="edit", product_images=[forged])
        response = client.put(f"/internal/v1/jobs/{uuid4()}", headers=auth(), json=payload.model_dump(mode="json"))
        assert response.status_code == 400
        assert ledger.claim_next("test-owner") is None


def test_request_validator_blocks_external_jobs_before_queue(runtime):
    ledger, assets, project, _ = runtime
    validations = []

    def disabled(payload):
        validations.append(payload)
        raise GatewayError("PROVIDER_CALLS_DISABLED", "sensitive configuration")

    app = create_app(ledger, assets, {TOKEN_A: project}, request_validator=disabled)
    payload = request()
    with TestClient(app) as client:
        response = client.put(f"/internal/v1/jobs/{uuid4()}", headers=auth(), json=payload.model_dump(mode="json"))
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "PROVIDER_CALLS_DISABLED"}}
        assert validations == [payload]
        assert ledger.claim_next("test-owner") is None


def test_committed_api_job_replay_does_not_revalidate_or_resubmit(runtime):
    ledger, assets, project, _ = runtime
    source = image_asset(assets, project)
    payload = request(operation="edit", product_images=[source])
    job_id = uuid4()
    original = ledger.submit(job_id, project, payload)
    assets._file_path(project, source.asset_id).unlink()

    def never(_):
        pytest.fail("Committed replay must not revalidate a provider request")

    app = create_app(ledger, assets, {TOKEN_A: project}, request_validator=never)
    with TestClient(app) as client:
        response = client.put(f"/internal/v1/jobs/{job_id}", headers=auth(), json=payload.model_dump(mode="json"))
        assert response.status_code == 202
        assert response.json() == original.model_dump(mode="json")


def test_provider_receipt_checks_job_ownership_before_store_lookup(runtime):
    ledger, assets, project, other = runtime
    job_id = uuid4()
    ledger.submit(job_id, project, request())
    calls = []
    receipt = {"job_id": str(job_id), "state": "submission_unknown", "usage": None}

    class Attempts:
        def get(self, received_job, received_project):
            calls.append((received_job, received_project))
            return receipt

    app = create_app(ledger, assets, {TOKEN_A: project, TOKEN_B: other}, provider_attempts=Attempts())
    with TestClient(app) as client:
        url = f"/internal/v1/jobs/{job_id}/provider"
        assert client.get(url).status_code == 401
        assert client.get(url, headers=auth(TOKEN_B)).status_code == 404
        assert client.get(f"/internal/v1/jobs/{uuid4()}/provider", headers=auth()).status_code == 404
        assert calls == []
        response = client.get(url, headers=auth())
        assert response.status_code == 200 and response.json() == receipt
        assert calls == [(job_id, project)]


@pytest.mark.parametrize("configured", [False, True])
def test_missing_provider_receipt_has_stable_not_found(runtime, configured):
    ledger, assets, project, _ = runtime
    job_id = uuid4()
    ledger.submit(job_id, project, request())

    class Attempts:
        def get(self, *_):
            return None

    app = create_app(ledger, assets, {TOKEN_A: project},
                     provider_attempts=Attempts() if configured else None)
    with TestClient(app) as client:
        response = client.get(f"/internal/v1/jobs/{job_id}/provider", headers=auth())
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "PROVIDER_ATTEMPT_NOT_FOUND"}}
