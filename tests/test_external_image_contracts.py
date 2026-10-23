from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from beauty_ai.contracts import (
    AssetRef, ExternalImageRequest, ExternalImageResult, ImagePrompt,
    ImageProviderUsage, ImageRequest, ImageResult, content_hash, image_result_matches,
)


def asset(**changes):
    return AssetRef(asset_id=uuid4(), sha256="a" * 64, width=1152, height=1440,
                    mime_type="image/png").model_copy(update=changes)


def prompt():
    return ImagePrompt(positive="Bright flowers beside the product.", negative="text",
                       prompt_version="test-v1", source_revision=1)


@pytest.mark.parametrize("operation", ["generate", "edit", "cutout"])
def test_operations_round_trip_through_shared_unions(operation):
    request = ExternalImageRequest(operation=operation,
        product_images=[] if operation == "generate" else [asset()],
        prompt=None if operation == "cutout" else prompt())
    result = ExternalImageResult(operation=operation, image=asset(), model=request.model,
        attempt_id=uuid4(), warnings=["GENERATIVE_CUTOUT_REQUIRES_REVIEW"] if operation == "cutout" else [])
    assert TypeAdapter(ImageRequest).validate_json(request.model_dump_json()) == request
    assert TypeAdapter(ImageResult).validate_json(result.model_dump_json()) == result
    assert image_result_matches(request, result)
    assert result.needs_manual_check is True and result.usage is None
    assert "seed" not in request.model_dump()


def test_maximum_inputs_preserve_order_and_snapshot_hash():
    products, references = [asset() for _ in range(5)], [asset() for _ in range(5)]
    request = ExternalImageRequest(operation="edit", product_images=products,
        reference_images=references, prompt=prompt())
    restored = ExternalImageRequest.model_validate_json(request.model_dump_json())
    assert restored.product_images == products and restored.reference_images == references
    assert content_hash(restored) == content_hash(request)
    assert content_hash(request.model_copy(update={"product_images": list(reversed(products))})) != content_hash(request)


@pytest.mark.parametrize("changes", [
    {"seed": 1}, {"url": "https://example.invalid/private.png"}, {"quality": "auto"},
    {"model": "gpt-image-2"}, {"pipeline_revision": "3"}, {"pipeline_profile_id": "local"},
    {"width": 1025}, {"width": True}, {"height": 1440.0},
    {"width": 256, "height": 256}, {"width": 3840, "height": 3840},
    {"width": 3840, "height": 512}, {"width": 4096, "height": 1024},
    {"prompt": None}, {"product_images": [asset()]},
    {"reference_images": [asset() for _ in range(6)]},
])
def test_generation_rejects_unsupported_options_and_invalid_canvas(changes):
    body = {"operation": "generate", "prompt": prompt(), **changes}
    with pytest.raises(ValidationError):
        ExternalImageRequest.model_validate(body)


@pytest.mark.parametrize("body", [
    {"operation": "edit", "prompt": prompt()},
    {"operation": "edit", "product_images": [asset()]},
    {"operation": "edit", "product_images": [asset() for _ in range(6)], "prompt": prompt()},
    {"operation": "cutout"},
    {"operation": "cutout", "product_images": [asset(), asset()]},
    {"operation": "cutout", "product_images": [asset()], "reference_images": [asset()]},
    {"operation": "cutout", "product_images": [asset()], "prompt": prompt()},
])
def test_operation_specific_inputs_are_required(body):
    with pytest.raises(ValidationError):
        ExternalImageRequest.model_validate(body)


def test_duplicate_asset_id_is_rejected_across_input_groups():
    ref = asset()
    with pytest.raises(ValidationError):
        ExternalImageRequest(operation="edit", product_images=[ref],
            reference_images=[ref.model_copy(update={"sha256": "b" * 64})], prompt=prompt())


@pytest.mark.parametrize("changes", [
    {"needs_manual_check": False}, {"provider": "other"},
    {"provider_request_id": "private\nsecret"}, {"provider_request_id": "한글"},
    {"provider_request_id": "x" * 201}, {"warnings": ["raw provider error"]},
    {"warnings": ["A" * 81]}, {"image": asset(mime_type="image/jpeg")},
    {"operation": "cutout", "warnings": []},
])
def test_results_reject_unsafe_metadata_and_missing_review(changes):
    body = dict(operation="generate", image=asset(),
        model="gpt-image-2.5-sunburst-2026-09-08", attempt_id=uuid4(), **{})
    body.update(changes)
    with pytest.raises(ValidationError):
        ExternalImageResult.model_validate(body)


def test_usage_preserves_absence_and_rejects_invented_billing_fields():
    usage = ImageProviderUsage(input_tokens=0, output_tokens=10)
    assert usage.input_tokens == 0 and usage.total_tokens is None
    assert usage.input_image_tokens is None and usage.input_text_tokens is None
    for invalid in ({"input_tokens": -1}, {"output_tokens": True},
                    {"total_tokens": 1.5}, {"cost_usd": 0.02}):
        with pytest.raises(ValidationError):
            ImageProviderUsage.model_validate(invalid)


def test_result_matching_includes_operation_model_and_dimensions():
    request = ExternalImageRequest(operation="generate", prompt=prompt())
    result = ExternalImageResult(operation="generate", image=asset(), model=request.model,
                                 attempt_id=uuid4())
    assert not image_result_matches(request, result.model_copy(update={"operation": "edit"}))
    assert not image_result_matches(request, result.model_copy(update={"model": "gpt-image-2-2026-04-21"}))
    assert not image_result_matches(request, result.model_copy(update={"image": asset(width=1024)}))
