"""Bounded adapter tests using real approved assets, not model-quality evidence."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image, ImageChops
import pytest

from beauty_ai import sdxl_processor as module
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundRequest, ImagePrompt, Rect, VisualBrief
from beauty_ai.errors import GatewayError
from beauty_ai.execution_trace import recording
from beauty_ai.image_preparation import inspect_composite
from beauty_ai.product_approval import ProductApprovalStore


class Array:
    """Uniform float-array double; avoids a mandatory numpy/model test dependency."""
    def __init__(self, shape, values=(0.2, 0.4, 0.6), dtype="float"):
        self.shape, self.values, self.dtype = shape, values, dtype

    def min(self):
        return min(self.values)

    def max(self):
        return max(self.values)

    def __getitem__(self, index):
        assert index == 0
        return Array(self.shape[1:], self.values, self.dtype)

    def __mul__(self, scale):
        return Array(self.shape, tuple(value * scale for value in self.values), self.dtype)

    def astype(self, dtype):
        return Array(self.shape, tuple(int(value) for value in self.values), dtype)

    def tobytes(self):
        assert self.dtype == "uint8" and len(self.shape) == 3
        return bytes(self.values) * (self.shape[0] * self.shape[1])


class Tokenizer:
    model_max_length = 77

    def __init__(self, backend, index):
        self.backend, self.index = backend, index

    def __call__(self, text, **kwargs):
        self.backend.token_checks.append((self.index, text, kwargs))
        if self.backend.token_error:
            raise self.backend.token_error
        count = self.backend.token_counts.get((self.index, text), len(text.split()) + 2)
        return {"input_ids": list(range(count))}


class Generator:
    def __init__(self, backend, device):
        assert device == "cpu"
        self.backend, self.device, self.seed = backend, device, None
        backend.generators.append(self)

    def manual_seed(self, seed):
        self.seed = seed
        return self


class Backend:
    def __init__(self):
        self.events, self.token_checks, self.generators, self.calls = [], [], [], []
        self.available = {"mps": True, "cuda": True}
        self.token_counts = {}
        self.token_error = self.load_error = self.infer_error = self.sync_error = None
        self.output_shape = self.output_values = None
        self.output_dtype, self.output_kind = "float", "normal"
        self.entered_event = self.hold_event = None
        self.active = False
        self.tokenizer, self.tokenizer_2 = Tokenizer(self, 1), Tokenizer(self, 2)
        self.vae = SimpleNamespace(
            register_to_config=lambda **kwargs: self.events.append(("vae", kwargs)),
            enable_tiling=lambda: self.events.append("vae_tiling"),
        )
        self.torch = SimpleNamespace(
            float16="fp16", float32="fp32", inference_mode=self.inference_mode,
            Generator=lambda *, device: Generator(self, device),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: self.available["mps"])),
            mps=SimpleNamespace(synchronize=lambda: self.synchronize("mps")),
            cuda=SimpleNamespace(is_available=lambda: self.available["cuda"], synchronize=lambda: self.synchronize("cuda")),
        )
        self.numpy = SimpleNamespace(
            ndarray=Array, floating="float", uint8="uint8",
            issubdtype=lambda dtype, kind: dtype == kind,
            isfinite=lambda value: SimpleNamespace(all=lambda: all(math.isfinite(item) for item in value.values)),
            rint=lambda value: Array(value.shape, tuple(round(item) for item in value.values), value.dtype),
        )

    def dependencies(self):
        self.events.append("imports")
        return module._Dependencies(
            self.torch, self.numpy, SimpleNamespace(from_pretrained=self.load),
            SimpleNamespace(from_pretrained=self.load_scheduler),
        )

    def load(self, path, **kwargs):
        self.events.append(("load", path, kwargs))
        if self.load_error:
            raise self.load_error
        return self

    def load_scheduler(self, path, **kwargs):
        self.events.append(("scheduler", path, kwargs))
        return "verified-euler"

    def set_progress_bar_config(self, **kwargs):
        self.events.append(("progress", kwargs))

    def to(self, device):
        self.events.append(("pipeline_to", device))
        return self

    def synchronize(self, device):
        self.events.append(("sync", device))
        if self.sync_error:
            raise self.sync_error

    @contextmanager
    def inference_mode(self):
        assert not self.active
        self.active = True
        self.events.append("inference_enter")
        try:
            yield
        finally:
            self.active = False
            self.events.append("inference_exit")

    def __call__(self, **kwargs):
        assert self.active
        self.events.append("infer")
        self.calls.append(dict(kwargs, image=kwargs["image"].copy(), mask_image=kwargs["mask_image"].copy()))
        if self.entered_event:
            self.entered_event.set()
            assert self.hold_event.wait(5)
        if self.infer_error:
            raise self.infer_error
        if self.output_kind == "missing":
            return object()
        if self.output_kind == "not_array":
            return SimpleNamespace(images=[])
        return SimpleNamespace(images=Array(
            self.output_shape or (1, kwargs["height"], kwargs["width"], 3),
            (0.2, 0.4, 0.6) if self.output_values is None else self.output_values,
            self.output_dtype,
        ))


def setup(tmp_path, monkeypatch, *, device="mps", approved=True, size=(512, 512), partial=True):
    backend = Backend()
    directory = tmp_path / "models" / "verified-sdxl"
    directory.mkdir(parents=True)
    def verify(path):
        assert path == directory
        backend.events.append("verified")
        return path
    monkeypatch.setattr(module, "_verify_model_directory", verify)
    monkeypatch.setattr(module, "_load_dependencies", backend.dependencies)
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    assets, project = LocalAssetStore(tmp_path / "assets"), uuid4()
    approvals = ProductApprovalStore(tmp_path / "approvals.sqlite3", assets)
    product = Image.new("RGBA", (40, 80))
    product.paste((31, 101, 177, 255), (10, 10, 30, 70))
    if partial:
        product.paste((31, 101, 177, 128), (9, 10, 10, 70))
    source = assets.put_image(product, project_id=project)
    visual = VisualBrief(
        concept="host-provided concept", style_preset_id="bright-ad", style_preset_version="1",
        layout_template_id="product-right", layout_template_version="1",
        generation_width=size[0], generation_height=size[1],
        product_box=Rect(x=.58, y=.2, width=.32, height=.6),
        text_safe_zone=Rect(x=.05, y=.05, width=.4, height=.6),
    )
    candidate = approvals.prepare(project, source, source, visual)
    if approved:
        approvals.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    request = BackgroundRequest(
        source=source, product_rgba=candidate.product_rgba, preserve_mask=candidate.preserve_mask,
        visual=visual, seed=35, pipeline_profile_id=module.PROFILE_ID,
        pipeline_revision=module.PIPELINE_REVISION,
        image_prompt=ImagePrompt(positive="bright flowers on a pale table", negative="letters watermark",
                                 prompt_version="host-1", source_revision=1),
    )
    return backend, assets, approvals, project, request, module.SDXLBackgroundProcessor(
        assets, approvals, directory, device=device,
    )


def test_fixed_local_loader_and_pipeline_settings(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    processor.validate_request(request)
    assert backend.events == []
    processor(request, project)
    assert backend.events[:2] == ["verified", "imports"]
    assert ("load", str(processor.model_directory), {
        "local_files_only": True, "use_safetensors": True, "variant": "fp16",
        "dtype": "fp16", "add_watermarker": False,
    }) in backend.events
    assert ("scheduler", str(processor.model_directory), {
        "subfolder": "scheduler", "local_files_only": True,
    }) in backend.events
    assert backend.scheduler == "verified-euler"
    assert ("vae", {"force_upcast": True}) in backend.events
    assert "vae_tiling" in backend.events and ("pipeline_to", "mps") in backend.events
    call = backend.calls[0]
    assert {key: call[key] for key in (
        "height", "width", "num_inference_steps", "guidance_scale", "strength", "num_images_per_prompt",
        "output_type", "return_dict",
    )} == {"height": 512, "width": 512, "num_inference_steps": 20, "guidance_scale": 7.5,
           "strength": .99, "num_images_per_prompt": 1, "output_type": "np", "return_dict": True}
    assert call["prompt"] == request.image_prompt.positive
    assert call["negative_prompt"] == request.image_prompt.negative
    assert backend.generators[0].device == "cpu" and backend.generators[0].seed == request.seed
    assert backend.events[-1] == ("sync", "mps")


def test_mask_direction_safe_zone_and_original_product_restoration(tmp_path, monkeypatch):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch)
    result = processor(request, project)
    safe = module._safe_bounds(request.visual.text_safe_zone, (512, 512))
    assert result.product_rgba == request.product_rgba
    with assets.read(request.preserve_mask, project_id=project) as keep:
        expected = ImageChops.invert(keep.convert("L"))
        expected.paste(0, safe)
        assert backend.calls[0]["mask_image"].tobytes() == expected.tobytes()
    with assets.read(request.product_rgba, project_id=project) as product:
        with assets.read(result.composed_preview, project_id=project) as preview:
            integrity = inspect_composite(preview, product)
            assert integrity.opaque_pixels > 0 and integrity.mismatched_pixels == 0
            assert integrity.partial_alpha_pixels > 0
            assert preview.crop(safe).convert("RGB").getextrema() == ((250, 250),) * 3
        bounds = product.getchannel("A").getbbox()
        center = ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
        assert backend.calls[0]["image"].getpixel(center) == product.getpixel(center)[:3]
    with assets.read(result.scene_underlay, project_id=project) as scene:
        assert scene.getpixel((0, 0)) == (51, 102, 153)
        assert scene.crop(safe).getextrema() == ((250, 250),) * 3
    assert "GENERATION_SAFETY_NOT_ASSESSED" in result.warnings
    assert "MANUAL_QUALITY_REVIEW_REQUIRED" in result.warnings
    assert "PARTIAL_ALPHA_REQUIRES_VISUAL_REVIEW" in result.warnings
    assert "GENERATED_SCENE_NOT_CLEAN_PLATE" in result.warnings


def test_both_prompts_and_both_tokenizers_are_checked_without_truncation(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    processor(request, project)
    assert {(index, text) for index, text, _ in backend.token_checks} == {
        (index, text) for index in (1, 2) for text in (request.image_prompt.positive, request.image_prompt.negative)
    }
    assert all(kwargs == {"add_special_tokens": True, "truncation": False, "padding": False,
                          "return_attention_mask": False, "verbose": False}
               for _, _, kwargs in backend.token_checks)


@pytest.mark.parametrize("index,field", [(1, "positive"), (1, "negative"), (2, "positive"), (2, "negative")])
def test_overlong_prompt_never_calls_model_or_echoes_text(tmp_path, monkeypatch, capsys, index, field):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.token_counts[(index, getattr(request.image_prompt, field))] = 78
    with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
        processor(request, project)
    assert backend.calls == [] and backend.generators == []
    assert backend.events[-1] == ("sync", "mps")
    assert capsys.readouterr() == ("", "")


def test_exact_token_limit_and_new_cpu_generator_per_request(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    for index in (1, 2):
        for text in (request.image_prompt.positive, request.image_prompt.negative):
            backend.token_counts[(index, text)] = 77
    processor(request, project)
    processor(request.model_copy(update={"seed": 81}), project)
    assert backend.events.count("verified") == backend.events.count("imports") == 1
    assert [generator.seed for generator in backend.generators] == [35, 81]
    assert backend.generators[0] is not backend.generators[1]


def test_unapproved_foreign_and_tampered_assets_never_load_model(tmp_path, monkeypatch):
    backend, assets, approvals, project, request, processor = setup(tmp_path, monkeypatch, approved=False)
    with pytest.raises(GatewayError) as error:
        processor(request, project)
    assert error.value.code == "PRODUCT_APPROVAL_REQUIRED"
    candidate = approvals.prepare(project, request.source, request.source, request.visual)
    approvals.approve(candidate.candidate_id, project, candidate.candidate_hash, confirmed=True)
    request = request.model_copy(update={"product_rgba": candidate.product_rgba, "preserve_mask": candidate.preserve_mask})
    with pytest.raises(GatewayError) as error:
        processor(request, uuid4())
    assert error.value.code == "PRODUCT_APPROVAL_REQUIRED"
    path = assets.files_dir / project.hex / f"{request.product_rgba.asset_id.hex}.png"
    path.write_bytes(b"tampered")
    with pytest.raises(GatewayError):
        processor(request, project)
    assert backend.events == []


def test_nonbinary_mask_rejected_even_if_approval_boundary_is_stubbed(tmp_path, monkeypatch):
    backend, assets, approvals, project, request, processor = setup(tmp_path, monkeypatch)
    mask = assets.put_image(Image.new("L", (512, 512), 127), project_id=project)
    request = request.model_copy(update={"preserve_mask": mask})
    monkeypatch.setattr(approvals, "require_approved", lambda *_: None)
    with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
        processor(request, project)
    assert backend.events == []


@pytest.mark.parametrize("field,value,code", [
    ("pipeline_profile_id", "private/model", "IMAGE_PROFILE_NOT_FOUND"),
    ("pipeline_revision", "main", "IMAGE_PROFILE_NOT_FOUND"),
    ("image_prompt", None, "INVALID_IMAGE_REQUEST"),
    ("kind", "cutout", "INVALID_IMAGE_REQUEST"),
])
def test_fixed_profile_and_required_prompt(tmp_path, monkeypatch, field, value, code):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    with pytest.raises(GatewayError, match=f"^{code}$"):
        processor(request.model_copy(update={field: value}), project)
    assert backend.events == []


@pytest.mark.parametrize("field,value", [
    ("style_preset_id", "other"), ("style_preset_version", "2"),
    ("layout_template_id", "other"), ("layout_template_version", "2"),
])
def test_visual_versions_are_fixed(tmp_path, monkeypatch, field, value):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    changed = request.model_copy(update={"visual": request.visual.model_copy(update={field: value})})
    with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
        processor(changed, project)
    assert backend.events == []


def test_allowed_rectangular_canvas_and_disallowed_working_size(tmp_path, monkeypatch):
    _, _, _, project, request, processor = setup(tmp_path, monkeypatch, size=(512, 768))
    assert processor(request, project).scene_underlay.height == 768
    bad = request.model_copy(update={
        "visual": request.visual.model_copy(update={"generation_width": 640}),
        "product_rgba": request.product_rgba.model_copy(update={"width": 640}),
        "preserve_mask": request.preserve_mask.model_copy(update={"width": 640}),
    })
    with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
        processor.validate_request(bad)


@pytest.mark.parametrize("device,dtype", [("cpu", "fp32"), ("cuda", "fp16")])
def test_explicit_device_and_dtype(tmp_path, monkeypatch, device, dtype):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    processor(request, project)
    assert ("pipeline_to", device) in backend.events
    assert next(event[2]["dtype"] for event in backend.events if isinstance(event, tuple) and event[0] == "load") == dtype
    assert backend.generators[0].device == "cpu"


@pytest.mark.parametrize("device", ["mps", "cuda"])
def test_unavailable_device_has_no_automatic_cpu_retry(tmp_path, monkeypatch, device):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    backend.available[device] = False
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert not any(isinstance(event, tuple) and event[0] == "load" for event in backend.events)


def test_fallback_missing_dependencies_and_failed_verification_rejected(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK")
    def fail_dependencies():
        raise ImportError("private path")
    monkeypatch.setattr(module, "_load_dependencies", fail_dependencies)
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    backend.events.clear()
    def fail_verification(path):
        raise GatewayError("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE")
    monkeypatch.setattr(module, "_verify_model_directory", fail_verification)
    monkeypatch.setattr(module, "_load_dependencies", backend.dependencies)
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert backend.events == []


@pytest.mark.parametrize("field,value", [
    ("output_shape", (1, 512, 512, 4)), ("output_shape", (2, 512, 512, 3)),
    ("output_shape", (1, 768, 512, 3)), ("output_dtype", "int"),
    ("output_values", (float("nan"), .2, .3)), ("output_values", (float("inf"), .2, .3)),
    ("output_values", (0.0, 0.0, 0.0)),
    ("output_values", (-0.001, .2, .3)), ("output_values", (1.001, .2, .3)),
    ("output_kind", "missing"), ("output_kind", "not_array"),
])
def test_invalid_float_output_is_rejected_before_pil_or_publication(tmp_path, monkeypatch, field, value):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch)
    setattr(backend, field, value)
    before = set(assets.files_dir.rglob("*.png"))
    with pytest.raises(GatewayError, match="^IMAGE_RESULT_INVALID$"):
        processor(request, project)
    assert backend.events[-1] == ("sync", "mps")
    assert set(assets.files_dir.rglob("*.png")) == before


def test_nonzero_dark_output_has_no_arbitrary_brightness_threshold(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.output_values = (0.0001, 0.0, 0.0)
    result = processor(request, project)
    assert "MANUAL_QUALITY_REVIEW_REQUIRED" in result.warnings


@pytest.mark.parametrize("failure,code", [
    (RuntimeError("private prompt backend error"), "IMAGE_PROCESSING_FAILED"),
    (RuntimeError("MPS out of memory private prompt"), "IMAGE_OUT_OF_MEMORY"),
    (MemoryError("private path"), "IMAGE_OUT_OF_MEMORY"),
])
def test_inference_failures_are_sanitized_and_synchronized(tmp_path, monkeypatch, failure, code):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.infer_error = failure
    before = set(assets.files_dir.rglob("*.png"))
    with pytest.raises(GatewayError, match=f"^{code}$"):
        processor(request, project)
    assert backend.events[-1] == ("sync", "mps")
    assert set(assets.files_dir.rglob("*.png")) == before


def test_load_tokenizer_and_sync_errors_are_safe(tmp_path, monkeypatch):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.load_error = RuntimeError("private directory")
    with pytest.raises(GatewayError, match="^MODEL_UNAVAILABLE$"):
        processor(request, project)
    assert backend.events[-1] == ("sync", "mps")
    backend.load_error = None
    backend.token_error = ValueError("private prompt")
    with pytest.raises(GatewayError, match="^INVALID_IMAGE_REQUEST$"):
        processor(request, project)
    assert backend.calls == []
    backend.token_error = None
    backend.sync_error = RuntimeError("private GPU state")
    before = set(assets.files_dir.rglob("*.png"))
    with pytest.raises(GatewayError, match="^IMAGE_PROCESSING_FAILED$"):
        processor(request, project)
    assert set(assets.files_dir.rglob("*.png")) == before


def test_concurrent_calls_load_once_and_serialize_inference(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    backend.entered_event, backend.hold_event = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(processor, request, project)
        assert backend.entered_event.wait(3)
        second = pool.submit(processor, request, project)
        backend.hold_event.set()
        assert first.result(timeout=5).product_rgba == request.product_rgba
        assert second.result(timeout=5).product_rgba == request.product_rgba
    assert backend.events.count("verified") == 1 and len(backend.calls) == 2


def test_import_is_independent_of_optional_ai_packages():
    code = ("import json,sys; import beauty_ai.sdxl_processor; "
            "print(json.dumps([name for name in ('torch','diffusers','numpy','transformers') if name in sys.modules]))")
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_installed_vae_control_api_without_pretrained_weights(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("diffusers")
    from diffusers import AutoencoderKL

    # A tiny, randomly initialized CPU VAE exercises the installed public API.
    # No checkpoint is loaded, downloaded, moved to GPU or used for inference.
    vae = AutoencoderKL(
        in_channels=3, out_channels=3,
        down_block_types=("DownEncoderBlock2D",), up_block_types=("UpDecoderBlock2D",),
        block_out_channels=(4,), layers_per_block=1, latent_channels=2,
        norm_num_groups=1, sample_size=8, mid_block_add_attention=False, force_upcast=False,
    )
    assert not vae.use_tiling
    backend, _, _, _, _, processor = setup(tmp_path, monkeypatch, device="cpu")
    # The pipeline double deliberately has no enable_vae_tiling method. Actual
    # VAE methods, rather than invented pipeline methods, must configure it.
    backend.vae = vae
    processor._ensure_loaded()
    assert vae.use_tiling is True
    assert vae.config.force_upcast is True


def test_installed_pipeline_accepts_dtype_before_any_weight_load(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("diffusers")
    from diffusers import StableDiffusionXLInpaintPipeline

    class ReachedLocalConfig(Exception):
        pass

    def stop_before_config(cls, *args, **kwargs):
        raise ReachedLocalConfig

    # The real loader processes dtype before reaching this seam. Stop before
    # config/weights I/O; -W error also catches deprecated argument regressions.
    monkeypatch.setattr(StableDiffusionXLInpaintPipeline, "load_config", classmethod(stop_before_config))
    with pytest.raises(ReachedLocalConfig):
        StableDiffusionXLInpaintPipeline.from_pretrained(
            str(tmp_path), local_files_only=True, use_safetensors=True,
            variant="fp16", dtype=torch.float16, add_watermarker=False,
        )


@pytest.mark.parametrize("device", ["mps", "cuda", "cpu"])
def test_execution_trace_cold_and_hot_background_preserve_outputs_and_device_facts(tmp_path, monkeypatch, device):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch, device=device)
    baseline_files = len(list(assets.files_dir.rglob("*.png")))
    memory_calls = []
    def memory(value):
        assert backend.events[-1] == ("sync", device)
        assert len(list(assets.files_dir.rglob("*.png"))) >= baseline_files + 2
        memory_calls.append(value)
        return value
    backend.torch.mps.current_allocated_memory = lambda: memory(1024)
    backend.torch.mps.driver_allocated_memory = lambda: memory(2048)
    backend.torch.cuda.memory_allocated = lambda: memory(1024)
    backend.torch.cuda.memory_reserved = lambda: memory(2048)
    observations, results = [], []
    for _ in range(2):
        with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
            results.append(processor(request, project))
        observations.append(recorder.finish(outcome="succeeded"))
    for index, observation in enumerate(observations):
        assert observation.backend.model_dump() == {
            "device": device, "model_revision": "462165984030d82259a11f4367a4eed129e94a7b", "reused": bool(index),
        }
        names = [item.name for item in observation.phases]
        assert set(names) == {"preprocess", "model_inference", "composite", "asset_store"} | (
            {"model_load"} if index == 0 else set())
        assert all(item.outcome == "completed" and item.elapsed_ms >= 0 for item in observation.phases)
        assert [item.model_dump() for item in observation.synchronizations] == (
            [] if device == "cpu" else [{"device": device, "success": True}] * (2 - index))
        snapshot, = observation.memory_snapshots
        assert snapshot.basis == "post_operation_process_snapshot"
        assert snapshot.allocated_bytes == (None if device == "cpu" else 1024)
        assert snapshot.driver_allocated_bytes == (2048 if device == "mps" else None)
        assert snapshot.reserved_bytes == (2048 if device == "cuda" else None)
        assert snapshot.unavailable_reason == ("NOT_GPU_DEVICE" if device == "cpu" else None)
        serialized = observation.model_dump_json()
        assert request.image_prompt.positive not in serialized
        assert request.image_prompt.negative not in serialized
        assert request.visual.concept not in serialized
        assert str(processor.model_directory) not in serialized
        assert str(request.source.asset_id) not in serialized
    assert memory_calls == ([] if device == "cpu" else [1024, 2048] * 2)
    assert len(backend.calls) == 2 and all(call["generator"].seed == request.seed for call in backend.calls)
    with assets.read(results[0].composed_preview, project_id=project) as cold:
        with assets.read(results[1].composed_preview, project_id=project) as warm:
            assert cold.tobytes() == warm.tobytes()


@pytest.mark.parametrize("failure,failed_phases,code", [
    ("approval", ["preprocess"], "PRODUCT_APPROVAL_REQUIRED"),
    ("verification", ["model_load"], "MODEL_UNAVAILABLE"),
    ("load_sync", ["model_load"], "IMAGE_PROCESSING_FAILED"),
    ("tokens", ["preprocess", "model_inference"], "INVALID_IMAGE_REQUEST"),
    ("inference", ["model_inference"], "IMAGE_PROCESSING_FAILED"),
    ("synchronize", ["model_inference"], "IMAGE_PROCESSING_FAILED"),
    ("pixels", ["model_inference"], "IMAGE_RESULT_INVALID"),
    ("composite", ["composite"], "IMAGE_QUALITY_REJECTED"),
    ("storage", ["asset_store"], "ASSET_STORAGE_FAILED"),
])
def test_execution_trace_failing_background_stages_do_not_expose_private_details(tmp_path, monkeypatch, failure, failed_phases, code):
    backend, assets, _, project, request, processor = setup(tmp_path, monkeypatch, approved=failure != "approval")
    if failure == "verification":
        def fail_verify(path):
            raise GatewayError("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE")
        monkeypatch.setattr(module, "_verify_model_directory", fail_verify)
    elif failure == "load_sync":
        backend.sync_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "tokens":
        backend.token_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "inference":
        backend.infer_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "synchronize":
        processor._ensure_loaded()
        backend.sync_error = RuntimeError("PRIVATE_BACKEND_DETAIL")
    elif failure == "pixels":
        backend.output_values = (float("nan"), .2, .3)
    elif failure == "composite":
        monkeypatch.setattr(module, "inspect_composite", lambda *args: SimpleNamespace(mismatched_pixels=1))
    elif failure == "storage":
        def fail_store(*args, **kwargs):
            raise GatewayError("ASSET_STORAGE_FAILED", "ASSET_STORAGE_FAILED")
        monkeypatch.setattr(assets, "put_image", fail_store)
    before = set(assets.files_dir.rglob("*.png"))
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        with pytest.raises(GatewayError) as error:
            processor(request, project)
    assert error.value.code == code
    observation = recorder.finish(outcome="failed")
    assert [item.name for item in observation.phases if item.outcome == "failed"] == failed_phases
    assert [item.success for item in observation.synchronizations] == {
        "approval": [], "verification": [], "load_sync": [False],
        "tokens": [True, True], "inference": [True, True], "synchronize": [False],
        "pixels": [True, True], "composite": [True, True], "storage": [True, True],
    }[failure]
    assert set(assets.files_dir.rglob("*.png")) == before
    if failure in {"approval", "verification", "load_sync"}:
        assert observation.memory_snapshots[0].unavailable_reason == "BACKEND_NOT_LOADED"
    serialized = observation.model_dump_json()
    assert "PRIVATE_BACKEND_DETAIL" not in serialized
    assert request.image_prompt.positive not in serialized
    assert request.image_prompt.negative not in serialized
    assert str(processor.model_directory) not in serialized


@pytest.mark.parametrize("failure,reason", [
    ("missing", "MEMORY_API_UNAVAILABLE"), ("error", "MEMORY_SNAPSHOT_FAILED"),
    ("boolean", "INVALID_MEMORY_VALUE"), ("overflow", "INVALID_MEMORY_VALUE"),
])
def test_memory_observation_failure_does_not_fail_background_or_hide_available_value(tmp_path, monkeypatch, failure, reason):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    def failed_memory():
        raise RuntimeError("PRIVATE_MEMORY_DETAIL")
    if failure == "error":
        backend.torch.mps.current_allocated_memory = failed_memory
    elif failure == "boolean":
        backend.torch.mps.current_allocated_memory = lambda: True
    elif failure == "overflow":
        backend.torch.mps.current_allocated_memory = lambda: 2**63
    backend.torch.mps.driver_allocated_memory = lambda: 8192
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        result = processor(request, project)
    assert result.product_rgba == request.product_rgba
    observation = recorder.finish(outcome="succeeded")
    snapshot, = observation.memory_snapshots
    assert snapshot.allocated_bytes is None and snapshot.driver_allocated_bytes == 8192
    assert snapshot.unavailable_reason == reason
    assert "PRIVATE_MEMORY_DETAIL" not in observation.model_dump_json()


def test_untraced_background_skips_memory_and_recorder_error_preserves_result(tmp_path, monkeypatch):
    backend, _, _, project, request, processor = setup(tmp_path, monkeypatch)
    calls = []
    def memory():
        calls.append("query")
        return 123
    backend.torch.mps.current_allocated_memory = memory
    backend.torch.mps.driver_allocated_memory = memory
    assert processor(request, project).product_rgba == request.product_rgba
    assert calls == []
    def failed_record(*args, **kwargs):
        raise RuntimeError("PRIVATE_RECORDER_DETAIL")
    monkeypatch.setattr(module, "record_memory", failed_record)
    with recording(job_id=uuid4(), project_id=project, request=request) as recorder:
        assert processor(request, project).product_rgba == request.product_rgba
    assert calls == ["query", "query"]
    assert recorder.finish(outcome="succeeded").memory_snapshots == []
