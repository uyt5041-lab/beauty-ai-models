from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from beauty_ai import evaluation
from beauty_ai.errors import GatewayError


REPO = Path(__file__).resolve().parents[1]


def write_manifest(root: Path, *, case_changes=None, top_changes=None) -> Path:
    case = {
        "case_id": "bottle-1", "split": "dev", "product_type": "cosmetic bottle",
        "rights_status": "synthetic_fixture", "rights_source": "Created in this test",
        "product_rgba": "product.png", "composed_preview": "preview.png",
        "predicted_mask": "predicted.png", "reference_mask": "reference.png",
    }
    case.update(case_changes or {})
    document = {"dataset_id": "test-images", "version": "1", "cases": [case]}
    document.update(top_changes or {})
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest


def save_images(root: Path) -> None:
    product = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    product.paste((80, 100, 120, 255), (2, 2, 6, 6))
    product.putpixel((1, 2), (80, 100, 120, 128))
    product.save(root / "product.png")
    background = Image.new("RGBA", product.size, (250, 220, 100, 255))
    Image.alpha_composite(background, product).convert("RGB").save(root / "preview.png")
    mask = product.getchannel("A").point(lambda value: 255 if value else 0)
    mask.save(root / "predicted.png")
    mask.save(root / "reference.png")


def evaluate_case(root: Path, **changes) -> dict:
    return evaluation.evaluate_manifest(write_manifest(root, case_changes=changes))["cases"][0]


def test_measurement_has_hashes_real_counts_and_unverified_provenance(tmp_path):
    save_images(tmp_path)
    path = write_manifest(tmp_path, case_changes={"human_review": "pass"})
    report = evaluation.evaluate_manifest(path)
    result = report["cases"][0]
    assert report["manifest"] == {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size, "case_count": 1,
    }
    assert report["dataset_id"] == "test-images" and report["version"] == "1"
    assert datetime.fromisoformat(report["evaluated_at"]).utcoffset().total_seconds() == 0
    assert report["evaluation_environment"]["python"]
    assert report["model_invoked"] is False
    assert report["runtime_measurements"] == {
        "status": "NOT_MEASURED", "reason": "MODEL_NOT_INVOKED",
        "gpu_elapsed_ms": None, "peak_vram_bytes": None, "cost": None,
        "real_model_quality": None,
    }
    assert result["status"] == "MEASURED"
    assert result["human_review"] == "pass"
    assert result["human_review_provenance"] == "manifest_declaration_unverified"
    assert result["rights_verification"] == "NOT_ASSESSED"
    assert result["quality_approval"] == report["quality_approval"] == "NOT_ASSESSED"
    assert result["pixel_integrity"] == {
        "opaque_pixels": 16, "mismatched_pixels": 0, "partial_alpha_pixels": 1,
        "opaque_preserved": True, "reason": None, "needs_manual_check": True,
    }
    assert result["mask_metrics"] == {
        "iou": 1, "boundary_precision": 1, "boundary_recall": 1, "boundary_f1": 1,
        "threshold": 128, "tolerance": 2,
    }
    for field, filename in (("product_rgba", "product.png"), ("composed_preview", "preview.png")):
        metadata = result["files"][field]
        assert metadata["sha256"] == hashlib.sha256((tmp_path / filename).read_bytes()).hexdigest()
        assert metadata["size_bytes"] == (tmp_path / filename).stat().st_size
        assert metadata["width"] == metadata["height"] == 8
    encoded = json.dumps(report)
    assert str(tmp_path) not in encoded
    assert "product.png" not in encoded


def test_unknown_rights_skips_all_file_reads_even_with_human_pass(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("Unknown-rights images must not be read")
    monkeypatch.setattr(evaluation, "_read_image", forbidden)
    result = evaluate_case(tmp_path, rights_status="unknown", human_review="pass")
    assert result["status"] == "NOT_ASSESSED" and result["reason"] == "RIGHTS_UNKNOWN"
    assert result["files"] == {} and result["mask_metrics"] is None
    assert result["pixel_integrity"] is None and result["quality_approval"] == "NOT_ASSESSED"


def test_example_runs_without_fictional_images():
    report = evaluation.evaluate_manifest(REPO / "examples/evaluation_manifest.json")
    assert report["cases"][0]["status"] == "NOT_ASSESSED"
    assert report["cases"][0]["reason"] == "RIGHTS_UNKNOWN"


@pytest.mark.parametrize("field,reason", [
    ("reference_mask", "REFERENCE_MASK_MISSING"),
    ("predicted_mask", "PREDICTED_MASK_MISSING"),
])
def test_missing_ground_truth_or_prediction_keeps_metrics_unknown(tmp_path, field, reason):
    save_images(tmp_path)
    result = evaluate_case(tmp_path, **{field: None, "rights_status": "declared_authorized"})
    assert result["status"] == "MEASURED"
    assert result["mask_metrics"] is None and result["mask_metrics_reason"] == reason
    assert result["rights_verification"] == "NOT_ASSESSED"


def test_pixel_mismatch_does_not_become_quality_approval(tmp_path):
    save_images(tmp_path)
    with Image.open(tmp_path / "preview.png") as image:
        image.putpixel((2, 2), (0, 0, 0))
        image.save(tmp_path / "preview.png")
    result = evaluate_case(tmp_path)
    assert result["pixel_integrity"]["mismatched_pixels"] == 1
    assert result["pixel_integrity"]["opaque_preserved"] is False
    assert result["quality_approval"] == "NOT_ASSESSED"


def test_no_opaque_pixels_is_unverifiable_and_empty_alpha_is_invalid(tmp_path):
    save_images(tmp_path)
    Image.new("RGBA", (8, 8), (10, 20, 30, 128)).save(tmp_path / "product.png")
    result = evaluate_case(tmp_path)
    assert result["status"] == "MEASURED"
    assert result["pixel_integrity"]["opaque_pixels"] == 0
    assert result["pixel_integrity"]["opaque_preserved"] is None
    assert result["pixel_integrity"]["reason"] == "NO_OPAQUE_PIXELS"
    Image.new("RGBA", (8, 8), (10, 20, 30, 0)).save(tmp_path / "product.png")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_PRODUCT_INVALID"


def test_rgb_is_not_converted_to_counterfeit_product_alpha(tmp_path):
    save_images(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "product.png")
    result = evaluate_case(tmp_path)
    assert result["status"] == "ERROR"
    assert result["error_code"] == "EVALUATION_PRODUCT_ALPHA_REQUIRED"
    assert result["pixel_integrity"] is None


@pytest.mark.parametrize("path", ["../private.png", "/tmp/private.png", "https://example.com/x.png", "C:\\secret.png", ".", "images/../../secret.png"])
def test_path_escape_url_and_absolute_paths_are_rejected(tmp_path, path):
    result = evaluate_case(tmp_path, product_rgba=path, rights_status="unknown")
    assert result["error_code"] == "EVALUATION_PATH_INVALID"
    assert path not in json.dumps(result)


def test_external_symlink_rejected_and_contained_symlink_allowed(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    save_images(root)
    Image.new("RGBA", (8, 8), "red").save(tmp_path / "outside.png")
    (root / "external.png").symlink_to(tmp_path / "outside.png")
    assert evaluate_case(root, product_rgba="external.png")["error_code"] == "EVALUATION_PATH_INVALID"
    (root / "internal.png").symlink_to(root / "product.png")
    assert evaluate_case(root, product_rgba="internal.png")["status"] == "MEASURED"


def test_replaced_symlink_after_validation_is_not_followed(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "a.png").write_bytes(b"initial")
    path = evaluation._contained_path(root, "a.png")
    (tmp_path / "secret").write_bytes(b"must not read")
    path.unlink()
    path.symlink_to(tmp_path / "secret")
    with pytest.raises(GatewayError) as error:
        evaluation._read_contained_bytes(root, path)
    assert error.value.code == "EVALUATION_IMAGE_UNAVAILABLE"
    assert str(tmp_path) not in str(error.value)


def test_broken_missing_and_unsupported_images_return_sanitized_error(tmp_path):
    save_images(tmp_path)
    (tmp_path / "product.png").write_bytes(b"secret api-key fake png")
    result = evaluate_case(tmp_path)
    assert result["error_code"] == "EVALUATION_IMAGE_INVALID"
    assert "secret" not in json.dumps(result)
    (tmp_path / "product.png").unlink()
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_UNAVAILABLE"
    Image.new("RGBA", (8, 8), "red").save(tmp_path / "product.png", format="TIFF")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_INVALID"


def test_animated_png_is_rejected(tmp_path):
    save_images(tmp_path)
    Image.new("RGBA", (8, 8), "red").save(
        tmp_path / "product.png", save_all=True,
        append_images=[Image.new("RGBA", (8, 8), "blue")], duration=100, loop=0,
    )
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_INVALID"


def test_image_pixel_and_byte_limits_apply_to_actual_files(tmp_path, monkeypatch):
    save_images(tmp_path)
    monkeypatch.setattr(evaluation, "MAX_IMAGE_PIXELS", 63)
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_TOO_LARGE"
    monkeypatch.setattr(evaluation, "MAX_IMAGE_PIXELS", 16_000_000)
    monkeypatch.setattr(evaluation, "MAX_IMAGE_BYTES", (tmp_path / "product.png").stat().st_size - 1)
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_TOO_LARGE"


def test_mask_colors_and_canvas_mismatch_are_rejected(tmp_path):
    save_images(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "predicted.png")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_MASK_INVALID"
    Image.new("L", (7, 8), 255).save(tmp_path / "predicted.png")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_CANVAS_INVALID"
    save_images(tmp_path)
    Image.new("RGB", (7, 8), "red").save(tmp_path / "preview.png")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_CANVAS_INVALID"


def test_empty_mask_union_is_reported_as_undefined(tmp_path):
    save_images(tmp_path)
    for name in ("predicted.png", "reference.png"):
        Image.new("L", (8, 8), 0).save(tmp_path / name)
    result = evaluate_case(tmp_path)
    assert result["status"] == "MEASURED" and result["mask_metrics"]["iou"] is None
    assert result["mask_metrics_reason"] == "NO_FOREGROUND_IN_EITHER_MASK"


@pytest.mark.parametrize("change", [
    {"dataset_id": 12}, {"version": " "}, {"api_secret_key": "private-value"},
    {"dataset_id": "a" * 129}, {"cases": []},
])
def test_invalid_manifest_fields_have_stable_safe_errors(tmp_path, change):
    path = write_manifest(tmp_path, top_changes=change)
    with pytest.raises(GatewayError) as error:
        evaluation.evaluate_manifest(path)
    assert error.value.code == "EVALUATION_MANIFEST_INVALID"
    assert str(error.value) == "EVALUATION_MANIFEST_INVALID"


def test_duplicate_ids_too_many_cases_and_duplicate_json_keys_are_rejected(tmp_path):
    path = write_manifest(tmp_path)
    document = json.loads(path.read_text())
    document["cases"] *= 2
    path.write_text(json.dumps(document))
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_INVALID$"):
        evaluation.evaluate_manifest(path)
    document["cases"] = [dict(document["cases"][0], case_id=str(index)) for index in range(201)]
    path.write_text(json.dumps(document))
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_INVALID$"):
        evaluation.evaluate_manifest(path)
    path.write_text('{"dataset_id":"private-value","dataset_id":"override"}')
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_INVALID$"):
        evaluation.evaluate_manifest(path)


def test_manifest_size_invalid_json_and_missing_file_are_bounded(tmp_path, monkeypatch):
    path = write_manifest(tmp_path)
    monkeypatch.setattr(evaluation, "MAX_MANIFEST_BYTES", path.stat().st_size - 1)
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_TOO_LARGE$"):
        evaluation.evaluate_manifest(path)
    path.write_bytes(b"\xff raw private-value")
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_INVALID$"):
        evaluation.evaluate_manifest(path)
    path.unlink()
    with pytest.raises(GatewayError, match="^EVALUATION_MANIFEST_UNAVAILABLE$"):
        evaluation.evaluate_manifest(path)


def test_cli_writes_private_json_and_does_not_overwrite(tmp_path):
    save_images(tmp_path)
    path = write_manifest(tmp_path)
    output = tmp_path / "outputs" / "report.json"
    command = [sys.executable, "-m", "beauty_ai.evaluation", str(path), "--output", str(output)]
    first = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    assert first.stderr == ""
    report = json.loads(output.read_text())
    assert report["cases"][0]["status"] == "MEASURED"
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert json.loads(first.stdout)["model_invoked"] is False
    original = output.read_bytes()
    second = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    assert second.returncode == 2 and second.stdout == ""
    assert json.loads(second.stderr) == {"error_code": "EVALUATION_OUTPUT_EXISTS"}
    assert output.read_bytes() == original
    assert str(tmp_path) not in second.stderr


def test_cli_sanitizes_invalid_input_and_argument_errors(tmp_path):
    path = write_manifest(tmp_path, top_changes={"api_key_DO_NOT_ECHO": "private-value"})
    command = [sys.executable, "-m", "beauty_ai.evaluation", str(path)]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert json.loads(result.stderr) == {"error_code": "EVALUATION_MANIFEST_INVALID"}
    result = subprocess.run(command + ["--private-value"], cwd=REPO, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert json.loads(result.stderr) == {"error_code": "EVALUATION_ARGUMENT_INVALID"}


def test_fifo_manifest_and_image_fail_without_blocking(tmp_path):
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    result = subprocess.run(
        [sys.executable, "-m", "beauty_ai.evaluation", str(fifo)],
        cwd=REPO, capture_output=True, text=True, check=False, timeout=3,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr) == {"error_code": "EVALUATION_MANIFEST_INVALID"}
    save_images(tmp_path)
    (tmp_path / "product.png").unlink()
    os.mkfifo(tmp_path / "product.png")
    assert evaluate_case(tmp_path)["error_code"] == "EVALUATION_IMAGE_INVALID"


def test_case_failure_does_not_abort_other_measurements(tmp_path):
    save_images(tmp_path)
    path = write_manifest(tmp_path)
    document = json.loads(path.read_text())
    document["cases"].append(dict(document["cases"][0], case_id="bad", product_rgba="missing.png"))
    path.write_text(json.dumps(document))
    report = evaluation.evaluate_manifest(path)
    assert [case["status"] for case in report["cases"]] == ["MEASURED", "ERROR"]
