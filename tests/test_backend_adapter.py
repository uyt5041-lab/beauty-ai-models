import base64
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import hashlib
from io import BytesIO
import sqlite3
from uuid import uuid4

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.backend_adapter import BackendImageAdapter, BackendImageInput
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore
from beauty_ai.prompt_policy import WATERMARK_POLICY_TEXT


def png(color="coral", size=(32, 40)):
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def body(**change):
    values = dict(job_id="service-001", copy_revision=1, copy_hash="a" * 64,
        image_prompt={"concept": "간결한 루틴", "prompt": "밝은 배경. 원본 제품은 그대로 둔다.",
                      "mood": ["청량", "선명"]},
        ad_copy={"headline": "오늘의 루틴", "subline": "나를 위한 시간", "cta": "제품 살펴보기"},
        caption="나의 루틴", hashtags=["#화장품"], product_images=[png(), png("blue")])
    values.update(change)
    return BackendImageInput(**values)


def setup(tmp_path, handler, *, budget="10"):
    assets = LocalAssetStore(tmp_path / "assets")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(),
                                 transport=httpx.MockTransport(handler) if handler else None)
    proc = ExternalImageProcessor(assets, adapter, ProviderAttemptStore(tmp_path / "attempts.sqlite3"),
                                  budget_limit_usd=budget)
    return BackendImageAdapter(proc, tmp_path / "bindings.sqlite3")


def response():
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png(size=(1152, 1440))).decode()}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}},
        headers={"x-request-id": "req-offline"})


@pytest.mark.parametrize("counts", [(2, 0), (5, 5)])
def test_real_boundary_order_metadata_and_restart_replay(tmp_path, counts):
    calls = []
    def handler(req):
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.read())
        calls.append(list(message.iter_parts()))
        return response()
    n, m = counts
    photos = [png((10 * i, 80, 120)) for i in range(n+m)]
    payload = body(product_images=photos[:n], reference_images=photos[n:])
    project = uuid4()
    first = setup(tmp_path, handler).generate(payload, project_id=project)
    second = setup(tmp_path, handler).generate(payload, project_id=project)
    assert len(calls) == 1
    assert first == second and first.mock
    assert first.width == 1152 and first.height == 1440
    assert hashlib.sha256(first.image_bytes).hexdigest() == first.sha256
    assert first.receipt["usage"]["total_tokens"] == 30
    assert first.receipt["elapsed_ms"] >= 0 and first.receipt["actual_cost_usd"] is None
    assert first.copy_hash == payload.copy_hash
    files = [part.get_payload(decode=True) for part in calls[0]
             if part.get_param("name", header="content-disposition") == "image[]"]
    assert files == photos
    prompt = next(part.get_payload(decode=True).decode() for part in calls[0]
                  if part.get_param("name", header="content-disposition") == "prompt")
    assert payload.image_prompt.prompt in prompt and "청량, 선명" in prompt
    assert WATERMARK_POLICY_TEXT in prompt
    assert "오늘의 루틴" not in prompt  # Copy is kept for later composition, not drawn accidentally.


@pytest.mark.parametrize("change", [dict(copy_hash="b" * 64), dict(copy_revision=2),
    dict(ad_copy={"headline": "새 카피"}), dict(product_images=[png("blue"), png()]),
    dict(caption="changed"), dict(image_prompt={"concept": "다름", "prompt": "수정한 배경", "mood": []})])
def test_changed_input_cannot_reuse_service_candidate(tmp_path, change):
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    adapter, project = setup(tmp_path, handler), uuid4()
    adapter.generate(body(), project_id=project)
    with pytest.raises(GatewayError, match="different inputs"):
        adapter.generate(body(**change), project_id=project)
    assert len(calls) == 1


@pytest.mark.parametrize("change", [dict(product_images=[png()]), dict(product_images=[png()]*6),
    dict(reference_images=[png()]*6), dict(copy_revision=True), dict(copy_hash="bad"),
    dict(product_images=["not bytes", "not bytes"])])
def test_invalid_inputs_rejected(change):
    with pytest.raises(ValidationError):
        body(**change)


def test_concurrent_duplicate_posts_at_most_once(tmp_path):
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    adapter, project = setup(tmp_path, handler), uuid4()
    def run(_):
        try:
            return adapter.generate(body(), project_id=project)
        except GatewayError as error:
            assert error.code == "PROVIDER_ATTEMPT_CONFLICT"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert len(calls) == 1 and any(results)


def test_uncertain_submission_does_not_retry_after_restart(tmp_path):
    calls = []
    def handler(req):
        calls.append(1)
        raise httpx.ReadTimeout("lost")
    project = uuid4()
    for _ in range(2):
        with pytest.raises(GatewayError):
            setup(tmp_path, handler).generate(body(), project_id=project)
    assert calls == [1]
    receipt = setup(tmp_path, handler).get_receipt("service-001", project_id=project)
    assert receipt["actual_cost_usd"] is None and receipt["error_code"] is not None
    assert receipt["model"] == "gpt-image-2.5-sunburst-2026-09-08"


def test_handoff_to_final_resize_keeps_source_and_real_dimensions(tmp_path):
    from beauty_ai.ad_resize import resize_ad_png
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    generated = setup(tmp_path, handler).generate(body(), project_id=uuid4())
    final = resize_ad_png(generated.image_bytes)
    assert final.source_sha256 == generated.sha256
    assert (generated.width, generated.height) == (1152, 1440)
    with Image.open(BytesIO(final.image_bytes)) as image:
        assert image.size == (final.width, final.height) == (1080, 1350)
    assert calls == [1]


def test_safe_zone_is_validated_and_preserved_in_prompt(tmp_path):
    captured = []
    def handler(req):
        captured.append(req.read())
        return response()
    zone = dict(left=0.08, top=0.06, right=0.92, bottom=0.30)
    setup(tmp_path, handler).generate(body(safe_zone=zone), project_id=uuid4())
    assert "카피 여백: 왼쪽 0.08".encode() in captured[0]
    for bad in (dict(zone, left=0.0), dict(zone, bottom=0.05)):
        with pytest.raises(ValidationError):
            body(safe_zone=bad)


def test_disabled_budget_and_invalid_bytes_never_call(tmp_path):
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    with pytest.raises(GatewayError) as denied:
        setup(tmp_path / "disabled", None).generate(body(), project_id=uuid4())
    assert denied.value.code == "PROVIDER_CALLS_DISABLED"
    with pytest.raises(GatewayError) as denied:
        setup(tmp_path / "budget", handler, budget="0").generate(body(), project_id=uuid4())
    assert denied.value.code == "PROVIDER_BUDGET_EXCEEDED"
    with pytest.raises(GatewayError):
        setup(tmp_path / "bad", handler).generate(body(product_images=[b"bad", png()]), project_id=uuid4())
    assert calls == []


def test_binding_tampering_blocks_call(tmp_path):
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    adapter, project = setup(tmp_path, handler), uuid4()
    adapter.generate(body(), project_id=project)
    with sqlite3.connect(adapter.path) as db:
        db.execute("UPDATE backend_image_bindings SET request_hash=?", ("0" * 64,))
    with pytest.raises(GatewayError) as invalid:
        adapter.generate(body(), project_id=project)
    assert invalid.value.code == "BACKEND_BINDING_INVALID" and calls == [1]


def test_project_scope_does_not_share_job_or_assets(tmp_path):
    calls = []
    def handler(req):
        calls.append(1)
        return response()
    adapter = setup(tmp_path, handler)
    a = adapter.generate(body(), project_id=uuid4())
    b = adapter.generate(body(), project_id=uuid4())
    assert len(calls) == 2 and a.receipt["model_job_id"] != b.receipt["model_job_id"]
