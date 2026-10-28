"""Deterministic cosmetics templates; no LLM, credentials, network or approval.

English presets are historical experiments, not the current team boundary. These instructions request
product fidelity; only review of the returned image can establish its quality.
Published version strings and their text must remain immutable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator

from .contracts import (AssetRef, Contract, ExternalImageRequest, ImagePrompt,
                        ImageProviderModel)
from .prompt_policy import WATERMARK_POLICY_ID, apply_watermark_policy
from .korean_image_policy import render_korean_image_policy

PROMPT_VERSION = "cosmetics-v1"
PIPELINE_REVISION = "2"
PresetId = Literal["clean_studio", "premium_minimal", "vivid_pop"]
LayoutId = Literal["product_right", "product_left", "product_center_bottom"]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]


@dataclass(frozen=True)
class CosmeticsPreset:
    id: PresetId
    title: str
    direction: str


PRESETS = (
    CosmeticsPreset("clean_studio", "클린 스튜디오",
        "A seamless warm-white studio with a matte surface, broad diffused side "
        "light and restrained neutral shadows. Crisp, natural cosmetic packaging detail."),
    CosmeticsPreset("premium_minimal", "고급 미니멀",
        "An uncluttered ivory-to-warm-gray backdrop and a plain matte plinth below "
        "the product. Soft directional key light and a restrained edge highlight. "
        "Quiet, precise premium cosmetics photography with generous breathing room."),
    CosmeticsPreset("vivid_pop", "밝은 컬러 광고",
        "A bright cobalt-blue and coral color-block studio with high-key light, "
        "clean geometric background planes and a controlled contact shadow. "
        "Vivid contemporary cosmetics advertising; the product remains the focal point."),
)

_LAYOUTS = {
    "product_right": "Place the complete product on the right. Reserve the left 35% "
                     "as a quiet, unobstructed background area for later Korean copy.",
    "product_left": "Place the complete product on the left. Reserve the right 35% "
                    "as a quiet, unobstructed background area for later Korean copy.",
    "product_center_bottom": "Place the complete product at the lower center. Reserve "
                             "the upper 30% as a quiet, unobstructed background area "
                             "for later Korean copy.",
}

_PRESERVE = (
    "Keep the supplied cosmetic product's shape, proportions, color, material, "
    "cap or pump, logo, printed label and original packaging artwork intact. "
    "Show one complete product, without cropping small parts. Do not invent "
    "ingredients, efficacy claims, certification marks or packaging details."
)

_NO_ADDITIONS = (
    "Preserve any artwork already printed on the real packaging. "
    "Do not add advertising copy, prices, badges "
    "or watermarks. Preserve existing product label and logo text."
)

_NEGATIVE = (
    "invented ingredients or efficacy claims; "
    "duplicate products; extra caps or pumps; changed product colors; distorted "
    "packaging or label characters; cropped product parts; new advertising text; "
    "objects in the reserved copy area"
)

CUTOUT_PROMPT_V2 = (
    "Remove only the background from the first cosmetic product photograph. "
    "Keep its original viewing angle and all visible parts, including fine pump "
    "edges, transparent or reflective materials, original colors, label characters, "
    "logo and packaging artwork. Fit the whole product without cropping or stretching. "
    "Return a PNG with a genuinely transparent alpha channel and clean, natural "
    "edges: no background residue, halos, fringing or invented holes. Do not repaint, "
    "polish, retouch or redesign the product. Do not draw a checkerboard, solid "
    "background, cast shadow or new props. " + _NO_ADDITIONS
)


class CosmeticsPromptBrief(Contract):
    """Caller-supplied English facts; language and source are not auto-verified."""
    product_description: str = Field(min_length=1, max_length=600)
    preset_id: PresetId = "clean_studio"
    layout_id: LayoutId = "product_right"
    brand_colors: list[HexColor] = Field(default_factory=list, max_length=3)
    creative_direction: str = Field(default="", max_length=800)
    source_revision: int = Field(ge=1, strict=True)
    language: Literal["en"] = "en"

    @field_validator("product_description")
    @classmethod
    def nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Product description must not be blank")
        return value

    @field_validator("product_description", "creative_direction")
    @classmethod
    def valid_text(cls, value: str) -> str:
        value.encode("utf-8")
        if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
            raise ValueError("Unsupported control character")
        return value


def _snapshot(brief: CosmeticsPromptBrief) -> CosmeticsPromptBrief:
    try:
        if (not isinstance(brief, CosmeticsPromptBrief)
                or set(brief.__dict__) - set(CosmeticsPromptBrief.model_fields)):
            raise ValueError
        return CosmeticsPromptBrief.model_validate(brief.model_dump(warnings=False))
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise ValueError("Invalid cosmetics prompt brief") from None


def build_cosmetics_prompt(brief: CosmeticsPromptBrief) -> ImagePrompt:
    """Compile the entire brief without truncation, translation or hidden calls."""
    brief = _snapshot(brief)
    preset = next(item for item in PRESETS if item.id == brief.preset_id)
    parts = ["COSMETICS PRODUCT BRIEF\n" + brief.product_description,
             "PRODUCT PRESERVATION\n" + _PRESERVE,
             "ART DIRECTION\n" + preset.direction]
    if brief.brand_colors:
        parts.append("BACKGROUND PALETTE\nUse " + ", ".join(brief.brand_colors)
                     + " instead of the preset's default background colors. Apply these "
                     "only to the background and scene accents; never recolor the product.")
    if brief.creative_direction:
        parts.append("TEAM CREATIVE DIRECTION\n" + brief.creative_direction
                     + "\nApply this direction only where compatible with product "
                     "preservation and the empty copy space.")
    parts.extend(["COMPOSITION\nPortrait 4:5. " + _LAYOUTS[brief.layout_id],
                  "OUTPUT RULES\n" + _NO_ADDITIONS])
    return ImagePrompt(positive="\n\n".join(parts), negative=_NEGATIVE,
        prompt_version=f"{PROMPT_VERSION}/{brief.preset_id}/{brief.layout_id}",
        source_revision=brief.source_revision)


def build_cosmetics_request(brief: CosmeticsPromptBrief, *,
        product_images: list[AssetRef], reference_images: list[AssetRef] | None = None,
        operation: Literal["edit", "generate"] = "edit",
        model: ImageProviderModel = "gpt-image-2.5-sunburst-2026-09-08",
        quality: Literal["low", "medium", "high"] = "medium") -> ExternalImageRequest:
    """Use edit with uploaded products; generate is only an illustrative concept."""
    if operation not in ("edit", "generate"):
        raise ValueError("Cosmetics prompt requests support edit or generate")
    return ExternalImageRequest(operation=operation, pipeline_revision=PIPELINE_REVISION,
        watermark_policy=WATERMARK_POLICY_ID,
        product_images=product_images, reference_images=reference_images or [],
        prompt=build_cosmetics_prompt(brief), model=model, quality=quality)


def build_cosmetics_cutout_request(product_image: AssetRef, *,
        model: ImageProviderModel = "gpt-image-2.5-sunburst-2026-09-08",
        quality: Literal["low", "medium", "high"] = "medium") -> ExternalImageRequest:
    return ExternalImageRequest(operation="cutout", pipeline_revision=PIPELINE_REVISION,
        watermark_policy=WATERMARK_POLICY_ID,
        product_images=[product_image], model=model, quality=quality)


def render_cosmetics_provider_prompt(request: ExternalImageRequest) -> str:
    """Render revision 2 only; adapter validation occurs before paid reservation."""
    request = ExternalImageRequest.model_validate(request.model_dump(warnings=False))
    if request.pipeline_revision != PIPELINE_REVISION:
        raise ValueError("Cosmetics provider policy requires pipeline revision 2")
    if request.cosmetics_policy_version == "ko-v1":
        return render_korean_image_policy(request)
    if request.operation == "cutout":
        return apply_watermark_policy(CUTOUT_PROMPT_V2, request.watermark_policy)
    assert request.prompt is not None
    parts = [request.prompt.positive]
    if request.prompt.negative:
        parts.append("Avoid: " + request.prompt.negative)
    parts.append("REQUIRED COSMETICS CONSTRAINTS\n" + _PRESERVE + " " + _NO_ADDITIONS
                 + " Keep palette changes in the scene, never on the product. "
                 "Reserve the requested copy area for a separate typography renderer.")
    # Keep reference variables separate from the versioned output policy.
    parts[-1] = apply_watermark_policy(parts[-1], request.watermark_policy)
    products, references = len(request.product_images), len(request.reference_images)
    if products:
        parts.append("PRODUCT REFERENCES\nImage 1 is the primary product and viewpoint. "
                     "Preserve that view and identity; any additional product images "
                     f"up to image {products} show the same product's details, not extra "
                     "objects to include. Show exactly one product.")
    else:
        parts.append("CONCEPT ONLY\nNo real product photo was supplied. Produce an "
                     "unbranded illustrative cosmetics concept, without invented label "
                     "copy or any claim of fidelity to a real product.")
    if references:
        parts.append(f"BACKGROUND REFERENCES\nImages {products + 1} through "
                     f"{products + references} supply background composition and lighting "
                     "only. Do not copy their products, text or logos. "
                     "Product identity and the copy area take priority.")
    return "\n\n".join(parts)
