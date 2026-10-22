"""Explicit local templates for integration; no segmentation or generative model.

The uploaded-alpha profile only repackages an already transparent product image.
The bright-template profile makes a deterministic decorative background after a
stored approval. These profiles make the file/job flow runnable without silently
substituting synthetic output for a requested AI model.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from PIL import Image, ImageChops, ImageDraw, ImageOps
from pydantic import TypeAdapter, ValidationError

from .assets import LocalAssetStore
from .contracts import (
    BackgroundRequest, BackgroundResult, CutoutRequest, CutoutResult,
    ImageRequest, ImageResult, Rect,
)
from .errors import GatewayError
from .image_integrity import composite_approved_foreground
from .image_preparation import inspect_composite
from .product_approval import ProductApprovalStore


UPLOADED_ALPHA_PROFILE_ID = "uploaded-alpha"
BRIGHT_TEMPLATE_PROFILE_ID = "bright-template"
PIPELINE_REVISION = "1"
STYLE_PRESET_ID = "bright-ad"
STYLE_PRESET_VERSION = "1"
LAYOUT_TEMPLATE_ID = "product-right"
LAYOUT_TEMPLATE_VERSION = "1"


@dataclass(frozen=True)
class PipelineProfile:
    kind: Literal["cutout", "render_background"]
    revision: str
    max_dimension: int
    style_preset_id: str | None = None
    style_preset_version: str | None = None
    layout_template_id: str | None = None
    layout_template_version: str | None = None


PROFILES = MappingProxyType({
    UPLOADED_ALPHA_PROFILE_ID: PipelineProfile("cutout", PIPELINE_REVISION, 16384),
    BRIGHT_TEMPLATE_PROFILE_ID: PipelineProfile(
        "render_background", PIPELINE_REVISION, 1024,
        STYLE_PRESET_ID, STYLE_PRESET_VERSION, LAYOUT_TEMPLATE_ID, LAYOUT_TEMPLATE_VERSION),
})
_REQUEST_ADAPTER = TypeAdapter(ImageRequest)


class LocalTemplateProcessor:
    """Synchronous, bounded local processor with server-owned immutable profiles."""

    def __init__(self, assets: LocalAssetStore, approvals: ProductApprovalStore):
        self.assets = assets
        self.approvals = approvals

    @staticmethod
    def _invalid() -> GatewayError:
        return GatewayError("INVALID_IMAGE_REQUEST", "Image request is unsupported by this profile.")

    @classmethod
    def _validated(cls, request: ImageRequest) -> ImageRequest:
        if not isinstance(request, (CutoutRequest, BackgroundRequest)):
            raise cls._invalid()
        try:
            request = _REQUEST_ADAPTER.validate_python(request.model_dump())
        except (ValidationError, ValueError, TypeError) as exc:
            raise cls._invalid() from exc
        profile = PROFILES.get(request.pipeline_profile_id)
        if profile is None or profile.revision != request.pipeline_revision:
            raise GatewayError("IMAGE_PROFILE_NOT_FOUND", "Image pipeline profile was not found.")
        if request.kind != profile.kind:
            raise cls._invalid()
        if isinstance(request, BackgroundRequest):
            visual = request.visual
            if (visual.generation_width > profile.max_dimension
                    or visual.generation_height > profile.max_dimension
                    or visual.style_preset_id != profile.style_preset_id
                    or visual.style_preset_version != profile.style_preset_version
                    or visual.layout_template_id != profile.layout_template_id
                    or visual.layout_template_version != profile.layout_template_version):
                raise cls._invalid()
        return request

    def validate_request(self, request: ImageRequest) -> None:
        """Check profile/shape before queuing; approval still requires project context."""
        self._validated(request)

    def __call__(self, request: ImageRequest, project_id: UUID) -> ImageResult:
        request = self._validated(request)
        if isinstance(request, CutoutRequest):
            return self._uploaded_alpha(request, project_id)
        return self._template_background(request, project_id)

    def _uploaded_alpha(self, request: CutoutRequest, project_id: UUID) -> CutoutResult:
        with self.assets.read(request.source, project_id=project_id) as source:
            if source.mode != "RGBA":
                raise GatewayError("MODEL_UNAVAILABLE", "A transparent RGBA upload is required; no cutout model is configured.")
            alpha = source.getchannel("A")
            minimum, maximum = alpha.getextrema()
            if maximum == 0:
                raise self._invalid()
            if minimum == 255:
                raise GatewayError("MODEL_UNAVAILABLE", "An opaque upload requires a cutout model; none is configured.")
            product = self.assets.put_image(source, project_id=project_id)
            keep = alpha.point(lambda value: 255 if value > 0 else 0)
            preserve_mask = self.assets.put_image(keep, project_id=project_id)
        return CutoutResult(
            product_rgba=product, preserve_mask=preserve_mask, needs_manual_check=True,
            warnings=["UPLOADED_ALPHA_NOT_MODEL", "MANUAL_QUALITY_REVIEW_REQUIRED"],
        )

    @staticmethod
    def _outside_bounds(rect: Rect, size: tuple[int, int]) -> tuple[int, int, int, int]:
        width, height = size
        return (
            max(0, math.floor(rect.x * width)),
            max(0, math.floor(rect.y * height)),
            min(width, math.ceil((rect.x + rect.width) * width)),
            min(height, math.ceil((rect.y + rect.height) * height)),
        )

    @classmethod
    def _render_template(cls, request: BackgroundRequest, keep: Image.Image) -> Image.Image:
        size = (request.visual.generation_width, request.visual.generation_height)
        rng = random.Random(request.seed)
        palettes = (
            ("#fff4b0", "#ffd2ed", "#ff3094", "#24e5de"),
            ("#efffc2", "#c9faff", "#ff6099", "#8c5cff"),
            ("#ffe3f4", "#fff6b0", "#5a58ff", "#00d7c6"),
        )
        top, bottom, accent, secondary = palettes[rng.randrange(len(palettes))]
        gradient = Image.linear_gradient("L").resize(size, Image.Resampling.BILINEAR)
        base = ImageOps.colorize(gradient, top, bottom).convert("RGBA")
        decoration = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(decoration)
        width, height = size
        for number in range(12):
            radius = rng.randint(max(3, min(size) // 32), max(4, min(size) // 8))
            x, y = rng.randrange(width), rng.randrange(height)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                         fill=accent if number % 2 else secondary)
        allowed = ImageChops.invert(keep.convert("L"))
        for rect in (request.visual.product_box, request.visual.text_safe_zone):
            # Pillow paste boxes are half-open, matching the normalized contract.
            allowed.paste(0, cls._outside_bounds(rect, size))
        decoration.putalpha(ImageChops.multiply(decoration.getchannel("A"), allowed))
        return Image.alpha_composite(base, decoration).convert("RGB")

    def _template_background(self, request: BackgroundRequest, project_id: UUID) -> BackgroundResult:
        # This checks the exact source/product/mask/visual snapshot and its files,
        # not merely an approval boolean supplied in the request.
        self.approvals.require_approved(request, project_id)
        with self.assets.read(request.product_rgba, project_id=project_id) as foreground:
            with self.assets.read(request.preserve_mask, project_id=project_id) as keep:
                scene = self._render_template(request, keep)
            composed = composite_approved_foreground(scene, foreground)
            report = inspect_composite(composed, foreground)
            if report.mismatched_pixels:
                raise GatewayError("IMAGE_QUALITY_REJECTED", "Opaque product pixels changed during compositing.")
            scene_ref = self.assets.put_image(scene, project_id=project_id)
            preview_ref = self.assets.put_image(composed, project_id=project_id)
        warnings = ["TEMPLATE_NOT_AI_GENERATION", "MANUAL_QUALITY_REVIEW_REQUIRED"]
        if report.partial_alpha_pixels:
            warnings.append("PARTIAL_ALPHA_REQUIRES_VISUAL_REVIEW")
        if not report.opaque_pixels:
            warnings.append("NO_OPAQUE_PIXELS_TO_VERIFY")
        return BackgroundResult(
            scene_underlay=scene_ref, product_rgba=request.product_rgba,
            composed_preview=preview_ref, warnings=warnings,
        )
