"""Adapter control/data-flow tests; the bounded backend is not model evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai import birefnet_processor as module
from beauty_ai.contracts import CutoutRequest
from beauty_ai.errors import GatewayError


class Tensor:
    def __init__(self, backend, shape=(1, 1, 1024, 1024), *, finite=True, floating=True):
        self.backend, self.shape, self.finite = backend, shape, finite
        self.dtype = SimpleNamespace(is_floating_point=floating)

    def unsqueeze(self, dimension):
        assert dimension == 0
        self.shape = (1,) + self.shape
        return self

    def to(self, *, device, dtype):
        self.backend.events.append(("input_to", device, dtype))
        return self

    def sigmoid(self):
        self.backend.events.append("sigmoid")
        return self

    def squeeze(self, dimension):
        assert dimension == 0
        self.shape = self.shape[1:]
        return self

    def cpu(self):
        self.backend.events.append("output_cpu")
        return self


class Backend:
    """Small dependency double: real Pillow transforms/asset files, no torch."""
    def __init__(self):
        self.events = []
        self.available = {"mps": True, "cuda": True}
        self.version = "0.29.1"
        self.finite, self.floating = True, True
        self.output_shape = (1, 1, 1024, 1024)
        self.empty_alpha = False
        self.outputs_kind = "list"
        self.infer_error = self.load_error = self.sync_error = None
        self.inference_active = False
        self.hold_event = self.entered_event = None
        self.torch = SimpleNamespace(
            float32="float32", inference_mode=self.inference_mode,
            is_tensor=lambda value: isinstance(value, Tensor),
            isfinite=lambda value: SimpleNamespace(all=lambda: SimpleNamespace(item=lambda: value.finite)),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: self.available["mps"])),
            mps=SimpleNamespace(synchronize=lambda: self.synchronize("mps")),
            cuda=SimpleNamespace(is_available=lambda: self.available["cuda"], synchronize=lambda: self.synchronize("cuda")),
        )
        self.transforms = SimpleNamespace(
            Compose=self.compose, Resize=self.resize, ToTensor=self.to_tensor,
            Normalize=self.normalize, ToPILImage=self.to_pil,
        )
        self.model = SimpleNamespace(float=self.float_model, eval=self.eval_model, to=self.move_model)
        self.auto_model = SimpleNamespace(from_pretrained=self.from_pretrained)

    def dependencies(self):
        self.events.append("imports")
        return module._Dependencies(self.torch, self.transforms, self.auto_model, self.version)

    @contextmanager
    def inference_mode(self):
        assert not self.inference_active
        self.inference_active = True
        self.events.append("inference_enter")
        try:
            yield
        finally:
            self.inference_active = False
            self.events.append("inference_exit")

    def synchronize(self, device):
        self.events.append(("sync", device))
        if self.sync_error:
            raise self.sync_error

    def compose(self, transforms):
        def execute(value):
            for transform in transforms:
                value = transform(value)
            return value
        return execute

    def resize(self, size):
        self.events.append(("resize_size", size))
        return lambda image: image.resize(size)

    def to_tensor(self):
        def execute(image):
            assert image.mode == "RGB"
            result = Tensor(self, shape=(3, image.height, image.width))
            image.close()
            return result
        return execute

    def normalize(self, mean, std):
        self.events.append(("normalization", mean, std))
        return lambda value: value

    def to_pil(self):
        def execute(value):
            assert value.shape == (1, 1024, 1024)
            alpha = Image.new("L", (1024, 1024), 0)
            if not self.empty_alpha:
                alpha.paste(255, (256, 0, 768, 1024))
                alpha.paste(128, (768, 0, 1024, 1024))
            return alpha
        return execute

    def from_pretrained(self, path, **kwargs):
        self.events.append(("load", path, kwargs))
        if self.load_error:
            raise self.load_error
        return self.model

    def float_model(self):
        self.events.append("float32_model")
        return self.model

    def eval_model(self):
        self.events.append("eval")
        return self.model

    def move_model(self, device):
        self.events.append(("model_to", device))
        return self

    def __call__(self, tensor):
        assert self.inference_active and tensor.shape == (1, 3, 1024, 1024)
        self.events.append("infer")
        if self.entered_event:
            self.entered_event.set()
            assert self.hold_event.wait(5)
        if self.infer_error:
            raise self.infer_error
        output = Tensor(self, shape=self.output_shape, finite=self.finite, floating=self.floating)
        if self.outputs_kind == "empty":
            return []
        if self.outputs_kind == "tensor":
            return output
        if self.outputs_kind == "object":
            return [object()]
        # A prior, incorrect tensor must not replace the last output.
        return [Tensor(self, shape=(1, 1, 1, 1), finite=False), output]


def setup(tmp_path, monkeypatch, *, device="mps"):
    backend = Backend()
    model_directory = tmp_path / "models" / module.PIPELINE_REVISION
    model_directory.mkdir(parents=True)
    def verify(path):
        assert path == model_directory
        backend.events.append("verified")
        return path
    monkeypatch.setattr(module, "_verify_model_directory", verify)
    monkeypatch.setattr(module, "_load_dependencies", backend.dependencies)
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    assets, project = LocalAssetStore(tmp_path / "assets"), uuid4()
    source_image = Image.new("RGB", (16, 24), (19, 87, 200))
    source_image.putpixel((3, 2), (1, 2, 3))
    source = assets.put_image(source_image, project_id=project)
    request = CutoutRequest(source=source, pipeline_profile_id=module.PROFILE_ID,
                            pipeline_revision=module.PIPELINE_REVISION)
    processor = module.BiRefNetCutoutProcessor(assets, model_directory, device=device)
    return backend, assets, project, request, processor


def test_local_verified_load_fp32_normalization_and_original_rgb_preserved(tmp_path, monkeypatch):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    assert backend.events == []
    assert processor.validate_request(request) is None
    assert backend.events == []
    result = processor(request, project)
    assert backend.events[:2] == ["verified", "imports"]
    load_event = next(event for event in backend.events if isinstance(event, tuple) and event[0] == "load")
    assert load_event == ("load", str(processor.model_directory), {
        "local_files_only": True, "trust_remote_code": True, "use_safetensors": True,
    })
    assert backend.events.index("float32_model") < backend.events.index("eval") < backend.events.index(("model_to", "mps"))
    assert ("resize_size", (1024, 1024)) in backend.events
    assert ("normalization", [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]) in backend.events
    assert ("input_to", "mps", "float32") in backend.events
    assert backend.events[-1] == ("sync", "mps")
    assert result.needs_manual_check
    assert result.warnings == ["MODEL_SEGMENTATION_REQUIRES_REVIEW", "MANUAL_QUALITY_REVIEW_REQUIRED"]
    with assets.read(request.source, project_id=project) as source:
        with assets.read(result.product_rgba, project_id=project) as product:
            assert product.size == source.size and product.mode == "RGBA"
            assert product.convert("RGB").tobytes() == source.convert("RGB").tobytes()
            alpha = product.getchannel("A")
            assert alpha.getextrema() == (0, 255)
            with assets.read(result.preserve_mask, project_id=project) as mask:
                assert mask.size == product.size
                assert mask.convert("L").tobytes() == alpha.point(lambda value: 255 if value > 0 else 0).tobytes()


def test_model_loads_once_for_multiple_requests(tmp_path, monkeypatch):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    processor(request, project)
    processor(request, project)
    assert backend.events.count("verified") == backend.events.count("imports") == 1
    assert len([event for event in backend.events if isinstance(event, tuple) and event[0] == "load"]) == 1
    assert backend.events.count("infer") == 2


def test_concurrent_callers_serialize_model_execution_and_load(tmp_path, monkeypatch):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.entered_event, backend.hold_event = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(processor, request, project)
        assert backend.entered_event.wait(3)
        second = pool.submit(processor, request, project)
        backend.hold_event.set()
        assert first.result(timeout=5).needs_manual_check
        assert second.result(timeout=5).needs_manual_check
    assert backend.events.count("verified") == 1
    assert backend.events.count("infer") == 2


def test_failed_verification_prevents_all_imports_and_remote_code(tmp_path, monkeypatch):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    def reject(path):
        raise GatewayError("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE")
    monkeypatch.setattr(module, "_verify_model_directory", reject)
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert backend.events == []


def test_foreign_or_tampered_asset_never_loads_model(tmp_path, monkeypatch):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as error:
        processor(request, uuid4())
    assert error.value.code == "ASSET_NOT_FOUND"
    path = assets.files_dir / project.hex / f"{request.source.asset_id.hex}.png"
    path.write_bytes(b"tampered")
    with pytest.raises(GatewayError) as error:
        processor(request, project)
    assert error.value.code == "ASSET_INTEGRITY_ERROR"
    assert backend.events == []


@pytest.mark.parametrize("device", ["mps", "cuda"])
def test_unavailable_device_is_not_silently_replaced_with_cpu(tmp_path, monkeypatch, device):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    backend.available[device] = False
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert not any(isinstance(event, tuple) and event[0] == "load" for event in backend.events)


@pytest.mark.parametrize("device", ["cpu", "cuda"])
def test_explicit_cpu_and_cuda_stay_on_selected_device(tmp_path, monkeypatch, device):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    assert processor(request, project).needs_manual_check
    assert ("model_to", device) in backend.events
    assert ("input_to", device, "float32") in backend.events
    assert ("model_to", "mps") not in backend.events
    if device == "cpu":
        assert not any(isinstance(event, tuple) and event[0] == "sync" for event in backend.events)
    else:
        assert backend.events[-1] == ("sync", "cuda")


@pytest.mark.parametrize("version", ["0.22.1", "invalid"])
def test_mps_requires_supported_torchvision(tmp_path, monkeypatch, version):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.version = version
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)


def test_mps_rejects_global_cpu_fallback(tmp_path, monkeypatch):
    _, _, project, request, processor = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)


@pytest.mark.parametrize("changes", [
    {"pipeline_profile_id": "private/model"}, {"pipeline_revision": "main"},
])
def test_profile_and_revision_are_fixed(tmp_path, monkeypatch, changes):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    request = request.model_copy(update=changes)
    with pytest.raises(GatewayError, match="^IMAGE_PROFILE_NOT_FOUND$"):
        processor(request, project)
    assert backend.events == []


def test_mutated_request_and_unknown_device_rejected(tmp_path, monkeypatch):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    for invalid in (request.model_copy(update={"kind": "render_background"}), {}, None):
        with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
            processor(invalid, project)
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        module.BiRefNetCutoutProcessor(assets, tmp_path, device="auto")
    assert backend.events == []


@pytest.mark.parametrize("field,value", [
    ("finite", False), ("floating", False), ("output_shape", (1, 3, 1024, 1024)),
    ("output_shape", (1, 1, 512, 512)), ("empty_alpha", True),
    ("outputs_kind", "empty"), ("outputs_kind", "tensor"), ("outputs_kind", "object"),
])
def test_invalid_outputs_fail_after_device_completion_without_asset_publication(tmp_path, monkeypatch, field, value):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    setattr(backend, field, value)
    before = set(assets.files_dir.rglob("*.png"))
    with pytest.raises(GatewayError, match="^IMAGE_RESULT_INVALID$"):
        processor(request, project)
    assert backend.events[-1] == ("sync", "mps")
    assert set(assets.files_dir.rglob("*.png")) == before


@pytest.mark.parametrize("failure,code", [
    (RuntimeError("private/path backend failure"), "IMAGE_PROCESSING_FAILED"),
    (RuntimeError("MPS out of memory private/path"), "IMAGE_OUT_OF_MEMORY"),
    (MemoryError("private/path"), "IMAGE_OUT_OF_MEMORY"),
])
def test_inference_errors_are_sanitized_and_synchronized(tmp_path, monkeypatch, failure, code):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.infer_error = failure
    with pytest.raises(GatewayError) as error:
        processor(request, project)
    assert error.value.code == str(error.value) == code
    assert backend.events[-1] == ("sync", "mps")


def test_missing_dependencies_and_load_errors_are_sanitized(tmp_path, monkeypatch):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    original = module._load_dependencies
    def unavailable():
        raise ImportError("private/path/dependency")
    monkeypatch.setattr(module, "_load_dependencies", unavailable)
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    monkeypatch.setattr(module, "_load_dependencies", original)
    backend.load_error = RuntimeError("private model path")
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert backend.events[-1] == ("sync", "mps")
    assert processor._model is None


def test_sync_failure_prevents_asset_publication(tmp_path, monkeypatch):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    processor._ensure_loaded()
    backend.sync_error = RuntimeError("private GPU details")
    before = set(assets.files_dir.rglob("*.png"))
    with pytest.raises(GatewayError, match="^IMAGE_PROCESSING_FAILED$"):
        processor(request, project)
    assert set(assets.files_dir.rglob("*.png")) == before


def test_module_import_does_not_load_optional_model_dependencies():
    command = [sys.executable, "-c", (
        "import json, sys; import beauty_ai.birefnet_processor; "
        "print(json.dumps([name for name in ('torch','torchvision','transformers','timm') if name in sys.modules]))"
    )]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
