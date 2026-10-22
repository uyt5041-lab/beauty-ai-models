import base64
import struct
import zlib
from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image
import pytest

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore


def png(*, size=(1024, 1024), alpha=False, empty=False, opaque=False, format="PNG"):
    image = Image.new("RGBA" if alpha else "RGB", size, (245, 120, 80, 0) if alpha else (245, 120, 80))
    if alpha and not empty:
        image.paste((80, 90, 100, 255), (0, 0, size[0] if opaque else size[0] // 2, size[1]))
    buffer = BytesIO()
    image.save(buffer, format=format)
    image.close()
    return buffer.getvalue()


def request(**kwargs):
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
                                prompt=ImagePrompt(positive="Bright flowers, product space, no advertising text.",
                                                   prompt_version="v1", source_revision=1), **kwargs)


def processor(tmp_path, handler=None, *, budget="10"):
    assets = LocalAssetStore(tmp_path / "assets")
    attempts = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(handler) if handler else None)
    return ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd=budget), assets, attempts


def response(data):
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data).decode()}],
                                     "usage": {"input_tokens": 15, "output_tokens": 30, "total_tokens": 45}},
                          headers={"x-request-id": "req-test"})


def test_default_disabled_before_attempt_or_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Actual transport must never be created")
    monkeypatch.setattr(httpx, "HTTPTransport", forbidden)
    proc, _, attempts = processor(tmp_path)
    job, project = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_CALLS_DISABLED"
    assert attempts.get(job, project) is None


@pytest.mark.parametrize("operation", ["generate", "edit", "cutout"])
def test_valid_image_saved_and_same_job_reuses_result(tmp_path, operation):
    calls = []
    def handler(req):
        calls.append(req)
        return response(png(alpha=operation == "cutout"))
    proc, assets, attempts = processor(tmp_path, handler)
    job, project = uuid4(), uuid4()
    payload = request()
    if operation != "generate":
        ref = assets.put_upload(png(size=(100, 100)), project_id=project)
        payload = ExternalImageRequest(operation=operation, product_images=[ref], width=1024, height=1024,
                                       prompt=payload.prompt if operation == "edit" else None)
    result = proc.process_job(payload, project, job)
    assert result.image.width == 1024
    assert result.needs_manual_check is True
    assert result.usage.total_tokens == 45
    assert proc.process_job(payload, project, job) == result
    assert len(calls) == 1
    assert attempts.get(job, project)["state"] == "succeeded"
    assert assets.read_bytes(result.image, project_id=project)


def test_foreign_asset_fails_without_reservation_or_post(tmp_path):
    proc, assets, attempts = processor(tmp_path, lambda _: pytest.fail("No POST"))
    ref = assets.put_upload(png(size=(50, 50)), project_id=uuid4())
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(ExternalImageRequest(operation="cutout", product_images=[ref]), project, job)
    assert error.value.code == "ASSET_NOT_FOUND"
    assert attempts.get(job, project) is None


def test_budget_denial_has_zero_posts(tmp_path):
    proc, _, attempts = processor(tmp_path, lambda _: pytest.fail("No POST"), budget="0")
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_BUDGET_EXCEEDED"
    assert attempts.get(job, project) is None


def test_response_loss_and_restart_never_resubmit(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("PRIVATE RAW REQUEST")
    proc, _, attempts = processor(tmp_path, handler)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_SUBMISSION_UNKNOWN"
    assert "PRIVATE" not in str(error.value)
    assert attempts.get(job, project)["state"] == "unknown"
    restarted, _, _ = processor(tmp_path, handler)
    with pytest.raises(GatewayError) as conflict:
        restarted.process_job(request(), project, job)
    assert conflict.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    assert len(calls) == 1


@pytest.mark.parametrize("data", [b"not an image", png(size=(128, 128)), png(format="JPEG")])
def test_invalid_provider_pixels_do_not_publish_result(tmp_path, data):
    proc, _, attempts = processor(tmp_path, lambda _: response(data))
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code in {"PROVIDER_OUTPUT_INVALID", "PROVIDER_RESPONSE_INVALID"}
    receipt = attempts.get(job, project)
    assert receipt["state"] == "failed" and receipt["result"] is None
    assert receipt["reservation_usd"] == "1.000000"


@pytest.mark.parametrize("data", [png(), png(alpha=True, empty=True), png(alpha=True, opaque=True)])
def test_cutout_requires_visible_foreground_and_transparent_background(tmp_path, data):
    proc, assets, attempts = processor(tmp_path, lambda _: response(data))
    project, job = uuid4(), uuid4()
    ref = assets.put_upload(png(size=(100, 100)), project_id=project)
    with pytest.raises(GatewayError) as error:
        proc.process_job(ExternalImageRequest(operation="cutout", product_images=[ref], width=1024, height=1024), project, job)
    assert error.value.code == "PROVIDER_OUTPUT_INVALID"
    assert attempts.get(job, project)["result"] is None


def test_storage_failure_keeps_provider_receipt_and_blocks_repost(tmp_path, monkeypatch):
    calls = []
    def handler(req):
        calls.append(req)
        return response(png())
    proc, assets, attempts = processor(tmp_path, handler)
    def fail(*args, **kwargs):
        raise OSError("PRIVATE storage path")
    monkeypatch.setattr(assets, "put_upload", fail)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_RESULT_STORAGE_FAILED"
    assert "PRIVATE" not in str(error.value)
    receipt = attempts.get(job, project)
    assert receipt["provider_request_id"] == "req-test" and receipt["usage"]["total_tokens"] == 45
    assert receipt["result"] is None
    with pytest.raises(GatewayError):
        proc.process_job(request(), project, job)
    assert len(calls) == 1


def test_receipt_commit_failure_leaves_submitting_and_prevents_restart_post(tmp_path, monkeypatch):
    calls = []
    def handler(req):
        calls.append(req)
        return response(png())
    proc, _, attempts = processor(tmp_path, handler)
    project, job = uuid4(), uuid4()
    def fail(*args, **kwargs):
        raise GatewayError("PROVIDER_ATTEMPT_STORAGE_ERROR", "Unavailable")
    monkeypatch.setattr(attempts, "finish", fail)
    with pytest.raises(GatewayError):
        proc.process_job(request(), project, job)
    restarted, _, receipts = processor(tmp_path, handler)
    assert receipts.get(job, project)["state"] == "submitting"
    with pytest.raises(GatewayError):
        restarted.process_job(request(), project, job)
    assert len(calls) == 1


def test_oversized_png_header_is_output_failure_not_unknown_submission(tmp_path):
    data = bytearray(png(size=(1, 1)))
    data[16:24] = struct.pack(">II", 10000, 10000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]) & 0xFFFFFFFF)
    proc, _, attempts = processor(tmp_path, lambda _: response(bytes(data)))
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    assert error.value.code == "PROVIDER_OUTPUT_INVALID"
    receipt = attempts.get(job, project)
    assert receipt["state"] == "failed"
    assert receipt["submission_status"] == "RESPONSE_RECEIVED"
    assert receipt["result"] is None
