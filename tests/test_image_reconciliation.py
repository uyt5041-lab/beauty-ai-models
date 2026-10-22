import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from beauty_ai import image_gateway as module
from beauty_ai.contracts import AssetRef, CutoutRequest, content_hash
from beauty_ai.errors import GatewayError, JobStillPending, SubmissionUncertain
from beauty_ai.image_gateway import HTTPImageGateway


@pytest.fixture
def request_model():
    asset = AssetRef(asset_id=uuid4(), sha256="a" * 64, width=32, height=64, mime_type="image/png")
    return CutoutRequest(source=asset, pipeline_profile_id="uploaded-alpha", pipeline_revision="1")


def record(request, job_id, status="queued"):
    result = None
    if status == "succeeded":
        result = {"kind": "cutout", "product_rgba": request.source.model_dump(mode="json"),
                  "preserve_mask": request.source.model_dump(mode="json"),
                  "needs_manual_check": True, "warnings": ["MANUAL_REVIEW"]}
    return {"job_id": str(job_id), "request_hash": content_hash(request), "status": status,
            "result": result, "error_code": "IMAGE_OUT_OF_MEMORY" if status in ("failed", "interrupted") else None}


@pytest.mark.parametrize("loss", [500, 503, 408, 425, 429, "transport", "timeout"])
async def test_uncertain_put_uses_one_id_and_get_until_same_result(request_model, loss):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        assert request.url.path == f"/internal/v1/jobs/{job_id}"
        if request.method == "PUT":
            assert request.headers["X-Request-ID"] == str(job_id)
            assert json.loads(request.content) == request_model.model_dump(mode="json")
            if loss == "transport":
                raise httpx.ReadError("private signed URL", request=request)
            if loss == "timeout":
                raise httpx.ReadTimeout("private timeout detail", request=request)
            return httpx.Response(loss)
        count = calls.count("GET")
        if count == 1:
            return httpx.Response(404)
        if count == 2:
            return httpx.Response(503)
        if count == 3:
            return httpx.Response(200, json=record(request_model, job_id, "running"))
        return httpx.Response(200, json=record(request_model, job_id, "succeeded"))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        result = await HTTPImageGateway(client).submit_and_wait(
            request_model, job_id=job_id, deadline_seconds=1, poll_seconds=.001)
    assert result.status == "succeeded" and result.job_id == job_id
    assert calls == ["PUT", "GET", "GET", "GET", "GET"]


async def test_completed_put_returns_without_unnecessary_get(request_model):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json=record(request_model, job_id, "succeeded"))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        result = await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert result.status == "succeeded" and calls == ["PUT"]


@pytest.mark.parametrize("get_response", [404, 429, 503, "transport"])
async def test_unconfirmed_expiry_remains_submission_uncertain(request_model, get_response):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return httpx.Response(503)
        if get_response == "transport":
            raise httpx.ConnectError("private host", request=request)
        return httpx.Response(get_response)
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SubmissionUncertain) as error:
            await HTTPImageGateway(client).submit_and_wait(
                request_model, job_id=job_id, deadline_seconds=.015, poll_seconds=.001)
    assert error.value.job_id == job_id
    assert calls.count("PUT") == 1 and calls.count("GET") >= 1
    assert "private" not in str(error.value)


@pytest.mark.parametrize("observed_in", ["put", "get"])
async def test_observed_record_stays_pending_even_if_later_gets_are_404(request_model, observed_in):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return (httpx.Response(202, json=record(request_model, job_id))
                    if observed_in == "put" else httpx.Response(503))
        if observed_in == "get" and calls.count("GET") == 1:
            return httpx.Response(200, json=record(request_model, job_id, "running"))
        return httpx.Response(404)
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(JobStillPending) as error:
            await HTTPImageGateway(client).submit_and_wait(
                request_model, job_id=job_id, deadline_seconds=.015, poll_seconds=.001)
    assert error.value.job_id == job_id and calls.count("PUT") == 1


async def test_submission_and_polling_share_one_monotonic_budget(request_model, monkeypatch):
    job_id, calls, timeouts = uuid4(), [], []
    clock = [100.0]
    def timeout(seconds):
        timeouts.append(seconds)
        return asyncio.timeout(seconds)
    async def sleep(seconds):
        clock[0] += seconds
        await asyncio.sleep(0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(timeout=timeout, sleep=sleep))
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            clock[0] += 3  # Submission consumes three of the five allowed seconds.
        return httpx.Response(200, json=record(request_model, job_id, "running"))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(JobStillPending):
            await HTTPImageGateway(client).submit_and_wait(
                request_model, job_id=job_id, deadline_seconds=5, poll_seconds=20)
    assert calls == ["PUT", "GET"]
    assert timeouts == [5, 2]
    assert clock[0] == 105  # Poll sleep is clamped to the original remaining time.


async def test_put_consuming_entire_deadline_is_uncertain_without_second_put(request_model):
    job_id, calls, cancelled = uuid4(), [], []
    async def handle(request):
        calls.append(request.method)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(request.method)
            raise
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SubmissionUncertain) as error:
            await asyncio.wait_for(HTTPImageGateway(client).submit_and_wait(
                request_model, job_id=job_id, deadline_seconds=.015, poll_seconds=.001), timeout=.5)
    assert error.value.job_id == job_id
    assert calls == cancelled == ["PUT"]


@pytest.mark.parametrize("put_observed", [False, True])
async def test_hanging_status_read_respects_remaining_deadline(request_model, put_observed):
    job_id, calls, cancelled = uuid4(), [], []
    async def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return (httpx.Response(202, json=record(request_model, job_id))
                    if put_observed else httpx.Response(503))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(request.method)
            raise
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        expected = JobStillPending if put_observed else SubmissionUncertain
        with pytest.raises(expected):
            await asyncio.wait_for(HTTPImageGateway(client).submit_and_wait(
                request_model, job_id=job_id, deadline_seconds=.015, poll_seconds=.001), timeout=.5)
    assert calls == ["PUT", "GET"] and cancelled == ["GET"]


@pytest.mark.parametrize("field", ["deadline_seconds", "poll_seconds"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"), True, None, "1", 10**1000])
async def test_invalid_intervals_fail_before_network(request_model, field, value):
    def forbidden(request):
        pytest.fail("Invalid timing arguments must fail before network access")
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ValueError, match="finite and positive"):
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=uuid4(), **{field: value})


@pytest.mark.parametrize("phase", ["put", "get"])
@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_terminal_failures_keep_existing_error_code(request_model, phase, status):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT" and phase == "get":
            return httpx.Response(503)
        return httpx.Response(200, json=record(request_model, job_id, status))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == "IMAGE_OUT_OF_MEMORY"
    assert calls == (["PUT"] if phase == "put" else ["PUT", "GET"])


@pytest.mark.parametrize("phase", ["put", "get"])
@pytest.mark.parametrize("status", [400, 401, 403, 409, 422])
async def test_definitive_rejections_do_not_poll_or_resubmit(request_model, phase, status):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT" and phase == "get":
            return httpx.Response(503)
        return httpx.Response(status, text="private upstream message")
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == ("IMAGE_STATUS_UNAVAILABLE" if phase == "get" else
                                "IDEMPOTENCY_CONFLICT" if status == 409 else "IMAGE_SUBMISSION_REJECTED")
    assert calls == (["PUT"] if phase == "put" else ["PUT", "GET"])
    assert "private" not in str(error.value)


@pytest.mark.parametrize("phase", ["put", "get"])
@pytest.mark.parametrize("corruption", ["json", "id", "hash", "kind", "missing_result"])
async def test_protocol_mismatch_is_not_hidden_by_polling(request_model, phase, corruption):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT" and phase == "get":
            return httpx.Response(503)
        data = record(request_model, job_id, "succeeded")
        if corruption == "json":
            return httpx.Response(200, text="private malformed response")
        if corruption == "id":
            data["job_id"] = str(uuid4())
        elif corruption == "hash":
            data["request_hash"] = "b" * 64
        elif corruption == "kind":
            data["result"] = {"kind": "render_background", "scene_underlay": data["result"]["product_rgba"],
                              "product_rgba": data["result"]["product_rgba"],
                              "composed_preview": data["result"]["product_rgba"], "warnings": []}
        else:
            data["result"] = None
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == "IMAGE_PROTOCOL_ERROR"
    assert calls == (["PUT"] if phase == "put" else ["PUT", "GET"])
    assert "private" not in str(error.value)


async def test_request_snapshot_is_stable_if_caller_edits_model_while_awaiting(request_model):
    job_id = uuid4()
    original = request_model.model_copy(deep=True)
    def handle(request):
        if request.method == "PUT":
            assert json.loads(request.content) == original.model_dump(mode="json")
            request_model.pipeline_revision = "changed-while-waiting"
            request_model.source.sha256 = "b" * 64
            return httpx.Response(202, json=record(original, job_id))
        return httpx.Response(200, json=record(original, job_id, "succeeded"))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        result = await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert result.request_hash == content_hash(original)
    assert result.request_hash != content_hash(request_model)


async def test_external_cancellation_is_not_swallowed_or_retried(request_model):
    job_id, calls, entered = uuid4(), [], asyncio.Event()
    async def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return httpx.Response(503)
        entered.set()
        await asyncio.Event().wait()
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id))
        await asyncio.wait_for(entered.wait(), .5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls == ["PUT", "GET"]


@pytest.mark.parametrize("status", ["failed", "interrupted"])
@pytest.mark.parametrize("code", ["private-token", "PRIVATE TOKEN", "https://private.example/token",
                                 "IMAGE_ERROR\n", "오류", "A" * 81])
async def test_response_error_text_cannot_be_published_as_safe_code(request_model, status, code):
    job_id, calls = uuid4(), []
    def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return httpx.Response(503)
        data = record(request_model, job_id, status)
        data["error_code"] = code
        return httpx.Response(200, json=data)
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == "IMAGE_PROTOCOL_ERROR"
    assert str(error.value) == "Invalid job error code."
    assert calls == ["PUT", "GET"]


@pytest.mark.parametrize("status,code", [("queued", "IMAGE_OUT_OF_MEMORY"),
                                         ("running", "IMAGE_OUT_OF_MEMORY"), ("succeeded", "")])
async def test_nonfailure_records_cannot_contain_error_codes(request_model, status, code):
    job_id = uuid4()
    data = record(request_model, job_id, status)
    data["error_code"] = code
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == "IMAGE_PROTOCOL_ERROR"


@pytest.mark.parametrize("operation", ["submit", "get", "wait"])
async def test_existing_gateway_methods_share_response_code_guard(request_model, operation):
    job_id = uuid4()
    data = record(request_model, job_id, "failed")
    data["error_code"] = "private raw upstream error"
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=data))) as client:
        gateway = HTTPImageGateway(client)
        with pytest.raises(GatewayError) as error:
            if operation == "submit":
                await gateway.submit(request_model, job_id=job_id)
            elif operation == "get":
                await gateway.get(job_id)
            else:
                await gateway.wait(job_id, expected_request=request_model)
    assert error.value.code == "IMAGE_PROTOCOL_ERROR"


@pytest.mark.parametrize("code", ["A", "A" * 80, "MODEL_UNAVAILABLE", "WORKER_INTERRUPTED"])
async def test_valid_server_error_codes_remain_unchanged(request_model, code):
    job_id = uuid4()
    data = record(request_model, job_id, "failed")
    data["error_code"] = code
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(GatewayError) as error:
            await HTTPImageGateway(client).submit_and_wait(request_model, job_id=job_id)
    assert error.value.code == code
