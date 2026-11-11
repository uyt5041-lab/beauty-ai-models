"""Approved product-only 14D generation + separately approved local A export."""
from pathlib import Path

from .a_template import make_a_template
from .adaptive_typography import AdaptiveTypographyOptions
from .api_image_processor import ExternalImageProcessor
from .backend_adapter import BackendImageAdapter
from .composition import ComposedAd, CompositionInput, compose_ad_png
from .errors import GatewayError

PROFILE_VERSION = "ittem-hotfix-20261112-v1"


def build_ittem_image_adapter(processor: ExternalImageProcessor, binding_path: str | Path) -> BackendImageAdapter:
    """Keep caller-supplied credentials/budgets; this function makes no call."""
    return BackendImageAdapter(processor, binding_path, model="gpt-image-2.6-sunburst",
                               quality="medium", complexity="high", scene_mode="dimensions14")


def compose_ittem_ad(body: CompositionInput, *, font_path: str | Path,
                     clearance_px: int = 12, relaxed_clearance_px: int = 4,
                     fallback_lower: bool = True, fallback_shift_px: int = 48) -> ComposedAd:
    """Local recomposition only. Serving still owns image/copy approval.

    Search vertically at normal clearance, then relaxed clearance, then perform
    the explicitly selected lower fallback. Never move horizontally or add a
    rectangle; fallback receipts retain any failed contrast/clearance targets.
    """
    if not isinstance(body, CompositionInput):
        raise ValueError("A validated CompositionInput is required")
    body = CompositionInput.model_validate(body.model_dump(warnings=False))
    expected = make_a_template()[1]
    if (body.layout.canvas_size != expected.canvas_size
            or body.layout.cta_placement != expected.cta_placement
            or set(body.layout.text_regions) != set(expected.text_regions)):
        raise GatewayError("COMPOSITION_HOTFIX_LAYOUT_INVALID", "잇템 핫픽스는 기존 A 합성 배치를 사용합니다.")
    for name, region in body.layout.text_regions.items():
        reference = expected.text_regions[name]
        if (region.left,region.top,region.right,region.bottom,region.align) != (
                reference.left,reference.top,reference.right,reference.bottom,"center"):
            raise GatewayError("COMPOSITION_HOTFIX_LAYOUT_INVALID", "A 좌표와 가로 중앙 정렬을 유지해 주세요.")
    if type(fallback_lower) is not bool:
        raise ValueError("fallback_lower must be a boolean")
    # Validate all options before rendering, even if the first search succeeds.
    normal = AdaptiveTypographyOptions(clearance_px=clearance_px, inset_px=0, step_px=1,
        movement_axis="vertical", fallback_shift_px=fallback_shift_px)
    relaxed = AdaptiveTypographyOptions(clearance_px=relaxed_clearance_px, inset_px=0, step_px=1,
        movement_axis="vertical", fallback_lower=fallback_lower, fallback_shift_px=fallback_shift_px)
    if relaxed_clearance_px > clearance_px:
        raise ValueError("Relaxed clearance must not exceed normal clearance")
    tries = []
    for options in (normal, relaxed):
        tries.append(options.clearance_px)
        try:
            result = compose_ad_png(body, font_path=font_path, adaptive=options)
        except GatewayError as error:
            if error.code != "COMPOSITION_NO_SAFE_POSITION":
                raise
            if options is relaxed:
                raise
            continue
        result.receipt.update(hotfix_profile=PROFILE_VERSION, clearance_search_px=tries,
                              horizontal_center_locked=True, image_generation_calls=0)
        return result
    raise AssertionError("Unreachable composition search state")
