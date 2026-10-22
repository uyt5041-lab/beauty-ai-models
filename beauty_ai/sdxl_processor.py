"""Fixed-profile, local SDXL inpainting with explicit product restoration.

The generated scene may still contain product pixels. Neither this adapter nor
the base SDXL pipeline provides an NSFW checker or human quality acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import threading
from typing import Any
from uuid import UUID

from PIL import Image, ImageChops
from pydantic import ValidationError

from .assets import LocalAssetStore
from .contracts import BackgroundRequest, BackgroundResult, ImageRequest, Rect
from .errors import GatewayError
from .image_integrity import composite_approved_foreground
from .image_preparation import inspect_composite
from .product_approval import ProductApprovalStore


PROFILE_ID = "sdxl-inpaint"
PIPELINE_REVISION = "sdxl-base-fp16-v1"
NEUTRAL_RGB = (250, 250, 250)
_ALLOWED_DIMENSIONS = frozenset((512, 768, 1024))


@dataclass(frozen=True)
class _Dependencies:
    torch: Any
    numpy: Any
    pipeline: Any
    scheduler: Any


def _load_dependencies() -> _Dependencies:
    """A private bounded-test seam, never a configurable alternate model path."""
    import numpy
    import torch
    from diffusers import EulerDiscreteScheduler, StableDiffusionXLInpaintPipeline

    return _Dependencies(torch, numpy, StableDiffusionXLInpaintPipeline, EulerDiscreteScheduler)


def _verify_model_directory(path: Path) -> Path:
    from .sdxl_model_store import verify_model_directory

    return verify_model_directory(path)


def _safe_error(code: str) -> GatewayError:
    return GatewayError(code, code)


def _operation_error(exc: Exception, default: str) -> GatewayError:
    if isinstance(exc, MemoryError) or type(exc).__name__ == "OutOfMemoryError":
        return _safe_error("IMAGE_OUT_OF_MEMORY")
    if isinstance(exc, RuntimeError):
        try:
            if "out of memory" in str(exc).lower():
                return _safe_error("IMAGE_OUT_OF_MEMORY")
        except Exception:
            pass
    return _safe_error(default)


def _safe_bounds(rect: Rect, size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (max(0, math.floor(rect.x * width)), max(0, math.floor(rect.y * height)),
            min(width, math.ceil((rect.x + rect.width) * width)),
            min(height, math.ceil((rect.y + rect.height) * height)))


class SDXLBackgroundProcessor:
    def __init__(self, assets: LocalAssetStore, approvals: ProductApprovalStore,
                 model_directory: Path, *, device: str = "mps"):
        if device not in ("cpu", "mps", "cuda"):
            raise _safe_error("MODEL_UNAVAILABLE")
        self.assets, self.approvals = assets, approvals
        self.model_directory, self.device = Path(model_directory), device
        self._pipeline = None
        self._dependencies = None
        self._execution_lock = threading.Lock()

    @staticmethod
    def _validated(request: ImageRequest) -> BackgroundRequest:
        if not isinstance(request, BackgroundRequest):
            raise _safe_error("INVALID_IMAGE_REQUEST")
        try:
            validated = BackgroundRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise _safe_error("INVALID_IMAGE_REQUEST") from None
        if (validated.pipeline_profile_id != PROFILE_ID
                or validated.pipeline_revision != PIPELINE_REVISION):
            raise _safe_error("IMAGE_PROFILE_NOT_FOUND")
        visual = validated.visual
        if (validated.image_prompt is None
                or visual.generation_width not in _ALLOWED_DIMENSIONS
                or visual.generation_height not in _ALLOWED_DIMENSIONS
                or (visual.style_preset_id, visual.style_preset_version) != ("bright-ad", "1")
                or (visual.layout_template_id, visual.layout_template_version) != ("product-right", "1")):
            raise _safe_error("INVALID_IMAGE_REQUEST")
        return validated

    def validate_request(self, request: ImageRequest) -> None:
        """Validate fixed profile/shape before queuing; tokenization is local at execution."""
        self._validated(request)

    def _check_device(self, deps: _Dependencies) -> None:
        try:
            if self.device == "cuda" and not deps.torch.cuda.is_available():
                raise _safe_error("MODEL_UNAVAILABLE")
            if self.device == "mps" and (
                not deps.torch.backends.mps.is_available()
                or os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0") != "0"
            ):
                raise _safe_error("MODEL_UNAVAILABLE")
        except GatewayError:
            raise
        except Exception:
            raise _safe_error("MODEL_UNAVAILABLE") from None

    def _synchronize(self, deps: _Dependencies) -> None:
        try:
            if self.device == "mps":
                deps.torch.mps.synchronize()
            elif self.device == "cuda":
                deps.torch.cuda.synchronize()
        except Exception as exc:
            raise _operation_error(exc, "IMAGE_PROCESSING_FAILED") from None

    def _ensure_loaded(self) -> None:
        if self._pipeline is not None:
            return
        verified = _verify_model_directory(self.model_directory)
        try:
            deps = _load_dependencies()
        except Exception:
            raise _safe_error("MODEL_UNAVAILABLE") from None
        self._check_device(deps)
        try:
            try:
                pipeline = deps.pipeline.from_pretrained(
                    str(verified), local_files_only=True, use_safetensors=True, variant="fp16",
                    dtype=deps.torch.float32 if self.device == "cpu" else deps.torch.float16,
                    add_watermarker=False,
                )
                pipeline.scheduler = deps.scheduler.from_pretrained(
                    str(verified), subfolder="scheduler", local_files_only=True,
                )
                pipeline.vae.register_to_config(force_upcast=True)
                pipeline.vae.enable_tiling()
                pipeline.set_progress_bar_config(disable=True)
                pipeline = pipeline.to(self.device)
            finally:
                self._synchronize(deps)
        except GatewayError:
            raise
        except Exception as exc:
            raise _operation_error(exc, "MODEL_UNAVAILABLE") from None
        self._dependencies, self._pipeline = deps, pipeline

    def _validate_prompt(self, request: BackgroundRequest) -> None:
        # Diffusers normally truncates and logs discarded prompt text. Reject
        # before calling it, suppress tokenizer length warnings too, and check
        # both encoders and both strings including their start/end tokens.
        try:
            for tokenizer in (self._pipeline.tokenizer, self._pipeline.tokenizer_2):
                if tokenizer.model_max_length != 77:
                    raise _safe_error("MODEL_UNAVAILABLE")
                for text in (request.image_prompt.positive, request.image_prompt.negative):
                    encoded = tokenizer(
                        text, add_special_tokens=True, truncation=False, padding=False,
                        return_attention_mask=False, verbose=False,
                    )
                    tokens = encoded["input_ids"]
                    if not isinstance(tokens, (list, tuple)) or not 1 <= len(tokens) <= 77:
                        raise _safe_error("INVALID_IMAGE_REQUEST")
        except GatewayError:
            raise
        except Exception:
            raise _safe_error("INVALID_IMAGE_REQUEST") from None

    def _generate(self, request: BackgroundRequest, initial: Image.Image,
                  inpaint_mask: Image.Image) -> Image.Image:
        deps = self._dependencies
        try:
            try:
                self._validate_prompt(request)
                with deps.torch.inference_mode():
                    generator = deps.torch.Generator(device="cpu").manual_seed(request.seed)
                    output = self._pipeline(
                        prompt=request.image_prompt.positive,
                        negative_prompt=request.image_prompt.negative,
                        image=initial, mask_image=inpaint_mask,
                        height=initial.height, width=initial.width,
                        num_inference_steps=20, guidance_scale=7.5, strength=0.99,
                        num_images_per_prompt=1, generator=generator, output_type="np",
                        return_dict=True,
                    )
                    pixels = getattr(output, "images", None)
                    np = deps.numpy
                    if (not isinstance(pixels, np.ndarray)
                            or pixels.shape != (1, initial.height, initial.width, 3)
                            or not np.issubdtype(pixels.dtype, np.floating)
                            or not np.isfinite(pixels).all()
                            or pixels.min() < 0 or pixels.max() > 1
                            or pixels.max() == 0):
                        raise _safe_error("IMAGE_RESULT_INVALID")
                    raw = np.rint(pixels[0] * 255).astype(np.uint8).tobytes()
                    return Image.frombytes("RGB", initial.size, raw)
            finally:
                self._synchronize(deps)
        except GatewayError:
            raise
        except Exception as exc:
            raise _operation_error(exc, "IMAGE_PROCESSING_FAILED") from None

    def __call__(self, request: ImageRequest, project_id: UUID) -> BackgroundResult:
        request = self._validated(request)
        self.approvals.require_approved(request, project_id)
        with self.assets.read(request.product_rgba, project_id=project_id) as foreground:
            with self.assets.read(request.preserve_mask, project_id=project_id) as keep:
                # Recheck the images read for this call before inverting. A gray
                # alpha matte must never be accepted as a binary protection mask.
                try:
                    ProductApprovalStore._validate_layers(foreground, keep, request.visual)
                except ValueError:
                    raise _safe_error("INVALID_IMAGE_REQUEST") from None
                inpaint_mask = ImageChops.invert(keep.convert("L"))
            safe = _safe_bounds(request.visual.text_safe_zone, foreground.size)
            inpaint_mask.paste(0, safe)
            neutral = Image.new("RGB", foreground.size, NEUTRAL_RGB)
            initial = composite_approved_foreground(neutral, foreground).convert("RGB")
            neutral.close()
            try:
                with self._execution_lock:
                    self._ensure_loaded()
                    scene = self._generate(request, initial, inpaint_mask)
            finally:
                initial.close()
                inpaint_mask.close()
            try:
                # Masked latent reconstruction can still alter pixels. Keep the
                # reserved rectangle exactly neutral after decoding as well.
                scene.paste(NEUTRAL_RGB, safe)
                preview = composite_approved_foreground(scene, foreground)
                try:
                    integrity = inspect_composite(preview, foreground)
                    if integrity.mismatched_pixels:
                        raise _safe_error("IMAGE_QUALITY_REJECTED")
                    scene_ref = self.assets.put_image(scene, project_id=project_id)
                    preview_ref = self.assets.put_image(preview, project_id=project_id)
                finally:
                    preview.close()
            finally:
                scene.close()
        warnings = ["MANUAL_QUALITY_REVIEW_REQUIRED", "GENERATION_SAFETY_NOT_ASSESSED",
                    "GENERATED_SCENE_NOT_CLEAN_PLATE", "PARTIAL_ALPHA_REQUIRES_VISUAL_REVIEW"]
        if not integrity.opaque_pixels:
            warnings.append("NO_OPAQUE_PIXELS_TO_VERIFY")
        return BackgroundResult(scene_underlay=scene_ref, product_rgba=request.product_rgba,
                                composed_preview=preview_ref, warnings=warnings)
