"""Image-only two-track boundary; all provider requests use MockTransport."""
from copy import deepcopy
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from uuid import uuid4
import base64

import httpx
import pytest
from pydantic import ValidationError

from src.image_generation.contracts import ExternalImageRequest, content_hash
from src.image_generation.errors import GatewayError
from src.image_generation import export_candidate
from src.image_generation.ad_resize import resize_ad_png
from src.image_generation.composition import CompositionLayout
from . import test_backend_adapter as backend
from .helpers import FONT, copy_output, layout


def candidate(mode="freeform", **changes):
    copy = copy_output()
    values = dict(generation_mode=mode, copy_output=copy, copy_hash=content_hash(copy),
                  ad_copy=copy["ad_copy"], caption=copy["caption"], hashtags=copy["hashtags"])
    values.update(changes)
    return backend.body(**values)


def fields(request):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.read())
    return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True).decode()
            for p in message.iter_parts()
            if p.get_param("name", header="content-disposition") != "image[]"}


def test_contract_legacy_request_serialization():
    request = ExternalImageRequest(operation="generate", prompt=dict(
        positive="밝은 화장품 배경", prompt_version="v1", source_revision=1))
    data = request.model_dump(mode="json")
    assert "text_rendering" not in data
    assert content_hash(request) == content_hash(data)
    with pytest.raises(ValidationError):
        ExternalImageRequest(**data, text_rendering="freeform-v1")


@pytest.mark.parametrize("mode", [None, "composed", "freeform"])
def test_contract_complete_copy_and_mode_replay(tmp_path, mode):
    calls = []
    def handler(req):
        calls.append(req)
        return backend.response()
    adapter = backend.setup(tmp_path, handler)
    body = candidate(mode)
    project = uuid4()
    one = adapter.generate(body, project_id=project)
    two = backend.setup(tmp_path, handler).generate(body, project_id=project)
    assert one == two and len(calls) == 1
    assert one.receipt["generation_mode"] == (mode or "composed")


@pytest.mark.parametrize("part", ["hash", "ad", "caption", "hashtags", "plan"])
def test_contract_copy_mismatch_before_assets_or_attempt(tmp_path, monkeypatch, part):
    body = candidate()
    if part == "hash":
        body.copy_hash = "b" * 64
    elif part == "ad":
        body.ad_copy.headline = "다른 문구"
    elif part == "caption":
        body.caption = "다른 본문"
    elif part == "hashtags":
        body.hashtags = ["#다름"]
    else:
        body.copy_output["plan"]["target"] = "다른 대상"
    adapter = backend.setup(tmp_path, lambda _: pytest.fail("No provider call"))
    monkeypatch.setattr(adapter.processor.assets, "put_upload", lambda *a, **k: pytest.fail("No asset write"))
    with pytest.raises(GatewayError) as error:
        adapter.generate(body, project_id=uuid4())
    assert error.value.code == "BACKEND_COPY_INVALID"


def test_contract_mode_change_conflicts_and_freeform_requires_snapshot(tmp_path):
    with pytest.raises(ValidationError):
        backend.body(generation_mode="freeform")
    with pytest.raises(ValidationError):
        candidate(safe_zone=dict(left=.1, top=.1, right=.9, bottom=.3))
    calls = []
    def handler(req):
        calls.append(req)
        return backend.response()
    adapter = backend.setup(tmp_path, handler)
    project = uuid4()
    body = candidate("composed")
    adapter.generate(body, project_id=project)
    with pytest.raises(GatewayError) as error:
        adapter.generate(body.model_copy(update={"generation_mode": "freeform"}), project_id=project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT"
    assert len(calls) == 1


def test_contract_nullable_optional_copy_fields(tmp_path):
    copy = copy_output()
    copy["ad_copy"].update(subline="", cta="")
    body = candidate(copy_output=copy, copy_hash=content_hash(copy),
                     ad_copy=dict(headline=copy["ad_copy"]["headline"], subline=None, cta=None))
    adapter = backend.setup(tmp_path, lambda _: backend.response())
    assert adapter.generate(body, project_id=uuid4()).receipt["state"] == "succeeded"


def test_contract_analysis_failure_blocks_freeform(tmp_path):
    body = candidate(product_analysis=backend.analysis_result(analysis_succeeded=False))
    adapter = backend.setup(tmp_path, lambda _: pytest.fail("No provider call"))
    with pytest.raises(GatewayError) as error:
        adapter.generate(body, project_id=uuid4())
    assert error.value.code == "PRODUCT_ANALYSIS_FAILED"


@pytest.mark.parametrize("ratio,placement,size", [
    ("3:4", "feed", (1152, 1536)), ("4:5", "feed", (1152, 1440)),
    ("9:16", "reels", (1152, 2048)), ("9:16", "story", (1152, 2048))])
def test_transport_three_free_one_composed_and_replay(tmp_path, ratio, placement, size):
    calls = []
    def handler(req):
        calls.append(fields(req))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(backend.png(size=size)).decode()}],
            "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}},
            headers={"x-request-id": f"req-{len(calls)}"})
    adapter = backend.setup(tmp_path, handler, budget="10")
    project = uuid4()
    bodies = [candidate(mode, job_id=f"batch-one-{index}",
                        layout_selection=dict(aspect_ratio=ratio, placement=placement))
              for index, mode in enumerate(["freeform"] * 3 + ["composed"])]
    results = [adapter.generate(body, project_id=project) for body in bodies]
    assert [r.receipt["generation_mode"] for r in results] == ["freeform"] * 3 + ["composed"]
    assert len({r.receipt["attempt_id"] for r in results}) == 4
    assert all(r.receipt["usage"]["total_tokens"] == 150 for r in results)
    assert all(r.receipt["actual_cost_usd"] is None for r in results)
    assert all(c["n"] == "1" and c["size"] == f"{size[0]}x{size[1]}" for c in calls)
    for c in calls[:3]:
        prompt = c["prompt"]
        for text in [bodies[0].image_prompt.prompt, bodies[0].ad_copy.headline, "워터마크 제외 정책", "원본 색"]:
            assert text in prompt
        for text in ["PRODUCT_TARGET", "UI_SAFE", "광고 문구·가격·배지를 새로 그리지 않는다", bodies[0].caption]:
            assert text not in prompt
        if (ratio, placement) != ("3:4", "feed"):
            assert bodies[0].ad_copy.cta not in prompt and "이미지 안에 CTA를 추가하지 않는다" in prompt
        else:
            assert bodies[0].ad_copy.cta in prompt
    assert "광고 문구·가격·배지를 새로 그리지 않는다" in calls[3]["prompt"]
    assert "PRODUCT_TARGET" in calls[3]["prompt"]
    assert bodies[0].ad_copy.headline not in calls[3]["prompt"]
    restarted = backend.setup(tmp_path, handler, budget="0")
    assert [restarted.generate(body, project_id=project) for body in bodies] == results
    assert len(calls) == 4


@pytest.mark.parametrize("mode", ["freeform", "composed"])
def test_transport_unknown_never_reposts(tmp_path, mode):
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("no receipt")
    adapter = backend.setup(tmp_path, handler)
    body, project = candidate(mode), uuid4()
    for _ in range(2):
        with pytest.raises(GatewayError):
            adapter.generate(body, project_id=project)
    assert len(calls) == 1
    receipt = adapter.get_receipt(body.job_id, project_id=project)
    assert receipt["state"] == "unknown" and receipt["generation_mode"] == mode


@pytest.mark.parametrize("mode", ["freeform", "composed"])
def test_transport_budget_zero_blocks_both(tmp_path, mode):
    adapter = backend.setup(tmp_path, lambda _: pytest.fail("No provider call"), budget="0")
    with pytest.raises(GatewayError):
        adapter.generate(candidate(mode), project_id=uuid4())


@pytest.mark.parametrize("ratio,placement,size,final", [
    ("3:4", "feed", (1152, 1536), (1080, 1440)),
    ("4:5", "feed", (1152, 1440), (1080, 1350)),
    ("9:16", "reels", (1152, 2048), (1080, 1920)),
    ("9:16", "story", (1152, 2048), (1080, 1920))])
def test_export_freeform_is_only_resize_no_font_or_overlay(tmp_path, monkeypatch, ratio, placement, size, final):
    import src.image_generation.candidate_export as module
    monkeypatch.setattr(module, "compose_ad_png", lambda *a, **k: pytest.fail("No second text overlay"))
    body = candidate(layout_selection=dict(aspect_ratio=ratio, placement=placement))
    adapter = backend.setup(tmp_path, lambda _: httpx.Response(200, json={"data": [
        {"b64_json": base64.b64encode(backend.png(size=size)).decode()}]}))
    generated = adapter.generate(body, project_id=uuid4())
    result = export_candidate(generated, copy_output=body.copy_output)
    assert result.image_bytes == resize_ad_png(generated.image_bytes, final_size=final).image_bytes
    assert (result.width, result.height) == final
    assert result.receipt["provider_calls"] == 0 and not result.receipt["protected_region_check"]
    assert result.receipt["needs_manual_check"] is True
    assert result.caption == body.caption and result.hashtags == tuple(body.hashtags)
    assert result.source_image_sha256 == generated.sha256
    assert result.copy_hash == body.copy_hash and result.job_id == body.job_id
    with pytest.raises(GatewayError) as error:
        export_candidate(generated, copy_output=body.copy_output, font_path=FONT)
    assert error.value.code == "CANDIDATE_FREEFORM_CONFIG_INVALID"


def test_export_composed_local_failure_repair_without_new_generation(tmp_path):
    calls = []
    def handler(req):
        calls.append(req)
        return backend.response()
    body, project = candidate("composed"), uuid4()
    adapter = backend.setup(tmp_path, handler)
    generated = adapter.generate(body, project_id=project)
    with pytest.raises(GatewayError) as error:
        export_candidate(generated, copy_output=body.copy_output)
    assert error.value.code == "CANDIDATE_COMPOSITION_REQUIRED"
    with pytest.raises(GatewayError) as error:
        export_candidate(generated, copy_output=body.copy_output,
                         layout=CompositionLayout(**layout()), font_path=tmp_path / "missing.ttf")
    assert error.value.code == "COMPOSITION_FONT_UNAVAILABLE"
    result = export_candidate(generated, copy_output=body.copy_output,
                              layout=CompositionLayout(**layout()), font_path=FONT)
    assert len(calls) == 1 and result.receipt["provider_calls"] == 0
    assert result.receipt["generation_mode"] == "composed"
    assert (result.width, result.height) == (1080, 1350)
    assert result.image_bytes != resize_ad_png(generated.image_bytes).image_bytes


@pytest.mark.parametrize("part,code", [
    ("image_hash", "COMPOSITION_IMAGE_HASH_MISMATCH"),
    ("dimensions", "CANDIDATE_IMAGE_METADATA_INVALID"),
    ("copy", "COMPOSITION_COPY_HASH_MISMATCH"),
    ("mode", "CANDIDATE_NOT_EXPORTABLE"),
    ("state", "CANDIDATE_NOT_EXPORTABLE")])
def test_export_rejects_changed_candidate_or_copy(tmp_path, part, code):
    body = candidate()
    generated = backend.setup(tmp_path, lambda _: backend.response()).generate(body, project_id=uuid4())
    copy = deepcopy(body.copy_output)
    if part == "image_hash": generated = replace(generated, sha256="b" * 64)
    elif part == "dimensions": generated = replace(generated, width=2048)
    elif part == "copy": copy["plan"]["target"] = "다른 대상"
    elif part == "mode": generated = replace(generated, receipt={**generated.receipt, "generation_mode": "other"})
    else: generated = replace(generated, receipt={**generated.receipt, "state": "unknown"})
    with pytest.raises(GatewayError) as error:
        export_candidate(generated, copy_output=copy)
    assert error.value.code == code


@pytest.mark.parametrize("modes", [["freeform"], ["composed"],
    ["freeform", "composed"], ["freeform", "freeform", "composed"]])
def test_transport_single_and_one_one_two_one(tmp_path, modes):
    calls = []
    def handler(req):
        calls.append(fields(req))
        return backend.response()
    adapter, project = backend.setup(tmp_path, handler, budget="10"), uuid4()
    results = [adapter.generate(candidate(mode, job_id=f"candidate-{i}"), project_id=project)
               for i, mode in enumerate(modes)]
    assert [r.receipt["generation_mode"] for r in results] == modes
    assert len(calls) == len(modes) and all(c["n"] == "1" for c in calls)


def test_contract_model_copy_cannot_bypass_freeform_requirements(tmp_path):
    body = candidate().model_copy(update={"copy_output": None})
    adapter = backend.setup(tmp_path, lambda _: pytest.fail("No provider call"))
    with pytest.raises(ValidationError):
        adapter.generate(body, project_id=uuid4())


def test_export_legacy_receipt_mode_defaults_to_composed(tmp_path):
    body = candidate(None)
    generated = backend.setup(tmp_path, lambda _: backend.response()).generate(body, project_id=uuid4())
    receipt = dict(generated.receipt)
    receipt.pop("generation_mode")
    result = export_candidate(replace(generated, receipt=receipt), copy_output=body.copy_output,
                              layout=CompositionLayout(**layout()), font_path=FONT)
    assert result.receipt["generation_mode"] == "composed"


def test_export_tag_only_caption_has_no_leading_blank_lines(tmp_path):
    copy = copy_output()
    copy["caption"] = ""
    body = candidate(copy_output=copy, caption="", copy_hash=content_hash(copy))
    generated = backend.setup(tmp_path, lambda _: backend.response()).generate(body, project_id=uuid4())
    result = export_candidate(generated, copy_output=copy)
    assert result.caption_bytes == " ".join(copy["hashtags"]).encode("utf-8")
