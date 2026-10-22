import base64
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, content_hash
from beauty_ai.inference_runtime import build_app
from beauty_ai.job_ledger import JobLedger
from beauty_ai.openai_images import OpenAIImagesConfig
from beauty_ai.openai_images_demo import fixture_png
from beauty_ai.provider_attempts import ProviderAttemptStore


TOKEN = "offline-runtime-test-token-0000000000001"


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Runtime tests must never use real HTTP transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def payload():
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
        prompt=ImagePrompt(positive="Offline test image", prompt_version="test-v1", source_revision=1))


def terminal(client, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/internal/v1/jobs/{job_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(0.01)
    pytest.fail("Mock job did not reach a terminal state")


def client_for(app):
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def success_response(request):
    return httpx.Response(200, headers={"x-request-id": "mock-runtime-response"}, json={
        "data": [{"b64_json": base64.b64encode(fixture_png("runtime fixture")).decode("ascii")}],
    })


def test_runtime_defaults_block_submission_without_consulting_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-offline-environment-value")
    monkeypatch.setenv("BEAUTY_OPENAI_IMAGE_LIVE_CALLS", "true")
    project, job_id = uuid4(), uuid4()
    with client_for(build_app(tmp_path, project, TOKEN)) as client:
        response = client.put(f"/internal/v1/jobs/{job_id}", json=payload().model_dump(mode="json"))
        assert response.status_code == 503
        assert response.json() == {"detail": {"code": "PROVIDER_CALLS_DISABLED"}}
        assert client.get(f"/internal/v1/jobs/{job_id}").status_code == 404
        assert client.get(f"/internal/v1/jobs/{job_id}/provider").status_code == 404
    assert JobLedger(tmp_path / "jobs.sqlite3").claim_next("offline-observer") is None


def test_mock_transport_still_requires_explicit_image_budget(tmp_path):
    calls = []
    project, job_id = uuid4(), uuid4()
    app = build_app(tmp_path, project, TOKEN, openai_config=OpenAIImagesConfig(),
                    openai_transport=httpx.MockTransport(lambda request: calls.append(request)))
    with client_for(app) as client:
        response = client.put(f"/internal/v1/jobs/{job_id}", json=payload().model_dump(mode="json"))
        assert response.status_code == 202
        job = terminal(client, job_id)
        assert job["status"] == "failed" and job["error_code"] == "PROVIDER_BUDGET_EXCEEDED"
        assert client.get(f"/internal/v1/jobs/{job_id}/provider").status_code == 404
    assert calls == []


def test_unknown_submission_stays_terminal_across_runtime_restart_and_next_job_runs(tmp_path):
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadTimeout("private-request-body", request=request)
        return success_response(request)

    project, job_id = uuid4(), uuid4()
    request = payload()

    def app():
        return build_app(tmp_path, project, TOKEN, openai_config=OpenAIImagesConfig(),
            openai_transport=httpx.MockTransport(provider), image_budget_usd="2", image_reservation_usd="1")

    with client_for(app()) as client:
        assert client.put(f"/internal/v1/jobs/{job_id}", json=request.model_dump(mode="json")).status_code == 202
        first = terminal(client, job_id)
        assert first["error_code"] == "PROVIDER_SUBMISSION_UNKNOWN"
        receipt = client.get(f"/internal/v1/jobs/{job_id}/provider").json()
        assert receipt["state"] == "unknown" and receipt["submission_status"] == "UNKNOWN"
        assert receipt["reservation_usd"] == "1.000000" and receipt["actual_cost_usd"] is None
    with client_for(app()) as client:
        replay = client.put(f"/internal/v1/jobs/{job_id}", json=request.model_dump(mode="json"))
        assert replay.json() == first and len(calls) == 1
        next_job = uuid4()
        assert client.put(f"/internal/v1/jobs/{next_job}", json=request.model_dump(mode="json")).status_code == 202
        assert terminal(client, next_job)["status"] == "succeeded"
        assert len(calls) == 2
        assert client.get(f"/internal/v1/jobs/{job_id}/provider").json()["state"] == "unknown"


def test_restart_marks_running_internal_job_interrupted_without_replaying_pending_attempt(tmp_path):
    project, job_id = uuid4(), uuid4()
    request = payload()
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    ledger.submit(job_id, project, request)
    assert ledger.claim_next("old-process").job.job_id == job_id
    attempts = ProviderAttemptStore(tmp_path / "provider_attempts.sqlite3")
    attempts.begin(job_id, project, content_hash(request), budget_limit_usd="2", reservation_usd="1")
    calls = []

    def provider(received):
        calls.append(received)
        return success_response(received)

    app = build_app(tmp_path, project, TOKEN, openai_config=OpenAIImagesConfig(),
        openai_transport=httpx.MockTransport(provider), image_budget_usd="2", image_reservation_usd="1")
    with client_for(app) as client:
        job = terminal(client, job_id)
        assert job["status"] == "interrupted" and job["error_code"] == "WORKER_INTERRUPTED"
        assert client.put(f"/internal/v1/jobs/{job_id}", json=request.model_dump(mode="json")).json() == job
        receipt = client.get(f"/internal/v1/jobs/{job_id}/provider").json()
        assert receipt["state"] == "submitting" and receipt["submission_status"] == "UNKNOWN"
        assert calls == []
        next_job = uuid4()
        assert client.put(f"/internal/v1/jobs/{next_job}", json=request.model_dump(mode="json")).status_code == 202
        assert terminal(client, next_job)["status"] == "succeeded"
        assert len(calls) == 1
