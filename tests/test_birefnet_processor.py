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
from beauty_ai.execution_trace import recording


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


@pytest.mark.parametrize("device", ["mps", "cuda", "cpu"])
def test_execution_trace_cold_and_reused_models_keep_outputs_and_observe_device(tmp_path, monkeypatch, device):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    memory_calls = []
    def memory(value):
        # Snapshots occur after native completion and result files are stored.
        assert len(list(assets.files_dir.rglob("*.png"))) >= 3
        assert backend.events[-1] == ("sync", device)
        memory_calls.append(value)
        return value
    backend.torch.mps.current_allocated_memory = lambda: memory(1234)
    backend.torch.mps.driver_allocated_memory = lambda: memory(5678)
    backend.torch.cuda.memory_allocated = lambda: memory(1234)
    backend.torch.cuda.memory_reserved = lambda: memory(5678)
    observations, results = [], []
    for _ in range(2):
        with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
            results.append(processor(request, project))
        observations.append(recorder.finish(outcome="succeeded"))
    for index, observation in enumerate(observations):
        assert observation.backend.model_dump() == {
            "device": device, "model_revision": module.PIPELINE_REVISION, "reused": bool(index),
        }
        names = [item.name for item in observation.phases]
        assert set(names) == {"preprocess", "model_inference", "composite", "asset_store"} | (
            {"model_load"} if index == 0 else set())
        assert all(item.outcome == "completed" and item.elapsed_ms >= 0 for item in observation.phases)
        assert [item.model_dump() for item in observation.synchronizations] == (
            [] if device == "cpu" else [{"device": device, "success": True}] * (2 - index))
        snapshot, = observation.memory_snapshots
        assert snapshot.basis == "post_operation_process_snapshot"
        assert snapshot.allocated_bytes == (None if device == "cpu" else 1234)
        assert snapshot.driver_allocated_bytes == (5678 if device == "mps" else None)
        assert snapshot.reserved_bytes == (5678 if device == "cuda" else None)
        assert snapshot.unavailable_reason == ("NOT_GPU_DEVICE" if device == "cpu" else None)
        assert str(processor.model_directory) not in observation.model_dump_json()
        assert str(request.source.asset_id) not in observation.model_dump_json()
    assert memory_calls == ([] if device == "cpu" else [1234, 5678] * 2)
    with assets.read(results[0].product_rgba, project_id=project) as cold:
        with assets.read(results[1].product_rgba, project_id=project) as warm:
            assert cold.tobytes() == warm.tobytes()


@pytest.mark.parametrize("failure,failed_phase,code", [
    ("profile", "preprocess", "IMAGE_PROFILE_NOT_FOUND"),
    ("imports", "model_load", "MODEL_UNAVAILABLE"),
    ("inference", "model_inference", "IMAGE_PROCESSING_FAILED"),
    ("synchronize", "model_inference", "IMAGE_PROCESSING_FAILED"),
    ("storage", "asset_store", "ASSET_STORAGE_FAILED"),
])
def test_execution_trace_records_actual_failed_stage_without_private_error(tmp_path, monkeypatch, failure, failed_phase, code):
    backend, assets, project, request, processor = setup(tmp_path, monkeypatch)
    if failure == "profile":
        request = request.model_copy(update={"pipeline_profile_id": "wrong-profile"})
    elif failure == "imports":
        def fail_import():
            raise ImportError("PRIVATE_BACKEND_DETAIL")
        monkeypatch.setattr(module, "_load_dependencies", fail_import)
    elif failure == "inference":
        backend.infer_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "synchronize":
        processor._ensure_loaded()
        backend.sync_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "storage":
        def fail_store(*args, **kwargs):
            raise GatewayError("ASSET_STORAGE_FAILED", "ASSET_STORAGE_FAILED")
        monkeypatch.setattr(assets, "put_image", fail_store)
    before = set(assets.files_dir.rglob("*.png"))
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        with pytest.raises(GatewayError, match=f"^{code}$"):
            processor(request, project)
    observation = recorder.finish(outcome="failed")
    assert [(item.name, item.outcome) for item in observation.phases if item.outcome == "failed"] == [
        (failed_phase, "failed"),
    ]
    assert set(assets.files_dir.rglob("*.png")) == before
    assert [item.success for item in observation.synchronizations] == {
        "profile": [], "imports": [], "inference": [True, True],
        "synchronize": [False], "storage": [True, True],
    }[failure]
    assert "PRIVATE_BACKEND_DETAIL" not in observation.model_dump_json()
    assert str(processor.model_directory) not in observation.model_dump_json()
    if failure in {"profile", "imports"}:
        assert observation.memory_snapshots[0].unavailable_reason == "BACKEND_NOT_LOADED"


@pytest.mark.parametrize("value", [True, -1, 2**63, 1.5, "1234", float("nan")])
def test_invalid_memory_values_remain_unknown_without_failing_cutout(tmp_path, monkeypatch, value):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.torch.mps.current_allocated_memory = lambda: value
    backend.torch.mps.driver_allocated_memory = lambda: 2000
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        result = processor(request, project)
    assert result.needs_manual_check
    snapshot, = recorder.finish(outcome="succeeded").memory_snapshots
    assert snapshot.allocated_bytes is None and snapshot.driver_allocated_bytes == 2000
    assert snapshot.unavailable_reason == "INVALID_MEMORY_VALUE"


@pytest.mark.parametrize("missing", [False, True])
def test_unavailable_memory_api_preserves_result_and_available_fields(tmp_path, monkeypatch, missing):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    def failed_memory():
        raise RuntimeError("PRIVATE_MEMORY_DETAIL")
    if not missing:
        backend.torch.mps.current_allocated_memory = failed_memory
    backend.torch.mps.driver_allocated_memory = lambda: 4096
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        result = processor(request, project)
    assert result.needs_manual_check
    observation = recorder.finish(outcome="succeeded")
    snapshot, = observation.memory_snapshots
    assert snapshot.allocated_bytes is None and snapshot.driver_allocated_bytes == 4096
    assert snapshot.unavailable_reason == ("MEMORY_API_UNAVAILABLE" if missing else "MEMORY_SNAPSHOT_FAILED")
    assert "PRIVATE_MEMORY_DETAIL" not in observation.model_dump_json()


def test_memory_is_not_queried_without_recording_and_recorder_failure_is_nonfatal(tmp_path, monkeypatch):
    backend, _, project, request, processor = setup(tmp_path, monkeypatch)
    calls = []
    def memory():
        calls.append("query")
        return 1
    backend.torch.mps.current_allocated_memory = memory
    backend.torch.mps.driver_allocated_memory = memory
    assert processor(request, project).needs_manual_check
    assert calls == []
    def broken_record(*args, **kwargs):
        raise RuntimeError("PRIVATE_RECORDER_DETAIL")
    monkeypatch.setattr(module, "record_memory", broken_record)
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        assert processor(request, project).needs_manual_check
    assert calls == ["query", "query"]
    assert recorder.finish(outcome="succeeded").memory_snapshots == []
