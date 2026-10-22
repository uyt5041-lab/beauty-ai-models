from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import socket
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
import uvicorn

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import AssetRef, CutoutRequest, CutoutResult, content_hash
from beauty_ai.image_gateway import HTTPImageGateway
from beauty_ai.inference_api import create_app
from beauty_ai.job_ledger import JobLedger


TOKEN_A = "test-only-project-a-token-0000000001"
TOKEN_B = "test-only-project-b-token-0000000002"


def png() -> bytes:
    data = BytesIO()
    Image.new("RGBA", (16, 32), (25, 70, 100, 255)).save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def service(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets", max_upload_bytes=1024)
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    a, b = uuid4(), uuid4()
    app = create_app(ledger, assets, {TOKEN_A: a, TOKEN_B: b})
    with TestClient(app) as client:
        yield client, assets, ledger, a, b


def auth(token=TOKEN_A):
    return {"Authorization": f"Bearer {token}"}


def submit_request(assets, project):
    source = assets.put_upload(png(), project_id=project)
    return CutoutRequest(source=source, pipeline_profile_id="fixture", pipeline_revision="1")


def test_auth_upload_download_and_project_scope(service):
    client, assets, _, a, _ = service
    assert client.post("/internal/v1/assets", content=png()).status_code == 401
    uploaded = client.post("/internal/v1/assets", content=png(), headers=auth())
    assert uploaded.status_code == 201
    ref = AssetRef.model_validate(uploaded.json())
    returned = client.get(f"/internal/v1/assets/{ref.asset_id}", headers=auth())
    assert returned.status_code == 200
    assert hashlib.sha256(returned.content).hexdigest() == ref.sha256
    assert returned.headers["etag"] == f'"{ref.sha256}"'
    assert client.get(f"/internal/v1/assets/{ref.asset_id}", headers=auth(TOKEN_B)).status_code == 404
    assert assets.read_bytes(ref, project_id=a) == returned.content
    health = client.get("/internal/v1/health", headers=auth()).json()
    assert health["local_worker_running"] is False
    assert health["model_readiness"] == "not_assessed"


def test_asset_limits_and_invalid_file(service):
    client, *_ = service
    assert client.post("/internal/v1/assets", content=b"bad", headers=auth()).status_code == 400
    assert client.post("/internal/v1/assets", content=b"x" * 1025, headers=auth()).status_code == 413


def test_durable_idempotency_conflict_scope_and_concurrency(service):
    client, assets, ledger, a, _ = service
    request = submit_request(assets, a)
    job_id = uuid4()
    url = f"/internal/v1/jobs/{job_id}"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.put(url, json=request.model_dump(mode="json"),
                                                       headers=auth()), range(8)))
    assert all(response.status_code == 202 for response in responses)
    assert {response.json()["request_hash"] for response in responses} == {content_hash(request)}
    assert ledger.get(job_id, a).status == "queued"
    changed = request.model_copy(update={"pipeline_revision": "2"})
    assert client.put(url, json=changed.model_dump(mode="json"), headers=auth()).status_code == 409
    assert client.get(url, headers=auth(TOKEN_B)).status_code == 404
    assert client.put(url, json=request.model_dump(mode="json"), headers=auth(TOKEN_B)).status_code == 404


def test_replay_resolves_committed_job_even_if_input_file_disappears(service):
    client, assets, _, a, _ = service
    request = submit_request(assets, a)
    url = f"/internal/v1/jobs/{uuid4()}"
    first = client.put(url, json=request.model_dump(mode="json"), headers=auth())
    assert first.status_code == 202
    assets._file_path(a, request.source.asset_id).unlink()
    replay = client.put(url, json=request.model_dump(mode="json"), headers=auth())
    assert replay.status_code == 202 and replay.json() == first.json()


def test_new_job_rejects_forged_metadata_and_cross_project_asset(service):
    client, assets, ledger, a, _ = service
    request = submit_request(assets, a)
    body = request.model_dump(mode="json")
    body["source"]["sha256"] = "0" * 64
    assert client.put(f"/internal/v1/jobs/{uuid4()}", json=body, headers=auth()).status_code == 400
    assert client.put(f"/internal/v1/jobs/{uuid4()}", json=request.model_dump(mode="json"),
                      headers=auth(TOKEN_B)).status_code == 404


def test_invalid_inputs_do_not_echo_private_payload(service):
    client, assets, _, project, _ = service
    private = "private-text-must-not-be-echoed"
    invalid = client.put(f"/internal/v1/jobs/{uuid4()}", json={"kind": private}, headers=auth())
    assert invalid.status_code == 422 and private not in invalid.text
    body = submit_request(assets, project).model_dump(mode="json")
    body[private] = private
    extra = client.put(f"/internal/v1/jobs/{uuid4()}", json=body, headers=auth())
    assert extra.status_code == 422 and private not in extra.text
    oversized = client.put(f"/internal/v1/jobs/{uuid4()}", content=b"x" * 65537, headers=auth())
    assert oversized.status_code == 413
    assert client.get("/internal/v1/jobs/not-a-uuid", headers=auth()).status_code == 422
    assert client.get("/docs").status_code == 404


def test_credentials_are_explicit_project_bound_and_not_weak(tmp_path):
    store, ledger = LocalAssetStore(tmp_path / "assets"), JobLedger(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError):
        create_app(ledger, store, {})
    with pytest.raises(ValueError):
        create_app(ledger, store, {"short": uuid4()})


@pytest.mark.asyncio
async def test_actual_loopback_http_gateway_worker_and_file_result(tmp_path):
    """Real TCP API/SQLite/worker integration; fixture processor is NOT a model."""
    assets = LocalAssetStore(tmp_path / "assets")
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project = uuid4()
    request = submit_request(assets, project)
    calls = []

    def processor(payload, project_id):
        calls.append(payload)
        product = assets.read(payload.source, project_id=project_id)
        rgba = assets.put_image(product.convert("RGBA"), project_id=project_id)
        mask = assets.put_image(Image.new("L", product.size, 255), project_id=project_id)
        return CutoutResult(product_rgba=rgba, preserve_mask=mask,
                            needs_manual_check=True, warnings=["FIXTURE_PROCESSOR_NOT_A_MODEL"])

    app = create_app(ledger, assets, {TOKEN_A: project}, processor)
    config = uvicorn.Config(app, log_level="critical", access_log=False, ws="none", lifespan="on")
    server = uvicorn.Server(config)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        limit = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < limit:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=auth()) as status_client:
            assert status_client.get("/internal/v1/health").json()["local_worker_running"]
            app.state.worker.fatal_error_code = "WORKER_LEDGER_ERROR"
            assert status_client.get("/internal/v1/health").status_code == 503
            app.state.worker.fatal_error_code = None
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=auth()) as client:
            gateway = HTTPImageGateway(client)
            job_id = uuid4()
            await gateway.submit(request, job_id=job_id)
            result = await gateway.wait(job_id, expected_request=request, deadline_seconds=5, poll_seconds=.01)
            assert result.status == "succeeded" and result.result.needs_manual_check
            assert len(calls) == 1
            again = await gateway.submit(request, job_id=job_id)
            assert again == result and len(calls) == 1
            ref = result.result.product_rgba
            data = await client.get(f"/internal/v1/assets/{ref.asset_id}")
            assert hashlib.sha256(data.content).hexdigest() == ref.sha256
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()
