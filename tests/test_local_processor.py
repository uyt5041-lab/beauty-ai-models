from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundRequest, CutoutRequest, Rect, VisualBrief
from beauty_ai.errors import GatewayError
from beauty_ai.image_integrity import composite_approved_foreground
from beauty_ai.image_preparation import inspect_composite
from beauty_ai.local_processor import (
    BRIGHT_TEMPLATE_PROFILE_ID, LAYOUT_TEMPLATE_ID, LAYOUT_TEMPLATE_VERSION,
    PIPELINE_REVISION, PROFILES, STYLE_PRESET_ID, STYLE_PRESET_VERSION,
    UPLOADED_ALPHA_PROFILE_ID, LocalTemplateProcessor,
)
from beauty_ai.product_approval import ProductApprovalStore


def visual(**changes):
    values = dict(
        concept="bright local template", style_preset_id=STYLE_PRESET_ID,
        style_preset_version=STYLE_PRESET_VERSION, layout_template_id=LAYOUT_TEMPLATE_ID,
        layout_template_version=LAYOUT_TEMPLATE_VERSION,
        generation_width=256, generation_height=256,
        product_box=Rect(x=.58, y=.2, width=.32, height=.6),
        text_safe_zone=Rect(x=.05, y=.05, width=.4, height=.6),
    )
    values.update(changes)
    return VisualBrief(**values)


def runtime(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    approvals = ProductApprovalStore(tmp_path / "approvals.sqlite3", assets)
    return assets, approvals, LocalTemplateProcessor(assets, approvals), uuid4()


def rgba_source(assets, project, *, partial=False):
    image = Image.new("RGBA", (40, 80), (0, 0, 0, 0))
    image.paste((31, 101, 177, 255), (10, 10, 30, 70))
    if partial:
        image.paste((31, 101, 177, 100), (9, 10, 10, 70))
    return assets.put_image(image, project_id=project)


def cutout_request(source, **changes):
    values = dict(source=source, pipeline_profile_id=UPLOADED_ALPHA_PROFILE_ID,
                  pipeline_revision=PIPELINE_REVISION)
    values.update(changes)
    return CutoutRequest(**values)


def background_for(assets, approvals, processor, project, *, partial=False, approve=True):
    source = rgba_source(assets, project, partial=partial)
    cutout = processor(cutout_request(source), project)
    candidate = approvals.prepare(project, source, cutout.product_rgba, visual())
    if approve:
        approvals.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    request = BackgroundRequest(
        source=source, product_rgba=candidate.product_rgba, preserve_mask=candidate.preserve_mask,
        visual=candidate.visual, seed=77, pipeline_profile_id=BRIGHT_TEMPLATE_PROFILE_ID,
        pipeline_revision=PIPELINE_REVISION,
    )
    return request, candidate


def test_uploaded_alpha_is_repackaged_without_claiming_segmentation(tmp_path):
    assets, _, processor, project = runtime(tmp_path)
    source = rgba_source(assets, project, partial=True)
    request = cutout_request(source)
    assert processor.validate_request(request) is None
    result = processor(request, project)
    assert result.needs_manual_check
    assert "UPLOADED_ALPHA_NOT_MODEL" in result.warnings
    assert "MANUAL_QUALITY_REVIEW_REQUIRED" in result.warnings
    assert result.product_rgba.asset_id != source.asset_id
    assert result.product_rgba.sha256 == source.sha256
    with assets.read(result.product_rgba, project_id=project) as product:
        assert product.mode == "RGBA"
        with assets.read(result.preserve_mask, project_id=project) as mask:
            expected = product.getchannel("A").point(lambda value: 255 if value else 0)
            assert mask.convert("L").tobytes() == expected.tobytes()


@pytest.mark.parametrize("image", [
    Image.new("RGB", (16, 16), "white"),
    Image.new("RGBA", (16, 16), (20, 40, 60, 255)),
])
def test_opaque_upload_requires_a_real_cutout_model(tmp_path, image):
    assets, _, processor, project = runtime(tmp_path)
    source = assets.put_image(image, project_id=project)
    with pytest.raises(GatewayError) as caught:
        processor(cutout_request(source), project)
    assert caught.value.code == "MODEL_UNAVAILABLE"


def test_empty_alpha_is_invalid_and_foreign_assets_are_not_read(tmp_path):
    assets, _, processor, project = runtime(tmp_path)
    empty = assets.put_image(Image.new("RGBA", (8, 8)), project_id=project)
    with pytest.raises(GatewayError) as caught:
        processor(cutout_request(empty), project)
    assert caught.value.code == "INVALID_IMAGE_REQUEST"
    source = rgba_source(assets, project)
    with pytest.raises(GatewayError) as caught:
        processor(cutout_request(source), uuid4())
    assert caught.value.code == "ASSET_NOT_FOUND"


@pytest.mark.parametrize("changes,code", [
    ({"pipeline_profile_id": "arbitrary/path/model"}, "IMAGE_PROFILE_NOT_FOUND"),
    ({"pipeline_revision": "unknown"}, "IMAGE_PROFILE_NOT_FOUND"),
    ({"pipeline_profile_id": BRIGHT_TEMPLATE_PROFILE_ID}, "INVALID_IMAGE_REQUEST"),
])
def test_profiles_and_operation_kinds_are_allowlisted(tmp_path, changes, code):
    assets, _, processor, project = runtime(tmp_path)
    source = rgba_source(assets, project)
    with pytest.raises(GatewayError) as caught:
        processor.validate_request(cutout_request(source, **changes))
    assert caught.value.code == code


def test_profile_configuration_is_immutable():
    with pytest.raises(TypeError):
        PROFILES["arbitrary"] = PROFILES[BRIGHT_TEMPLATE_PROFILE_ID]
    with pytest.raises(FrozenInstanceError):
        PROFILES[BRIGHT_TEMPLATE_PROFILE_ID].max_dimension = 99999


@pytest.mark.parametrize("field,value", [
    ("generation_width", 1032),
    ("style_preset_id", "freeform"),
    ("style_preset_version", "2"),
    ("layout_template_id", "unknown"),
    ("layout_template_version", "2"),
])
def test_background_dimensions_and_preset_versions_are_bounded(tmp_path, field, value):
    assets, approvals, processor, project = runtime(tmp_path)
    request, _ = background_for(assets, approvals, processor, project)
    # Keep declared canvas sizes coherent, so the profile-specific limit is tested.
    visual_values = request.visual.model_dump()
    visual_values[field] = value
    altered_visual = VisualBrief(**visual_values)
    changes = {"visual": altered_visual}
    if field == "generation_width":
        changes["product_rgba"] = request.product_rgba.model_copy(update={"width": value})
        changes["preserve_mask"] = request.preserve_mask.model_copy(update={"width": value})
    changed = request.model_copy(update=changes)
    with pytest.raises(GatewayError) as caught:
        processor.validate_request(changed)
    assert caught.value.code == "INVALID_IMAGE_REQUEST"


def test_background_cannot_run_before_approval_or_after_visual_changes(tmp_path):
    assets, approvals, processor, project = runtime(tmp_path)
    request, candidate = background_for(assets, approvals, processor, project, approve=False)
    with pytest.raises(GatewayError):
        processor(request, project)
    approvals.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    changed_visual = request.visual.model_copy(update={"concept": "changed after confirmation"})
    with pytest.raises(GatewayError):
        processor(request.model_copy(update={"visual": changed_visual}), project)


def test_template_saves_actual_layers_preserves_product_and_is_seed_deterministic(tmp_path):
    assets, approvals, processor, project = runtime(tmp_path)
    request, _ = background_for(assets, approvals, processor, project)
    first, second = processor(request, project), processor(request, project)
    assert first.product_rgba == request.product_rgba
    assert first.scene_underlay.asset_id != second.scene_underlay.asset_id
    assert first.scene_underlay.sha256 == second.scene_underlay.sha256
    assert first.composed_preview.sha256 == second.composed_preview.sha256
    assert "TEMPLATE_NOT_AI_GENERATION" in first.warnings
    assert "MANUAL_QUALITY_REVIEW_REQUIRED" in first.warnings
    with assets.read(first.scene_underlay, project_id=project) as scene:
        with assets.read(first.product_rgba, project_id=project) as product:
            with assets.read(first.composed_preview, project_id=project) as preview:
                assert scene.size == product.size == preview.size == (256, 256)
                assert inspect_composite(preview, product).opaque_preserved
                assert preview.tobytes() == composite_approved_foreground(scene, product).tobytes()
                # Within the safe rectangle, the gradient varies only vertically:
                # no decorative marks or copy text have been painted there.
                assert scene.getpixel((20, 50)) == scene.getpixel((90, 50))
                assert scene.getpixel((20, 100)) == scene.getpixel((90, 100))
    different = processor(request.model_copy(update={"seed": 90}), project)
    assert different.scene_underlay.sha256 != first.scene_underlay.sha256


def test_partial_alpha_is_reported_as_needing_visual_review(tmp_path):
    assets, approvals, processor, project = runtime(tmp_path)
    request, _ = background_for(assets, approvals, processor, project, partial=True)
    result = processor(request, project)
    assert "PARTIAL_ALPHA_REQUIRES_VISUAL_REVIEW" in result.warnings
    assert "MANUAL_QUALITY_REVIEW_REQUIRED" in result.warnings


def test_approved_asset_tampering_is_not_accepted_as_a_successful_template(tmp_path):
    assets, approvals, processor, project = runtime(tmp_path)
    request, _ = background_for(assets, approvals, processor, project)
    file = assets.files_dir / project.hex / f"{request.product_rgba.asset_id.hex}.png"
    file.write_bytes(b"corrupt approved image")
    with pytest.raises(GatewayError) as caught:
        processor(request, project)
    # The approval boundary intentionally normalizes invalid stored inputs.
    assert caught.value.code == "INVALID_IMAGE_REQUEST"
