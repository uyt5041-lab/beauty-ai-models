"""Actual TCP response-loss reconciliation; fixture processing is not AI inference."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
from io import BytesIO
import json
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest
import uvicorn

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import CutoutRequest, CutoutResult, content_hash
from beauty_ai.errors import JobStillPending, SubmissionUncertain
from beauty_ai.image_gateway import HTTPImageGateway
from beauty_ai.inference_api import create_app
from beauty_ai.job_ledger import JobLedger


TOKEN_A = "test-only-reconciliation-a-0000000001"
TOKEN_B = "test-only-reconciliation-b-0000000002"


@asynccontextmanager
async def running_service(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    project, foreign_project = uuid4(), uuid4()
    image = Image.new("RGBA", (20, 32))
    image.paste((30, 90, 170, 255), (4, 3, 16, 28))
    source = assets.put_image(image, project_id=project)
    image.close()
    payload = CutoutRequest(source=source, pipeline_profile_id="fixture", pipeline_revision="1")
    entered, release = threading.Event(), threading.Event()
    calls, wire_puts = [], []

    def fixture_processor(request, project_id):
        calls.append((request, project_id))
        entered.set()
        if not release.wait(5):
            raise RuntimeError("FIXTURE_WAIT_EXPIRED")
        with assets.read(request.source, project_id=project_id) as product:
            rgba = assets.put_image(product, project_id=project_id)
            keep = product.getchannel("A").point(lambda value: 255 if value else 0)
            try:
                mask = assets.put_image(keep, project_id=project_id)
            finally:
                keep.close()
        return CutoutResult(product_rgba=rgba, preserve_mask=mask, needs_manual_check=True,
                            warnings=["FIXTURE_PROCESSOR_NOT_A_MODEL"])

    app = create_app(ledger, assets, {TOKEN_A: project, TOKEN_B: foreign_project}, fixture_processor)

    @app.middleware("http")
    async def count_actual_puts(request, call_next):
        if request.method == "PUT" and request.url.path.startswith("/internal/v1/jobs/"):
            wire_puts.append(request.url.path)
        return await call_next(request)

    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False,
                                           ws="none", lifespan="on"))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=False)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        assert server.started
        yield SimpleNamespace(assets=assets, ledger=ledger, project=project, payload=payload,
                              address=address, entered=entered, release=release, calls=calls,
                              wire_puts=wire_puts, worker=app.state.worker)
    finally:
        # Even a failed assertion releases bounded fixture work before the real
        # lifespan waits for its worker; no timeout is treated as cancellation.
        release.set()
        server.should_exit = True
        await asyncio.to_thread(thread.join, 8)
        listener.close()
        assert not thread.is_alive()


class LoseCommittedResponse(httpx.AsyncBaseTransport):
    """Inject loss after real TCP delivery and a separately verified DB commit."""
    def __init__(self, service, job_id, *, loss="read_error", release_on_get=True, hide_get=False):
        self.service, self.job_id = service, job_id
        self.loss, self.release_on_get, self.hide_get = loss, release_on_get, hide_get
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.puts = 0
        self.get_ids = []
        self.commit_before_loss = None
        self.closed = False

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if request.url.path.startswith("/internal/v1/jobs/"):
            request_id = UUID(request.url.path.rsplit("/", 1)[-1])
            if request.method == "PUT":
                self.puts += 1
                if self.puts == 1:
                    body = json.loads(await response.aread())
                    await response.aclose()
                    assert response.status_code == 202
                    assert UUID(body["job_id"]) == self.job_id == request_id
                    # This new connection checks persisted state, not a Python
                    # cache or a response fabricated by the boundary double.
                    reopened = JobLedger(self.service.ledger.path)
                    committed = reopened.get(self.job_id, self.service.project)
                    assert committed.request_hash == content_hash(self.service.payload)
                    self.commit_before_loss = committed
                    if self.loss == "read_error":
                        raise httpx.ReadError("Injected response loss after commit", request=request)
                    assert self.loss == "status_503"
                    return httpx.Response(503, json={"detail": {"code": "BOUNDARY_UNAVAILABLE"}}, request=request)
            elif request.method == "GET":
                self.get_ids.append(request_id)
                body = json.loads(await response.aread())
                await response.aclose()
                assert response.status_code == 200 and UUID(body["job_id"]) == self.job_id
                if self.hide_get:
                    # The record exists, but the caller cannot observe it yet.
                    return httpx.Response(404, json={"detail": {"code": "IMAGE_JOB_NOT_FOUND"}}, request=request)
                if self.release_on_get:
                    self.service.release.set()
                return httpx.Response(200, json=body, request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()
        self.closed = True


def assert_single_persisted_execution(service, transport, job_id):
    assert transport.commit_before_loss is not None
    assert transport.puts == 1
    assert service.wire_puts == [f"/internal/v1/jobs/{job_id}"]
    assert transport.get_ids and set(transport.get_ids) == {job_id}
    assert service.calls == [(service.payload, service.project)]
    with sqlite3.connect(service.ledger.path) as database:
        rows = database.execute("SELECT job_id, request_hash, status FROM jobs").fetchall()
    assert rows == [(str(job_id), content_hash(service.payload), "succeeded")]


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["read_error", "status_503"])
async def test_lost_committed_put_reconciles_over_tcp_once_with_real_files_and_project_auth(tmp_path, loss):
    async with running_service(tmp_path) as service:
        job_id = uuid4()
        transport = LoseCommittedResponse(service, job_id, loss=loss)
        async with httpx.AsyncClient(base_url=service.address, transport=transport,
                                     headers={"Authorization": "Bearer " + TOKEN_A}, timeout=2) as client:
            result = await HTTPImageGateway(client).submit_and_wait(
                service.payload, job_id=job_id, deadline_seconds=5, poll_seconds=.01,
            )
            assert result.job_id == job_id and result.status == "succeeded"
            assert result.request_hash == content_hash(service.payload)
            assert result.result.warnings == ["FIXTURE_PROCESSOR_NOT_A_MODEL"]
            assert result.result.needs_manual_check
            assert result == service.ledger.get(job_id, service.project)
            for ref in (result.result.product_rgba, result.result.preserve_mask):
                response = await client.get(f"/internal/v1/assets/{ref.asset_id}")
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == ref.sha256
                assert response.content == service.assets.read_bytes(ref, project_id=service.project)
                with Image.open(BytesIO(response.content)) as decoded:
                    decoded.load()
                    assert decoded.size == (20, 32)
            with service.assets.read(result.result.product_rgba, project_id=service.project) as product:
                assert product.mode == "RGBA"
                with service.assets.read(result.result.preserve_mask, project_id=service.project) as mask:
                    assert mask.convert("L").tobytes() == product.getchannel("A").point(lambda value: 255 if value else 0).tobytes()

            async with httpx.AsyncClient(base_url=service.address, timeout=2) as boundary_client:
                job_path = f"/internal/v1/jobs/{job_id}"
                asset_path = f"/internal/v1/assets/{result.result.product_rgba.asset_id}"
                assert (await boundary_client.get(job_path)).status_code == 401
                assert (await boundary_client.get(asset_path)).status_code == 401
                foreign = {"Authorization": "Bearer " + TOKEN_B}
                assert (await boundary_client.get(job_path, headers=foreign)).status_code == 404
                assert (await boundary_client.get(asset_path, headers=foreign)).status_code == 404
            assert_single_persisted_execution(service, transport, job_id)
        assert transport.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("hide_record,error_type", [(False, JobStillPending), (True, SubmissionUncertain)])
async def test_reconciliation_deadline_does_not_cancel_or_resubmit_real_work(tmp_path, hide_record, error_type):
    async with running_service(tmp_path) as service:
        job_id = uuid4()
        transport = LoseCommittedResponse(service, job_id, release_on_get=False, hide_get=hide_record)
        async with httpx.AsyncClient(base_url=service.address, transport=transport,
                                     headers={"Authorization": "Bearer " + TOKEN_A}, timeout=2) as client:
            gateway = HTTPImageGateway(client)
            with pytest.raises(error_type) as error:
                await gateway.submit_and_wait(service.payload, job_id=job_id,
                                              deadline_seconds=.3, poll_seconds=.01)
            assert error.value.job_id == job_id
            assert await asyncio.to_thread(service.entered.wait, 1)
            assert service.ledger.get(job_id, service.project).status == "running"
            assert service.worker.is_running
            assert transport.puts == 1 and len(service.calls) == 1
            # The original worker is still running; restoring observation and
            # waiting on that ID finishes it without a new submission.
            transport.hide_get = False
            service.release.set()
            result = await gateway.wait(job_id, expected_request=service.payload,
                                        deadline_seconds=5, poll_seconds=.01)
            assert result.status == "succeeded"
            assert_single_persisted_execution(service, transport, job_id)
        assert transport.closed
