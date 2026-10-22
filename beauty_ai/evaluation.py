"""Offline image measurements with explicit provenance and no quality approval.

Rights and human reviews in a manifest are unverified declarations. The tool
never invokes a model, resolves a URL, or treats pixel agreement as acceptance.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
from typing import Annotated, Literal
import warnings

from PIL import Image, UnidentifiedImageError, __version__ as pillow_version
from pydantic import ConfigDict, Field, StringConstraints, ValidationError, model_validator

from .contracts import Contract
from .errors import GatewayError
from .evaluation_metrics import mask_metrics
from .image_preparation import inspect_composite


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
ImagePath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class EvaluationCase(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: Identifier
    split: Literal["dev", "holdout"]
    product_type: Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"\S")]
    rights_status: Literal["unknown", "declared_authorized", "synthetic_fixture"]
    rights_source: Annotated[str, StringConstraints(max_length=1000)]
    product_rgba: ImagePath
    composed_preview: ImagePath
    predicted_mask: ImagePath | None = None
    reference_mask: ImagePath | None = None
    human_review: Literal["not_assessed", "pass", "fail"] = "not_assessed"


class EvaluationManifest(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)

    dataset_id: Identifier
    version: Identifier
    cases: list[EvaluationCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvaluationManifest:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        return self


def _error(code: str) -> GatewayError:
    # Never propagate filenames, manifest input, decoder messages or OS errors.
    return GatewayError(code, code)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _load_manifest(path: Path) -> tuple[EvaluationManifest, bytes, Path]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise _error("EVALUATION_MANIFEST_INVALID")
            raw = source.read(MAX_MANIFEST_BYTES + 1)
        root = path.resolve(strict=True).parent
    except (OSError, ValueError, RuntimeError) as exc:
        if isinstance(exc, GatewayError):
            raise
        raise _error("EVALUATION_MANIFEST_UNAVAILABLE") from None
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _error("EVALUATION_MANIFEST_TOO_LARGE")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
        manifest = EvaluationManifest.model_validate(document)
    except (ValueError, UnicodeError, RecursionError, ValidationError):
        raise _error("EVALUATION_MANIFEST_INVALID") from None
    return manifest, raw, root


def _contained_path(root: Path, relative: str) -> Path:
    # Colon/backslash are rejected too, so Windows drives and URL-like inputs
    # cannot acquire different meanings when a manifest moves between hosts.
    pure = PurePosixPath(relative)
    if (":" in relative or "\\" in relative or "\x00" in relative
            or pure.is_absolute() or ".." in pure.parts or not pure.parts):
        raise _error("EVALUATION_PATH_INVALID")
    try:
        resolved = (root / pure).resolve()
        if not resolved.is_relative_to(root) or resolved == root:
            raise _error("EVALUATION_PATH_INVALID")
    except (OSError, RuntimeError, ValueError):
        raise _error("EVALUATION_PATH_INVALID") from None
    return resolved


def _read_contained_bytes(root: Path, path: Path) -> bytes:
    """Open a resolved, contained regular file without following replaced links."""
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        parts = path.relative_to(root).parts
        for part in parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            descriptors.append(directory)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(file_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise _error("EVALUATION_IMAGE_INVALID")
        if info.st_size > MAX_IMAGE_BYTES:
            raise _error("EVALUATION_IMAGE_TOO_LARGE")
        with os.fdopen(os.dup(file_fd), "rb") as source:
            raw = source.read(MAX_IMAGE_BYTES + 1)
        if len(raw) > MAX_IMAGE_BYTES:
            raise _error("EVALUATION_IMAGE_TOO_LARGE")
        return raw
    except OSError:
        raise _error("EVALUATION_IMAGE_UNAVAILABLE") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_image(root: Path, path: Path) -> tuple[Image.Image, dict]:
    raw = _read_contained_bytes(root, path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP"):
                    raise _error("EVALUATION_IMAGE_INVALID")
                if (source.width < 1 or source.height < 1
                        or source.width * source.height > MAX_IMAGE_PIXELS):
                    raise _error("EVALUATION_IMAGE_TOO_LARGE")
                if getattr(source, "n_frames", 1) != 1 or getattr(source, "is_animated", False):
                    raise _error("EVALUATION_IMAGE_INVALID")
                source.load()
                metadata = {
                    "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                    "width": source.width, "height": source.height,
                    "mode": source.mode, "format": source.format,
                }
                return source.copy(), metadata
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise _error("EVALUATION_IMAGE_TOO_LARGE") from None
    except (OSError, ValueError, SyntaxError, EOFError, UnidentifiedImageError):
        raise _error("EVALUATION_IMAGE_INVALID") from None


def _evaluate_case(case: EvaluationCase, root: Path) -> dict:
    result = {
        "case_id": case.case_id, "split": case.split, "product_type": case.product_type,
        "rights_status": case.rights_status, "rights_source": case.rights_source,
        "rights_verification": "NOT_ASSESSED",
        "human_review": case.human_review,
        "human_review_provenance": "manifest_declaration_unverified",
        "quality_approval": "NOT_ASSESSED", "status": "NOT_ASSESSED",
        "reason": None, "error_code": None, "files": {},
        "pixel_integrity": None, "mask_metrics": None, "mask_metrics_reason": None,
    }
    images = {}
    try:
        paths = {
            field: _contained_path(root, value)
            for field in ("product_rgba", "composed_preview", "predicted_mask", "reference_mask")
            if (value := getattr(case, field)) is not None
        }
        if case.rights_status == "unknown":
            result.update(reason="RIGHTS_UNKNOWN", mask_metrics_reason="RIGHTS_UNKNOWN")
            return result
        for field, path in paths.items():
            images[field], result["files"][field] = _read_image(root, path)
        product, preview = images["product_rgba"], images["composed_preview"]
        if product.mode != "RGBA":
            raise _error("EVALUATION_PRODUCT_ALPHA_REQUIRED")
        if preview.mode not in ("RGB", "RGBA") or preview.size != product.size:
            raise _error("EVALUATION_CANVAS_INVALID")
        try:
            integrity = inspect_composite(preview, product)
        except ValueError:
            raise _error("EVALUATION_PRODUCT_INVALID") from None
        pixel_integrity = asdict(integrity) | {
            "opaque_preserved": integrity.opaque_preserved if integrity.opaque_pixels else None,
            "reason": None if integrity.opaque_pixels else "NO_OPAQUE_PIXELS",
            "needs_manual_check": integrity.needs_manual_check,
        }
        for field in ("predicted_mask", "reference_mask"):
            if field in images and images[field].size != product.size:
                raise _error("EVALUATION_CANVAS_INVALID")
        metrics = None
        if "reference_mask" not in images:
            metrics_reason = "REFERENCE_MASK_MISSING"
        elif "predicted_mask" not in images:
            metrics_reason = "PREDICTED_MASK_MISSING"
        else:
            try:
                metrics = asdict(mask_metrics(images["predicted_mask"], images["reference_mask"]))
            except ValueError:
                raise _error("EVALUATION_MASK_INVALID") from None
            metrics_reason = "NO_FOREGROUND_IN_EITHER_MASK" if metrics["iou"] is None else None
        result.update(status="MEASURED", pixel_integrity=pixel_integrity,
                      mask_metrics=metrics, mask_metrics_reason=metrics_reason)
    except GatewayError as exc:
        result.update(status="ERROR", reason="MEASUREMENT_FAILED", error_code=exc.code,
                      pixel_integrity=None, mask_metrics=None, mask_metrics_reason="MEASUREMENT_FAILED")
    finally:
        for image in images.values():
            image.close()
    return result


def evaluate_manifest(path: str | os.PathLike[str]) -> dict:
    """Measure bounded local files; malformed manifests raise safe GatewayError.

    Invalid individual files are case-level ERROR records. Unknown rights skip
    image reads. No image paths or decoder exception text are included in output.
    """
    try:
        manifest_path = Path(path)
    except (TypeError, ValueError):
        raise _error("EVALUATION_MANIFEST_INVALID") from None
    manifest, raw, root = _load_manifest(manifest_path)
    return {
        "schema_version": "1", "dataset_id": manifest.dataset_id, "version": manifest.version,
        "manifest": {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
                     "case_count": len(manifest.cases)},
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_environment": {
            "python": platform.python_version(), "pillow": pillow_version,
            "system": platform.system(), "machine": platform.machine(),
            "pixel_policy": "decoded pixels; no EXIF rotation or color conversion",
            "mask_definition": "white foreground; threshold 128; 3x3 zero-padded erosion boundary; square tolerance 2px",
        },
        "model_invoked": False, "quality_approval": "NOT_ASSESSED",
        "runtime_measurements": {
            "status": "NOT_MEASURED", "reason": "MODEL_NOT_INVOKED",
            "gpu_elapsed_ms": None, "peak_vram_bytes": None, "cost": None,
            "real_model_quality": None,
        },
        "cases": [_evaluate_case(case, root) for case in manifest.cases],
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _error("EVALUATION_ARGUMENT_INVALID")


def _write_report(path: Path, report: dict) -> None:
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise _error("EVALUATION_OUTPUT_EXISTS") from None
    except (OSError, ValueError):
        raise _error("EVALUATION_OUTPUT_UNAVAILABLE") from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
    except (OSError, ValueError):
        # The exclusively-created file is ours. Never remove an existing output.
        try:
            path.unlink()
        except OSError:
            pass
        raise _error("EVALUATION_OUTPUT_UNAVAILABLE") from None


def main(argv: list[str] | None = None) -> int:
    parser = _SafeArgumentParser(description="Measure local images without invoking a model.")
    parser.add_argument("manifest")
    parser.add_argument("--output", default="outputs/evaluation-report.json")
    try:
        args = parser.parse_args(argv)
        report = evaluate_manifest(args.manifest)
        _write_report(Path(args.output), report)
    except GatewayError as exc:
        print(json.dumps({"error_code": exc.code}), file=sys.stderr)
        return 2
    print(json.dumps({"report_written": True, "case_count": len(report["cases"]), "model_invoked": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
