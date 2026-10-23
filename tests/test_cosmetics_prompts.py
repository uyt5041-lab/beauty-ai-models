"""Cosmetics policy acceptance, compatibility and offline transport checks."""
from __future__ import annotations

import base64
from copy import deepcopy
from email import policy
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
import re
from uuid import UUID, uuid4

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import AssetRef, ExternalImageRequest, content_hash
from beauty_ai.cosmetics_prompts import (
    CosmeticsPromptBrief, PRESETS, build_cosmetics_cutout_request,
    build_cosmetics_prompt, build_cosmetics_request,
    render_cosmetics_provider_prompt,
)
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError
from beauty_ai.provider_attempts import ProviderAttemptStore


def brief(**changes):
    return CosmeticsPromptBrief(product_description="A white serum bottle with a blue pump and its original printed label.",
                                source_revision=4, **changes)


def png(*, color=(240, 240, 240, 255), size=(16, 16), transparent=False):
    with Image.new("RGBA", size, color) as image:
        if transparent:
            image.paste((0, 0, 0, 0), (0, 0, size[0] // 3, size[1]))
        output = BytesIO()
        image.save(output, format="PNG")
    return output.getvalue()


def asset(data=b"asset", *, number=1):
    return AssetRef(asset_id=UUID(int=number), sha256=hashlib.sha256(data).hexdigest(),
                    width=16, height=16, mime_type="image/png")


def response(data=None):
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data or png()).decode("ascii")}]},
                          headers={"x-request-id": "mock-cosmetics-response"})


def multipart(wire):
    envelope = b"Content-Type: " + wire.headers["content-type"].encode() + b"\r\n\r\n" + wire.content
    return [(part.get_param("name", header="content-disposition"), part.get_payload(decode=True),
             part.get_filename())
            for part in BytesParser(policy=policy.default).parsebytes(envelope).iter_parts()]


@pytest.mark.parametrize("preset_id", ["clean_studio", "premium_minimal", "vivid_pop"])
@pytest.mark.parametrize("layout_id,area", [
    ("product_right", "left 35%"), ("product_left", "right 35%"),
    ("product_center_bottom", "upper 30%"),
])
def test_each_preset_reserves_copy_space_without_recoloring_or_erasing_packaging(preset_id, layout_id, area):
    request = build_cosmetics_request(brief(preset_id=preset_id, layout_id=layout_id,
                                          brand_colors=["#0099CC", "#Ff2255"]), product_images=[asset()])
    text = OpenAIImagesAdapter._prompt(request)
    assert request.pipeline_revision == "2" and request.operation == "edit"
    assert request.width * 5 == request.height * 4
    assert area in text and "Portrait 4:5" in text
    assert "#0099CC, #Ff2255" in text
    assert "instead of the preset's default background colors" in text
    assert "never recolor the product" in text
    assert not re.search(r"\b(?:flowers?|floral|botanical|petals?)\b", text, re.IGNORECASE)
    assert "original packaging artwork" in text
    assert "Preserve existing product label and logo text" in text
    assert "Do not add advertising copy" in text
    assert "ingredients, efficacy claims, certification marks" in text
    assert request.prompt.prompt_version == f"cosmetics-v1/{preset_id}/{layout_id}"


def test_presets_are_distinct_and_brief_is_not_mutated_or_silently_trimmed():
    assert {preset.id for preset in PRESETS} == {"clean_studio", "premium_minimal", "vivid_pop"}
    source = brief(brand_colors=["#ABCDEF"], creative_direction="  Strong edge lighting.\nKeep the cap visible.  ")
    before = source.model_dump()
    compiled = build_cosmetics_prompt(source)
    assert source.model_dump() == before
    assert source.product_description in compiled.positive
    assert source.creative_direction in compiled.positive
    assert "only where compatible with product preservation" in compiled.positive
    assert len({build_cosmetics_prompt(brief(preset_id=item.id)).positive for item in PRESETS}) == 3
    assert compiled == build_cosmetics_prompt(CosmeticsPromptBrief.model_validate(before))


@pytest.mark.parametrize("preset_id", ["clean_studio", "premium_minimal", "vivid_pop"])
def test_explicit_team_flower_direction_is_retained_without_a_flower_ban(preset_id):
    direction = "Place one white flower beside the bottle, outside the reserved copy area."
    source = brief(preset_id=preset_id, creative_direction=direction)
    request = build_cosmetics_request(source, product_images=[asset()])
    text = OpenAIImagesAdapter._prompt(request)
    assert direction in request.prompt.positive and direction in text
    assert not re.search(r"\b(?:flowers?|floral|botanical|petals?)\b", request.prompt.negative, re.IGNORECASE)
    assert "Do not add flowers" not in text and "Do not copy their products, text, logos, flowers" not in text
    assert "Preserve existing product label and logo text" in text
    assert "ingredients, efficacy claims, certification marks" in text


@pytest.mark.parametrize("change", [
    {"product_description": ""}, {"product_description": " \n\t"},
    {"product_description": "x" * 601}, {"product_description": "private\x00text"},
    {"product_description": "bad\ud800"}, {"creative_direction": "x" * 801},
    {"creative_direction": "bad\x01"}, {"preset_id": "unregistered_preset"}, {"layout_id": "fill_frame"},
    {"brand_colors": ["red"]}, {"brand_colors": ["#ABC"]}, {"brand_colors": ["#ABCDEG"]},
    {"brand_colors": ["#ABCDEF"] * 4}, {"source_revision": True}, {"source_revision": 0},
    {"source_revision": "1"}, {"language": "ko"}, {"unknown": "field"},
])
def test_brief_rejects_invalid_options_instead_of_silently_repairing_them(change):
    body = {"product_description": "Verified cosmetic bottle.", "source_revision": 1, **change}
    with pytest.raises(ValidationError):
        CosmeticsPromptBrief.model_validate(body)


@pytest.mark.parametrize("make_invalid", [
    lambda: brief().model_copy(update={"preset_id": "unregistered_preset"}),
    lambda: brief().model_copy(update={"source_revision": True}),
    lambda: brief().model_copy(update={"product_description": " "}),
    lambda: brief().model_copy(update={"brand_colors": ["unsafe"]}),
    lambda: brief().model_copy(update={"unreviewed_field": "hidden"}),
    lambda: CosmeticsPromptBrief.model_construct(product_description="A bottle", source_revision=0),
    lambda: {"product_description": "A bottle", "source_revision": 1},
])
def test_compiler_revalidates_constructed_and_copied_model_instances(make_invalid):
    with pytest.raises(ValueError, match="^Invalid cosmetics prompt brief$"):
        build_cosmetics_prompt(make_invalid())


def test_maximum_valid_brief_is_kept_within_existing_image_prompt_contract():
    source = CosmeticsPromptBrief(product_description="x" * 600, creative_direction="y" * 800,
                                   brand_colors=["#000000", "#FFFFFF", "#12ab34"], source_revision=1)
    prompt = build_cosmetics_prompt(source)
    assert source.product_description in prompt.positive and source.creative_direction in prompt.positive
    assert len(prompt.positive) <= 4000 and len(prompt.negative) <= 2000
    assert len(prompt.prompt_version) <= 80


@pytest.mark.parametrize("change", [
    {"product_description": "A different verified bottle."}, {"preset_id": "vivid_pop"},
    {"layout_id": "product_left"}, {"brand_colors": ["#112233"]},
    {"creative_direction": "Soft edge lighting."}, {"source_revision": 5},
])
def test_every_effective_brief_change_invalidates_request_hash(change):
    source = brief()
    baseline = build_cosmetics_request(source, product_images=[asset()])
    changed = build_cosmetics_request(CosmeticsPromptBrief.model_validate({**source.model_dump(), **change}),
                                      product_images=[asset()])
    assert content_hash(baseline) != content_hash(changed)
    assert content_hash(baseline) == content_hash(ExternalImageRequest.model_validate_json(baseline.model_dump_json()))


# Frozen from the pre-cosmetics (139db55) contract and renderer. The constant was
# independently calculated from this literal body with standard-library JSON/SHA256.
LEGACY_BODY = {
    "kind": "api_image", "operation": "edit", "pipeline_profile_id": "openai-images",
    "pipeline_revision": "1", "model": "gpt-image-2.5-sunburst-2026-09-08",
    "product_images": [{"asset_id": "00000000-0000-0000-0000-000000000001", "sha256": "a" * 64,
                        "width": 16, "height": 16, "mime_type": "image/png"}],
    "reference_images": [{"asset_id": "00000000-0000-0000-0000-000000000002", "sha256": "b" * 64,
                          "width": 16, "height": 16, "mime_type": "image/png"}],
    "prompt": {"positive": "Legacy cosmetics studio.", "negative": "extra products",
               "prompt_version": "legacy-v1", "source_revision": 3, "language": "en"},
    "width": 1152, "height": 1440, "quality": "medium",
}
LEGACY_HASH = "0d05ae8131f501ef213e54bcd2a7d4e81b0e2bb7565d784346039829d0c901d3"
LEGACY_PROMPT = (
    "Legacy cosmetics studio.\n\nAvoid: extra products"
    "\n\nReference roles: images 1 through 1 show the same product from different views. Preserve its identity."
    "\nBackground references: images 2 through 2 describe the background and composition, not additional products."
)
LEGACY_CUTOUT = (
    "Remove only the background from the provided product photo and return a "
    "transparent PNG. Keep the same product view, shape, color, label, logo and "
    "small printed details. Do not add, duplicate or redesign any product part. "
    "Do not add text, props, a background, or a new cast shadow."
)


def test_v1_wire_body_golden_hash_and_literal_prompts_remain_unchanged():
    request = ExternalImageRequest.model_validate(deepcopy(LEGACY_BODY))
    assert request.model_dump(mode="json") == LEGACY_BODY
    assert content_hash(request) == content_hash(LEGACY_BODY) == LEGACY_HASH
    assert OpenAIImagesAdapter._prompt(request) == LEGACY_PROMPT
    without_explicit_revision = {key: value for key, value in LEGACY_BODY.items() if key != "pipeline_revision"}
    assert content_hash(ExternalImageRequest.model_validate(without_explicit_revision)) == LEGACY_HASH
    cutout = ExternalImageRequest(operation="cutout", product_images=[asset()])
    assert OpenAIImagesAdapter._prompt(cutout) == LEGACY_CUTOUT
    revised = ExternalImageRequest.model_validate({**LEGACY_BODY, "pipeline_revision": "2"})
    assert content_hash(revised) != LEGACY_HASH
    assert OpenAIImagesAdapter._prompt(revised) != LEGACY_PROMPT


@pytest.mark.parametrize("operation", ["generate", "edit"])
def test_v2_advertising_canvas_must_match_prompt_portrait_layout(operation):
    request = build_cosmetics_request(brief(), product_images=[] if operation == "generate" else [asset()],
                                      operation=operation)
    with pytest.raises(ValidationError, match="4:5"):
        ExternalImageRequest.model_validate({**request.model_dump(), "width": 1024, "height": 1024})
    # Prior profile and product cutouts retain their independent supported canvases.
    ExternalImageRequest.model_validate({**request.model_dump(), "pipeline_revision": "1", "width": 1024, "height": 1024})
    cutout = build_cosmetics_cutout_request(asset())
    ExternalImageRequest.model_validate({**cutout.model_dump(), "width": 1024, "height": 1024})


def test_default_edit_requires_a_real_product_and_concept_generation_forbids_product_assets():
    with pytest.raises(ValidationError):
        build_cosmetics_request(brief(), product_images=[])
    with pytest.raises(ValidationError):
        build_cosmetics_request(brief(), product_images=[asset()], operation="generate")
    with pytest.raises(ValueError):
        build_cosmetics_request(brief(), product_images=[asset()], operation="cutout")


def test_v2_generate_uses_json_concept_policy_and_never_reads_live_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-sentinel-key")
    monkeypatch.setenv("BEAUTY_OPENAI_IMAGE_LIVE_CALLS", "true")
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: pytest.fail("No real transport"))
    calls = []
    def handle(wire):
        calls.append(wire)
        assert str(wire.url) == "https://api.openai.com/v1/images/generations"
        assert wire.headers["authorization"] == "Bearer offline-test"
        body = json.loads(wire.content)
        assert set(body) == {"model", "prompt", "n", "size", "quality", "output_format"}
        assert body["n"] == 1 and body["size"] == "1152x1440" and body["quality"] == "low"
        assert "CONCEPT ONLY" in body["prompt"] and "No real product photo was supplied" in body["prompt"]
        assert "REQUIRED COSMETICS CONSTRAINTS" in body["prompt"]
        assert "private-sentinel" not in wire.content.decode()
        return response()
    request = build_cosmetics_request(brief(preset_id="vivid_pop"), product_images=[], operation="generate", quality="low")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle))
    result = adapter.execute(request, [], [], client_request_id=uuid4())
    assert result.image_bytes == png() and result.usage is None and len(calls) == 1


@pytest.mark.parametrize("operation,product_count,reference_count", [("edit", 2, 2), ("generate", 0, 2), ("cutout", 1, 0)])
def test_v2_multipart_keeps_product_order_reference_roles_and_fixed_cutout_policy(operation, product_count, reference_count):
    images = [png(color=(index * 30, 20, 50, 255)) for index in range(product_count + reference_count)]
    refs = [asset(data, number=index + 1) for index, data in enumerate(images)]
    products, references = refs[:product_count], refs[product_count:]
    request = (build_cosmetics_cutout_request(products[0]) if operation == "cutout" else
               build_cosmetics_request(brief(), product_images=products, reference_images=references, operation=operation))
    calls = []
    def handle(wire):
        calls.append(wire)
        assert str(wire.url) == "https://api.openai.com/v1/images/edits"
        parts = multipart(wire)
        files = [(value, filename) for name, value, filename in parts if name == "image[]"]
        assert [value for value, _ in files] == images
        assert [name for _, name in files] == [f"image-{i + 1}.png" for i in range(len(images))]
        fields = {name: value.decode() for name, value, filename in parts if filename is None}
        assert fields["n"] == "1" and fields["output_format"] == "png" and fields["size"] == "1152x1440"
        text = fields["prompt"]
        if operation == "cutout":
            assert fields["background"] == "transparent"
            assert "halos, fringing or invented holes" in text
            assert "Do not repaint, polish, retouch or redesign" in text
            assert "packaging artwork" in text
            assert "REQUIRED COSMETICS CONSTRAINTS" not in text
        else:
            assert "background" not in fields
            assert f"Images {product_count + 1} through {len(images)}" in text
            assert "Do not copy their products, text or logos." in text
            if operation == "edit":
                assert "Image 1 is the primary product and viewpoint" in text
                assert "up to image 2" in text and "Show exactly one product" in text
            else:
                assert "CONCEPT ONLY" in text
        return response()
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle))
    adapter.execute(request, images[:product_count], images[product_count:], client_request_id=uuid4())
    assert len(calls) == 1


def test_forged_v2_canvas_is_rejected_before_any_transport_attempt():
    request = build_cosmetics_request(brief(), product_images=[], operation="generate")
    forged = request.model_copy(update={"width": 1024, "height": 1024})
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda _: pytest.fail("No send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        adapter.execute(forged, [], [], client_request_id=uuid4())
    with pytest.raises(ValueError, match="pipeline revision 2"):
        render_cosmetics_provider_prompt(ExternalImageRequest.model_validate(LEGACY_BODY))


@pytest.mark.parametrize("operation", ["generate", "edit", "cutout"])
def test_valid_mock_output_remains_unapproved_and_has_no_actual_cost(tmp_path, operation):
    assets = LocalAssetStore(tmp_path / "assets")
    attempts = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    calls = []
    def handle(wire):
        calls.append(wire)
        return response(png(size=(1152, 1440), transparent=operation == "cutout"))
    processor = ExternalImageProcessor(assets, OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle)),
                                        attempts, budget_limit_usd="3")
    project_id, job_id = uuid4(), uuid4()
    product = assets.put_upload(png(), project_id=project_id)
    request = (build_cosmetics_cutout_request(product) if operation == "cutout" else
               build_cosmetics_request(brief(), product_images=[] if operation == "generate" else [product], operation=operation))
    result = processor.process_job(request, project_id, job_id)
    assert result.needs_manual_check is True
    expected_warning = "GENERATIVE_CUTOUT_REQUIRES_REVIEW" if operation == "cutout" else "GENERATED_IMAGE_REQUIRES_REVIEW"
    assert expected_warning in result.warnings and result.usage is None
    assert attempts.get(job_id, project_id)["actual_cost_usd"] is None
    assert processor.process_job(request, project_id, job_id) == result and len(calls) == 1


def test_v2_cutout_rejects_opaque_provider_output_despite_strict_prompt(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    attempts = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda _: response(png(size=(1152, 1440)))))
    processor = ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd="3")
    project_id, job_id = uuid4(), uuid4()
    request = build_cosmetics_cutout_request(assets.put_upload(png(), project_id=project_id))
    with pytest.raises(GatewayError) as error:
        processor.process_job(request, project_id, job_id)
    assert error.value.code == "PROVIDER_OUTPUT_INVALID"
    receipt = attempts.get(job_id, project_id)
    assert receipt["state"] == "failed" and receipt["result"] is None
