"""Tests for observation semantics without optional model imports or inference."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from beauty_ai.contracts import AssetRef, CutoutRequest, content_hash
from beauty_ai import execution_trace as trace


def request():
    return CutoutRequest(source=AssetRef(asset_id=uuid4(), sha256="a" * 64, width=8,
                                       height=8, mime_type="image/png"),
                         pipeline_profile_id="test", pipeline_revision="1")


def context(**kwargs):
    return trace.recording(job_id=uuid4(), project_id=uuid4(), request=request(), **kwargs)


def test_inactive_helpers_are_noops_even_without_valid_observations():
    assert not trace.is_recording()
    with trace.phase("arbitrary caller data"):
        trace.set_backend("unknown", "private/value", None)
        trace.device_synchronized("unknown", success="private value")
        trace.record_memory("unknown", allocated_bytes=object())
    assert not trace.is_recording()


def test_nested_context_restores_previous_and_copied_context_does_not_cross_threads():
    with context() as first:
        trace.set_backend("mps", "fixed1", False)
        with context() as second:
            trace.set_backend("cpu", "fixed2", True)
        assert trace.is_recording()
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(copied.run, trace.is_recording).result() is False
            executor.submit(copied.run, trace.set_backend, "cuda", "wrong", True).result()
        assert first.finish(outcome="succeeded").backend.device == "mps"
        assert second.finish(outcome="succeeded").backend.device == "cpu"
    assert not trace.is_recording()


def test_monotonic_processor_and_nested_spans_are_not_automatically_added(monkeypatch):
    # worker starts at 10, processor 11..19, outer phase 12..18, inner 13..15.
    times = iter([10., 11., 12., 13., 15., 18., 19., 21.])
    monkeypatch.setattr(trace.time, "monotonic", lambda: next(times))
    with context(created_at="2026-10-22T00:00:00+00:00",
                 claimed_at="2026-10-22T09:00:00.250+09:00") as recorder:
        with recorder.processor(), trace.phase("model_inference"):
            with trace.phase("preprocess"):
                pass
        packet = recorder.finish(outcome="succeeded")
    assert packet.worker_elapsed_ms == 11000
    assert packet.processor_elapsed_ms == 8000
    assert [(p.name, p.elapsed_ms) for p in packet.phases] == [
        ("preprocess", 2000), ("model_inference", 6000)]
    assert packet.queue_elapsed_ms == 250
    assert packet.queue_clock_basis == "server_wall_clock"
    assert packet.clock_basis == "worker_monotonic_wall_clock"
    assert packet.processor_outcome == "completed"
    assert trace.ExecutionRecord.model_validate_json(packet.model_dump_json()) == packet


def test_failed_spans_propagate_original_error_and_never_capture_text():
    error = RuntimeError("secret-token https://private.example /private/model")
    with context() as recorder:
        with pytest.raises(RuntimeError) as caught:
            with recorder.processor(), trace.phase("model_inference"):
                raise error
        packet = recorder.finish(outcome="failed")
    assert caught.value is error
    assert packet.processor_outcome == "failed"
    assert packet.phases[0].outcome == "failed"
    assert packet.worker_elapsed_ms >= packet.processor_elapsed_ms >= 0
    assert "secret-token" not in packet.model_dump_json()
    assert "private" not in packet.model_dump_json()
    assert not trace.is_recording()


@pytest.mark.parametrize("bad_end", [float("nan"), float("inf"), -1.])
def test_invalid_monotonic_measurement_is_unknown_not_zero(monkeypatch, bad_end):
    times = iter([0., 0., 0., bad_end, bad_end, bad_end])
    monkeypatch.setattr(trace.time, "monotonic", lambda: next(times))
    with context() as recorder:
        with recorder.processor(), trace.phase("preprocess"):
            pass
        packet = recorder.finish(outcome="succeeded")
    assert packet.worker_elapsed_ms is None
    assert packet.processor_elapsed_ms is None
    assert packet.phases[0].elapsed_ms is None
    assert packet.worker_unavailable_reason == "INVALID_MONOTONIC_CLOCK"
    assert packet.processor_unavailable_reason == "INVALID_MONOTONIC_CLOCK"
    assert packet.phases[0].unavailable_reason == "INVALID_MONOTONIC_CLOCK"


@pytest.mark.parametrize("created,claimed,expected", [
    (None, None, "CLAIM_TIME_UNAVAILABLE"),
    ("2026-10-22T00:00:01+00:00", "2026-10-22T00:00:00+00:00", "CLOCK_MOVED_BACKWARD"),
    ("2026-10-22T00:00:00", "2026-10-22T00:00:01", "INVALID_WALL_CLOCK"),
    ("private/raw", "2026-10-22T00:00:00+00:00", "INVALID_WALL_CLOCK"),
])
def test_queue_missing_invalid_or_backward_time_is_not_a_duration(created, claimed, expected):
    with context(created_at=created, claimed_at=claimed) as recorder:
        packet = recorder.finish(outcome="succeeded")
    assert packet.queue_elapsed_ms is None
    assert packet.queue_unavailable_reason == expected
    assert "private/raw" not in packet.model_dump_json()
    assert packet.processor_elapsed_ms is None
    assert packet.processor_unavailable_reason == "NOT_MEASURED"


def test_versions_only_include_loaded_allowlisted_modules_with_safe_versions(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(__version__="2.14.1"))
    monkeypatch.delitem(sys.modules, "diffusers", raising=False)
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(__version__="https://private/token"))
    monkeypatch.setitem(sys.modules, "unrelated_provider", SimpleNamespace(__version__="1.0.0"))
    with context() as recorder:
        packet = recorder.finish(outcome="succeeded")
    assert packet.loaded_package_versions["torch"] == "2.14.1"
    assert "diffusers" not in packet.loaded_package_versions
    assert "transformers" not in packet.loaded_package_versions
    assert "unrelated_provider" not in packet.loaded_package_versions
    assert packet.python_version == ".".join(map(str, sys.version_info[:3]))


def test_backend_sync_and_memory_are_explicit_process_snapshots():
    with context() as recorder:
        trace.set_backend("mps", "fixed-revision", reused=True)
        trace.device_synchronized("mps")
        trace.device_synchronized("mps", success=False)
        trace.record_memory("mps", allocated_bytes=100, driver_allocated_bytes=200)
        trace.record_memory("cuda", allocated_bytes=300, reserved_bytes=400)
        trace.record_memory("cpu", unavailable_reason="NOT_GPU_DEVICE")
        packet = recorder.finish(outcome="succeeded")
    assert packet.backend.reused is True
    assert [x.success for x in packet.synchronizations] == [True, False]
    assert packet.memory_snapshots[0].allocated_bytes == 100
    assert packet.memory_snapshots[0].reserved_bytes is None
    assert packet.memory_snapshots[1].driver_allocated_bytes is None
    assert packet.memory_snapshots[2].unavailable_reason == "NOT_GPU_DEVICE"
    assert all(x.basis == "post_operation_process_snapshot" for x in packet.memory_snapshots)


@pytest.mark.parametrize("device,values", [
    ("mps", {"allocated_bytes": -1}), ("mps", {"allocated_bytes": True}),
    ("mps", {"allocated_bytes": 2.5}), ("mps", {"allocated_bytes": 2**63}),
    ("mps", {"allocated_bytes": "secret"}), ("mps", {"reserved_bytes": 20}),
    ("cuda", {"driver_allocated_bytes": 20}), ("cpu", {"allocated_bytes": 20}),
    ("mps", {}), ("mps", {"allocated_bytes": 20}), ("cuda", {"allocated_bytes": 20}),
])
def test_invalid_memory_observation_is_sanitized_without_affecting_processing(device, values):
    with context() as recorder:
        trace.record_memory(device, **values)
        packet = recorder.finish(outcome="succeeded")
    memory = packet.memory_snapshots[0]
    assert memory.allocated_bytes is memory.reserved_bytes is memory.driver_allocated_bytes is None
    assert memory.unavailable_reason == "INVALID_MEMORY_VALUE"
    assert "secret" not in packet.model_dump_json()


def test_observation_collections_are_bounded_and_truncation_is_explicit():
    with context() as recorder:
        for _ in range(70):
            with trace.phase("preprocess"):
                pass
            trace.device_synchronized("cpu")
            trace.record_memory("cpu", unavailable_reason="NOT_GPU_DEVICE")
        packet = recorder.finish(outcome="succeeded")
    assert len(packet.phases) == len(packet.synchronizations) == 64
    assert len(packet.memory_snapshots) == 16
    assert packet.observations_truncated is True
    assert len(packet.model_dump_json().encode()) < 65536


@pytest.mark.parametrize("change", [
    {"worker_elapsed_ms": float("nan")}, {"worker_elapsed_ms": -1},
    {"worker_elapsed_ms": None}, {"processor_outcome": "completed"},
    {"queue_elapsed_ms": 0}, {"loaded_package_versions": {"secret": "1.0"}},
    {"raw_exception": "secret"}, {"phases": [{"name": "private", "elapsed_ms": 0, "outcome": "completed"}]},
])
def test_record_schema_rejects_corrupted_or_ambiguous_observations(change):
    with context() as recorder:
        packet = recorder.finish(outcome="succeeded")
    data = packet.model_dump(mode="json") | change
    with pytest.raises(ValidationError):
        trace.ExecutionRecord.model_validate_json(json.dumps(data))


def test_request_identity_and_safe_metadata_do_not_include_asset_or_user_text():
    req = request().model_copy(update={"pipeline_profile_id": "private/path/token"})
    with trace.recording(job_id=uuid4(), project_id=uuid4(), request=req) as recorder:
        packet = recorder.finish(outcome="succeeded")
    assert packet.request_hash == content_hash(req)
    assert packet.pipeline_profile_id is None
    assert str(req.source.asset_id) not in packet.model_dump_json()
    assert "private/path/token" not in packet.model_dump_json()
