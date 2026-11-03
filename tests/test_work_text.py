"""Installed OpenAI SDK boundaries with mock transport: no paid model calls."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
import pytest

from beauty_ai.contracts import LLMReview, ProductFacts, AdOptions
from beauty_ai.errors import GatewayError
from beauty_ai.text_gateway import OpenAITextGateway
from beauty_ai.work_ledger import AdWorkLedger
from beauty_ai.work_tracking import track_ad_work


def response_body(text=None, *, status="completed", refusal=False):
    content = ([{"type": "refusal", "refusal": "PRIVATE provider refusal detail"}] if refusal else
               [{"type": "output_text", "text": text if text is not None else
                 '{"issues":[],"uncertainties":[]}', "annotations": []}])
    return {"id": "resp-synthetic", "object": "response", "created_at": 1793667600,
            "status": status, "model": "returned-model", "error": None,
            "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
            "output": [{"id": "msg-synthetic", "type": "message", "status": "completed",
                        "role": "assistant", "content": content}],
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18,
                      "input_tokens_details": {"cached_tokens": 3},
                      "output_tokens_details": {"reasoning_tokens": 2}}}


def http_response(body=None, status=200):
    return httpx.Response(status, json=response_body() if body is None else body,
                          headers={"x-request-id": "req-synthetic"})


@pytest.fixture
async def tracked(tmp_path):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work = uuid4(), uuid4()
    ledger.create_work(project, work, "synthetic-product", required_stages=["review"])
    clients = []

    def build(handler, *, total_timeout=1):
        client = AsyncOpenAI(api_key="synthetic-test-key", max_retries=2,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        clients.append(client)
        gateway = OpenAITextGateway(client, text_model="configured-model", vision_model="configured-model",
                                    review_model="configured-model", total_timeout=total_timeout)
        return gateway

    yield ledger, project, work, build
    for client in clients:
        await client.close()


async def invoke(gateway, **changes):
    arguments = {"operation": "review_copy", "schema": LLMReview, "model": "configured-model",
                 "instruction": "Review supplied text.",
                 "content": [{"type": "input_text", "text": "PRIVATE prompt 7921"}],
                 "prompt_version": "review-v1", **changes}
    return await gateway._parse(**arguments)


def records(ledger, project):
    return ledger.export_dataset(project)


async def test_real_sdk_raw_observation_preserves_usage_and_untracked_client_retry_setting(tracked, tmp_path):
    ledger, project, work, build = tracked
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return http_response()

    gateway = build(handler)
    with track_ad_work(ledger, project, work):
        result = await invoke(gateway)
    data = records(ledger, project)
    assert result.data == LLMReview(issues=[], uncertainties=[])
    assert result.meta.input_tokens == 11 and result.meta.output_tokens == 7
    assert result.meta.model == "returned-model"
    assert gateway.client.max_retries == 2
    assert len(calls) == len(data["attempts"]) == len(data["receipts"]) == 1
    saved = data["receipts"][0]
    assert saved["status"] == "SUCCEEDED" and saved["provider_request_id"] == "req-synthetic"
    assert saved["usage"]["cached_input_tokens"] == 3 and saved["usage"]["reasoning_tokens"] == 2
    assert saved["usage"]["input_image_tokens"] is None
    assert saved["model"] == "returned-model" and saved["estimated_cost_usd"] is None
    assert calls[0]["store"] is False and calls[0]["text"]["format"]["type"] == "json_schema"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            raw = path.read_bytes()
            assert b"PRIVATE prompt 7921" not in raw and b"synthetic-test-key" not in raw


@pytest.mark.parametrize("text", ["{}", "not JSON", '{"issues":"wrong","uncertainties":[]}'])
async def test_real_sdk_parse_failure_keeps_usage_before_structured_validation(tracked, text):
    ledger, project, work, build = tracked
    gateway = build(lambda _: http_response(response_body(text)))
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    assert error.value.code == "INVALID_STRUCTURED_OUTPUT"
    saved = records(ledger, project)["receipts"][0]
    assert saved["status"] == "FAILED" and saved["usage"]["input_tokens"] == 11
    assert saved["error_code"] == "INVALID_STRUCTURED_OUTPUT"


@pytest.mark.parametrize("changes", [{"input_tokens": True}, {"input_tokens": -1}, {"total_tokens": 1}])
async def test_real_sdk_malformed_usage_is_not_coerced_into_accounting(tracked, changes):
    ledger, project, work, build = tracked
    body = response_body()
    body["usage"].update(changes)
    gateway = build(lambda _: http_response(body))
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    assert error.value.code == "WORK_USAGE_INVALID"
    saved = records(ledger, project)["receipts"][0]
    assert saved["status"] == "FAILED" and saved["usage"] is None


@pytest.mark.parametrize("status,refusal,code", [
    ("completed", True, "TEXT_REFUSAL"), ("incomplete", False, "TEXT_INCOMPLETE")])
async def test_refusal_and_incomplete_keep_observed_cost_evidence(tracked, status, refusal, code):
    ledger, project, work, build = tracked
    gateway = build(lambda _: http_response(response_body(status=status, refusal=refusal)))
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    assert error.value.code == code
    saved = records(ledger, project)["receipts"][0]
    assert saved["status"] == "FAILED" and saved["error_code"] == code
    assert saved["usage"]["output_tokens"] == 7
    assert "PRIVATE" not in json.dumps(saved)


async def test_real_sdk_http_error_is_failed_observed_once_without_sdk_retry(tracked):
    ledger, project, work, build = tracked
    calls = []

    def handler(request):
        calls.append(request)
        return http_response({"error": {"message": "PRIVATE provider error", "type": "rate_limit_error"}}, 429)

    gateway = build(handler)
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    saved = records(ledger, project)["receipts"][0]
    assert len(calls) == 1 and error.value.code == "TEXT_RATE_LIMIT"
    assert saved["status"] == "FAILED" and saved["usage"] is None
    assert saved["provider_request_id"] == "req-synthetic"


async def test_real_sdk_transport_timeout_is_unknown_and_same_operation_replay_is_blocked(tracked):
    ledger, project, work, build = tracked
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("PRIVATE transport exception", request=request)

    gateway = build(handler)
    with track_ad_work(ledger, project, work):
        with pytest.raises(GatewayError) as error:
            await invoke(gateway)
        assert error.value.code == "TEXT_PROVIDER_ERROR"
        with pytest.raises(GatewayError) as replay:
            await invoke(gateway)
        assert replay.value.code.startswith("WORK_")
    data = records(ledger, project)
    assert len(calls) == len(data["attempts"]) == len(data["receipts"]) == 1
    assert data["receipts"][0]["status"] == "UNKNOWN" and data["receipts"][0]["usage"] is None


async def test_total_deadline_keeps_unknown_receipt_and_includes_semaphore_wait(tracked):
    ledger, project, work, build = tracked

    async def handler(_):
        await asyncio.Event().wait()

    gateway = build(handler, total_timeout=.01)
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    assert error.value.code == "TEXT_DEADLINE"
    assert records(ledger, project)["receipts"][0]["status"] == "UNKNOWN"
    second = uuid4()
    ledger.create_work(project, second, "synthetic-second", required_stages=["review"])
    await gateway._semaphore.acquire()
    await gateway._semaphore.acquire()
    try:
        with track_ad_work(ledger, project, second), pytest.raises(GatewayError) as waiting:
            await invoke(gateway)
        assert waiting.value.code == "TEXT_DEADLINE"
    finally:
        gateway._semaphore.release()
        gateway._semaphore.release()
    assert len(records(ledger, project)["attempts"]) == 1


async def test_cancelled_sdk_call_persists_unknown_then_propagates_cancellation(tracked):
    ledger, project, work, build = tracked
    entered = asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    gateway = build(handler)
    with track_ad_work(ledger, project, work):
        task = asyncio.create_task(invoke(gateway))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    saved = records(ledger, project)["receipts"][0]
    assert saved["status"] == "UNKNOWN" and saved["error_code"] == "TEXT_CANCELLED"
    assert saved["usage"] is None


@pytest.mark.parametrize("client", [
    SimpleNamespace(responses=SimpleNamespace(parse=lambda **_: None)),
    SimpleNamespace(with_options=lambda **_: None, responses=SimpleNamespace(parse=lambda **_: None)),
    SimpleNamespace(with_options=lambda **_: None,
                    responses=SimpleNamespace(with_raw_response=SimpleNamespace(parse=lambda **_: None))),
    SimpleNamespace(with_options=lambda: None,
                    responses=SimpleNamespace(with_raw_response=SimpleNamespace(parse=lambda **_: None))),
])
async def test_unsafe_tracking_clients_are_rejected_before_attempt_or_sdk(tracked, client):
    ledger, project, work, _ = tracked
    gateway = OpenAITextGateway(client, text_model="m", vision_model="m", review_model="m")
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await invoke(gateway)
    assert error.value.code == "WORK_CLIENT_UNSAFE"
    assert records(ledger, project)["attempts"] == []


async def test_cancellation_still_propagates_when_receipt_storage_needs_recovery(tracked, monkeypatch):
    ledger, project, work, build = tracked
    entered = asyncio.Event()

    async def handler(_):
        entered.set()
        await asyncio.Event().wait()

    def fail(*_):
        raise GatewayError("WORK_STORAGE_ERROR", "PRIVATE storage detail")

    gateway = build(handler)
    with track_ad_work(ledger, project, work):
        monkeypatch.setattr(ledger, "record_receipt", fail)
        task = asyncio.create_task(invoke(gateway))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await task
    pending = cancelled.value.work_capture_error
    assert pending.code == "WORK_CAPTURE_PENDING" and pending.recovery_saved
    assert pending.receipt["status"] == "UNKNOWN" and pending.receipt["usage"] is None


async def test_capture_storage_failure_keeps_safe_recovery_and_retry_performs_no_sdk_call(tracked, monkeypatch, tmp_path):
    ledger, project, work, build = tracked
    calls = []
    gateway = build(lambda request: calls.append(request) or http_response())
    original = ledger.record_receipt

    def fail(*_):
        raise GatewayError("WORK_STORAGE_ERROR", "PRIVATE storage detail")

    with track_ad_work(ledger, project, work):
        monkeypatch.setattr(ledger, "record_receipt", fail)
        with pytest.raises(GatewayError) as error:
            await invoke(gateway)
        assert error.value.code == "WORK_CAPTURE_PENDING" and error.value.recovery_saved
        assert error.value.receipt["usage"]["input_tokens"] == 11
        with pytest.raises(GatewayError):
            await invoke(gateway)
        assert len(calls) == 1
    pending = list((tmp_path / ".cache").rglob("*.json"))
    assert len(pending) == 1 and pending[0].stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(ledger, "record_receipt", original)
    with track_ad_work(ledger, project, work):
        assert len(records(ledger, project)["receipts"]) == 1
        with pytest.raises(GatewayError) as replay:
            await invoke(gateway)
        assert replay.value.code == "WORK_ALREADY_RECORDED"
    assert len(calls) == 1 and not list((tmp_path / ".cache").rglob("*.json"))


async def test_domain_validation_after_parse_does_not_rewrite_successful_model_receipt(tracked):
    ledger, project, work, build = tracked
    body = response_body('{"variants":[]}')
    gateway = build(lambda _: http_response(body))
    facts = ProductFacts(product_id=uuid4(), revision=1, product_name="Synthetic cream",
                         functional_status="general", functional_categories=[], confirmed_claims=[])
    options = AdOptions(purpose="소개", target="성인", tone="차분함", channel="instagram_feed",
                        market="KR", style_preset_id="minimal", layout_template_id="split-v1")
    with track_ad_work(ledger, project, work), pytest.raises(GatewayError) as error:
        await gateway.draft_campaign(facts, options, count=1)
    assert error.value.code == "INVALID_DOMAIN_OUTPUT"
    saved = records(ledger, project)["receipts"][0]
    assert saved["status"] == "SUCCEEDED" and saved["error_code"] is None


async def test_request_identity_changes_for_prompt_version_and_operation(tracked):
    ledger, project, work, build = tracked
    calls = []

    def handler(request):
        calls.append(request)
        response = http_response()
        response.headers["x-request-id"] = "req-" + str(len(calls))
        return response

    gateway = build(handler)
    with track_ad_work(ledger, project, work):
        await invoke(gateway)
        await invoke(gateway, prompt_version="review-v2")
        await invoke(gateway, operation="rewrite_copy", prompt_version="review-v2")
    data = records(ledger, project)
    assert len(calls) == len(data["attempts"]) == 3
    assert len({row["request_hash"] for row in data["attempts"]}) == 3
