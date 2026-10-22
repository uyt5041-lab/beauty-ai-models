"""Pinned, local-only BiRefNet cutout execution with explicit device selection.

Heavy dependencies are loaded on the first authorized image call. The manifest
verifier must succeed before Transformers may execute the reviewed model code.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import threading
from typing import Any
from uuid import UUID

from PIL import Image
from pydantic import ValidationError

from .assets import LocalAssetStore
from .contracts import CutoutRequest, CutoutResult, ImageRequest
from .errors import GatewayError
from .execution_trace import device_synchronized, is_recording, phase, record_memory, set_backend


PROFILE_ID = "birefnet-lite"
PIPELINE_REVISION = "aa62cd87eafb9cc43056d08ef3615a14628b831d"
INPUT_SIZE = 1024


@dataclass(frozen=True)
class _Dependencies:
    torch: Any
    transforms: Any
    auto_model: Any
    torchvision_version: str


def _load_dependencies() -> _Dependencies:
    """Private injection seam for tests, never a runtime model-selection setting."""
    import torch
    import torchvision
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation

    return _Dependencies(torch, transforms, AutoModelForImageSegmentation, torchvision.__version__)


def _verify_model_directory(path: Path) -> Path:
    from .model_store import verify_model_directory

    return verify_model_directory(path)


def _safe_error(code: str) -> GatewayError:
    return GatewayError(code, code)


def _operation_error(exc: Exception, default: str) -> GatewayError:
    # Inspect locally only. No backend message, path or tensor is ever returned.
    if isinstance(exc, MemoryError) or type(exc).__name__ == "OutOfMemoryError":
        return _safe_error("IMAGE_OUT_OF_MEMORY")
    if isinstance(exc, RuntimeError):
        try:
            if "out of memory" in str(exc).lower():
                return _safe_error("IMAGE_OUT_OF_MEMORY")
        except Exception:
            pass
    return _safe_error(default)


class BiRefNetCutoutProcessor:
    def __init__(self, assets: LocalAssetStore, model_directory: Path, *, device: str = "mps"):
        if device not in ("cpu", "mps", "cuda"):
            raise _safe_error("MODEL_UNAVAILABLE")
        self.assets = assets
        self.model_directory = Path(model_directory)
        self.device = device
        self._model = None
        self._dependencies = None
        self._preprocess = None
        # A direct caller gets the same one-at-a-time guarantee as the worker.
        # The lock also makes lazy initialization single-shot across threads.
        self._execution_lock = threading.Lock()

    @staticmethod
    def _validated(request: ImageRequest) -> CutoutRequest:
        if not isinstance(request, CutoutRequest):
            raise _safe_error("INVALID_IMAGE_REQUEST")
        try:
            validated = CutoutRequest.model_validate(request.model_dump())
        except (ValidationError, ValueError, TypeError):
            raise _safe_error("INVALID_IMAGE_REQUEST") from None
        if (validated.pipeline_profile_id != PROFILE_ID
                or validated.pipeline_revision != PIPELINE_REVISION):
            raise _safe_error("IMAGE_PROFILE_NOT_FOUND")
        return validated

    def validate_request(self, request: ImageRequest) -> None:
        """Validate the fixed profile; file/project checks happen at execution."""
        self._validated(request)

    def _check_device(self, deps: _Dependencies) -> None:
        try:
            if self.device == "cuda" and not deps.torch.cuda.is_available():
                raise _safe_error("MODEL_UNAVAILABLE")
            if self.device == "mps":
                version = re.match(r"^(\d+)\.(\d+)", deps.torchvision_version)
                if (not deps.torch.backends.mps.is_available()
                        or version is None or tuple(map(int, version.groups())) < (0, 23)
                        or os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0") != "0"):
                    raise _safe_error("MODEL_UNAVAILABLE")
        except GatewayError:
            raise
        except Exception:
            raise _safe_error("MODEL_UNAVAILABLE") from None

    def _synchronize(self, deps: _Dependencies) -> None:
        if self.device == "cpu":
            return
        try:
            if self.device == "mps":
                deps.torch.mps.synchronize()
            elif self.device == "cuda":
                deps.torch.cuda.synchronize()
        except Exception as exc:
            device_synchronized(self.device, success=False)
            raise _operation_error(exc, "IMAGE_PROCESSING_FAILED") from None
        device_synchronized(self.device, success=True)

    def _ensure_loaded(self) -> None:
        set_backend(self.device, PIPELINE_REVISION, reused=self._model is not None)
        if self._model is not None:
            return
        with phase("model_load"):
            self._load_model()

    def _load_model(self) -> None:
        # Verification precedes optional imports and, critically, remote-code
        # loading. This API never accepts a repository ID or fetches a model.
        verified = _verify_model_directory(self.model_directory)
        try:
            deps = _load_dependencies()
        except Exception:
            raise _safe_error("MODEL_UNAVAILABLE") from None
        self._check_device(deps)
        try:
            preprocess = deps.transforms.Compose([
                deps.transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
                deps.transforms.ToTensor(),
                deps.transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ])
            try:
                model = deps.auto_model.from_pretrained(
                    str(verified), local_files_only=True, trust_remote_code=True,
                    use_safetensors=True,
                )
                model = model.float().eval().to(self.device)
            finally:
                self._synchronize(deps)
        except GatewayError:
            raise
        except Exception as exc:
            raise _operation_error(exc, "MODEL_UNAVAILABLE") from None
        self._dependencies, self._preprocess, self._model = deps, preprocess, model

    def _infer_alpha(self, rgb: Image.Image) -> Image.Image:
        # This outer wall-clock span includes nested preprocessing and native
        # completion. Nested observations must not be added as independent time.
        with phase("model_inference"):
            return self._run_inference(rgb)

    def _run_inference(self, rgb: Image.Image) -> Image.Image:
        deps = self._dependencies
        try:
            try:
                with deps.torch.inference_mode():
                    with phase("preprocess"):
                        inputs = self._preprocess(rgb).unsqueeze(0).to(
                            device=self.device, dtype=deps.torch.float32,
                        )
                    outputs = self._model(inputs)
                    if not isinstance(outputs, (list, tuple)) or not outputs:
                        raise _safe_error("IMAGE_RESULT_INVALID")
                    logits = outputs[-1]
                    if (not deps.torch.is_tensor(logits)
                            or tuple(logits.shape) != (1, 1, INPUT_SIZE, INPUT_SIZE)
                            or not logits.dtype.is_floating_point
                            or not deps.torch.isfinite(logits).all().item()):
                        raise _safe_error("IMAGE_RESULT_INVALID")
                    probabilities = logits.sigmoid().squeeze(0).cpu()
                    alpha = deps.transforms.ToPILImage()(probabilities)
                    if alpha.mode != "L" or alpha.size != (INPUT_SIZE, INPUT_SIZE):
                        alpha.close()
                        raise _safe_error("IMAGE_RESULT_INVALID")
                    restored = alpha.resize(rgb.size, Image.Resampling.LANCZOS)
                    alpha.close()
                    if restored.getextrema()[1] == 0:
                        restored.close()
                        raise _safe_error("IMAGE_RESULT_INVALID")
                    return restored
            finally:
                # No success or failure is reported while native work remains
                # queued on the selected device. There is no CPU retry path.
                self._synchronize(deps)
        except GatewayError:
            raise
        except Exception as exc:
            raise _operation_error(exc, "IMAGE_PROCESSING_FAILED") from None

    def __call__(self, request: ImageRequest, project_id: UUID) -> CutoutResult:
        try:
            return self._process(request, project_id)
        finally:
            self._record_memory_snapshot()

    def _process(self, request: ImageRequest, project_id: UUID) -> CutoutResult:
        with phase("preprocess"):
            request = self._validated(request)
            # Read first: an unauthorized/tampered asset never triggers a model load.
            with self.assets.read(request.source, project_id=project_id) as source:
                rgb = source.convert("RGB")
        try:
            with self._execution_lock:
                self._ensure_loaded()
                alpha = self._infer_alpha(rgb)
            try:
                with phase("composite"):
                    product = rgb.copy()
                    product.putalpha(alpha)
                    keep = alpha.point(lambda value: 255 if value > 0 else 0)
                try:
                    with phase("asset_store"):
                        product_ref = self.assets.put_image(product, project_id=project_id)
                        mask_ref = self.assets.put_image(keep, project_id=project_id)
                finally:
                    product.close()
                    keep.close()
            finally:
                alpha.close()
        finally:
            rgb.close()
        return CutoutResult(
            product_rgba=product_ref, preserve_mask=mask_ref, needs_manual_check=True,
            warnings=["MODEL_SEGMENTATION_REQUIRES_REVIEW", "MANUAL_QUALITY_REVIEW_REQUIRED"],
        )

    def _record_memory_snapshot(self) -> None:
        """Best-effort process snapshot; never a peak, job delta or failure cause."""
        try:
            if not is_recording():
                return
            values = {"allocated_bytes": None, "driver_allocated_bytes": None, "reserved_bytes": None}
            reason = None
            if self.device == "cpu":
                reason = "NOT_GPU_DEVICE"
            elif self._dependencies is None:
                reason = "BACKEND_NOT_LOADED"
            else:
                names = (("allocated_bytes", "current_allocated_memory"),
                         ("driver_allocated_bytes", "driver_allocated_memory")) if self.device == "mps" else (
                             ("allocated_bytes", "memory_allocated"), ("reserved_bytes", "memory_reserved"))
                for field, name in names:
                    try:
                        api = getattr(getattr(self._dependencies.torch, self.device), name, None)
                        if not callable(api):
                            reason = reason or "MEMORY_API_UNAVAILABLE"
                            continue
                        value = api()
                        if type(value) is not int or not 0 <= value <= 2**63 - 1:
                            reason = reason or "INVALID_MEMORY_VALUE"
                        else:
                            values[field] = value
                    except Exception:
                        reason = reason or "MEMORY_SNAPSHOT_FAILED"
            record_memory(self.device, **values, unavailable_reason=reason)
        except Exception:
            # Instrumentation must not replace a successful result or its
            # original failure; no native exception text is retained.
            pass
