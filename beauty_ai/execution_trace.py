"""Bounded job observations, never prompt logging or GPU performance claims."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import math
import re
import sys
import threading
import time
from typing import Annotated, Iterator, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ImageRequest, content_hash


Device = Literal["cpu", "mps", "cuda"]
PhaseName = Literal["model_load", "preprocess", "model_inference", "composite", "asset_store", "result_validation"]
Outcome = Literal["completed", "failed"]
MemoryReason = Literal["BACKEND_NOT_LOADED", "NOT_GPU_DEVICE", "MEMORY_API_UNAVAILABLE",
                       "MEMORY_SNAPSHOT_FAILED", "INVALID_MEMORY_VALUE"]
QueueReason = Literal["CLAIM_TIME_UNAVAILABLE", "CLOCK_MOVED_BACKWARD", "INVALID_WALL_CLOCK"]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")]
ByteCount = Annotated[int, Field(ge=0, le=2**63 - 1)]
_PHASES = frozenset(("model_load", "preprocess", "model_inference", "composite", "asset_store", "result_validation"))
_PACKAGES = ("torch", "torchvision", "transformers", "diffusers", "accelerate", "timm",
             "numpy", "kornia", "einops", "PIL", "pydantic")


class _Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PhaseMeasurement(_Observation):
    name: PhaseName
    elapsed_ms: Duration | None
    outcome: Outcome
    unavailable_reason: Literal["INVALID_MONOTONIC_CLOCK"] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if (self.elapsed_ms is None) != (self.unavailable_reason is not None):
            raise ValueError("Invalid phase observation")
        return self


class BackendObservation(_Observation):
    device: Device
    model_revision: Identifier
    reused: bool


class Synchronization(_Observation):
    device: Device
    success: bool


class MemorySnapshot(_Observation):
    device: Device
    allocated_bytes: ByteCount | None = None
    driver_allocated_bytes: ByteCount | None = None
    reserved_bytes: ByteCount | None = None
    unavailable_reason: MemoryReason | None = None
    basis: Literal["post_operation_process_snapshot"] = "post_operation_process_snapshot"

    @model_validator(mode="after")
    def coherent(self):
        values = (self.allocated_bytes, self.driver_allocated_bytes, self.reserved_bytes)
        relevant = ((self.allocated_bytes, self.driver_allocated_bytes) if self.device == "mps"
                    else (self.allocated_bytes, self.reserved_bytes) if self.device == "cuda"
                    else (None,))
        if any(value is None for value in relevant) and self.unavailable_reason is None:
            raise ValueError("Missing memory observation reason")
        if ((self.device == "mps" and self.reserved_bytes is not None)
                or (self.device == "cuda" and self.driver_allocated_bytes is not None)
                or (self.device == "cpu" and any(value is not None for value in values))):
            raise ValueError("Memory observation does not match device")
        return self


class ExecutionRecord(_Observation):
    schema_version: Literal[1] = 1
    job_id: UUID
    project_id: UUID
    request_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    pipeline_profile_id: Identifier | None
    pipeline_revision: Identifier | None
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] | None
    outcome: Literal["succeeded", "failed"]
    clock_basis: Literal["worker_monotonic_wall_clock"] = "worker_monotonic_wall_clock"
    worker_elapsed_ms: Duration | None
    worker_unavailable_reason: Literal["INVALID_MONOTONIC_CLOCK"] | None
    processor_elapsed_ms: Duration | None
    processor_outcome: Outcome | None
    processor_unavailable_reason: Literal["NOT_MEASURED", "INVALID_MONOTONIC_CLOCK"] | None
    queue_elapsed_ms: Duration | None
    queue_clock_basis: Literal["server_wall_clock"] = "server_wall_clock"
    queue_unavailable_reason: QueueReason | None
    phases: Annotated[list[PhaseMeasurement], Field(max_length=64)]
    backend: BackendObservation | None
    synchronizations: Annotated[list[Synchronization], Field(max_length=64)]
    memory_snapshots: Annotated[list[MemorySnapshot], Field(max_length=16)]
    python_version: Identifier
    loaded_package_versions: dict[str, Identifier]
    observations_truncated: bool = False

    @model_validator(mode="after")
    def coherent(self):
        if ((self.worker_elapsed_ms is None) != (self.worker_unavailable_reason is not None)
                or (self.processor_elapsed_ms is None) != (self.processor_unavailable_reason is not None)
                or (self.queue_elapsed_ms is None) != (self.queue_unavailable_reason is not None)):
            raise ValueError("Invalid execution measurement")
        if (self.processor_outcome is None) != (self.processor_unavailable_reason == "NOT_MEASURED"):
            raise ValueError("Invalid processor observation")
        if set(self.loaded_package_versions) - set(_PACKAGES):
            raise ValueError("Unknown package observation")
        return self


def request_metadata(request: ImageRequest) -> dict:
    """Only bounded identifiers, never arbitrary user text or asset references."""
    def identifier(value):
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}", value) else None
    return {"pipeline_profile_id": identifier(request.pipeline_profile_id),
            "pipeline_revision": identifier(request.pipeline_revision),
            "seed": getattr(request, "seed", None)}


def queue_duration(created_at: str | None, claimed_at: str | None) -> tuple[float | None, QueueReason | None]:
    if created_at is None or claimed_at is None:
        return None, "CLAIM_TIME_UNAVAILABLE"
    try:
        created, claimed = datetime.fromisoformat(created_at), datetime.fromisoformat(claimed_at)
        if created.utcoffset() is None or claimed.utcoffset() is None:
            return None, "INVALID_WALL_CLOCK"
        elapsed = (claimed - created).total_seconds() * 1000
        if not math.isfinite(elapsed):
            return None, "INVALID_WALL_CLOCK"
        return (None, "CLOCK_MOVED_BACKWARD") if elapsed < 0 else (elapsed, None)
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID_WALL_CLOCK"


def _duration(start: float) -> float | None:
    elapsed = (time.monotonic() - start) * 1000
    return elapsed if math.isfinite(elapsed) and elapsed >= 0 else None


class ExecutionRecorder:
    def __init__(self, job_id: UUID, project_id: UUID, request: ImageRequest, *,
                 created_at: str | None = None, claimed_at: str | None = None):
        self.identity = {"job_id": job_id, "project_id": project_id,
                         "request_hash": content_hash(request), **request_metadata(request)}
        self.started = time.monotonic()
        self.thread_id = threading.get_ident()
        self.queue_elapsed, self.queue_reason = queue_duration(created_at, claimed_at)
        self.phases: list[PhaseMeasurement] = []
        self.backend: BackendObservation | None = None
        self.synchronizations: list[Synchronization] = []
        self.memory_snapshots: list[MemorySnapshot] = []
        self.processor_elapsed = None
        self.processor_outcome = None
        self.processor_reason = "NOT_MEASURED"
        self.truncated = False

    def append(self, collection: list, value, limit: int) -> None:
        if len(collection) < limit:
            collection.append(value)
        else:
            self.truncated = True

    @contextmanager
    def processor(self) -> Iterator[None]:
        start, outcome = time.monotonic(), "failed"
        try:
            yield
            outcome = "completed"
        finally:
            self.processor_elapsed = _duration(start)
            self.processor_outcome = outcome
            self.processor_reason = None if self.processor_elapsed is not None else "INVALID_MONOTONIC_CLOCK"

    def finish(self, *, outcome: Literal["succeeded", "failed"]) -> ExecutionRecord:
        elapsed = _duration(self.started)
        versions = {}
        for name in _PACKAGES:
            module = sys.modules.get(name)
            version = vars(module).get("__version__") if module is not None and hasattr(module, "__dict__") else None
            if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}", version):
                versions[name] = str(version)
        return ExecutionRecord(
            **self.identity, outcome=outcome, worker_elapsed_ms=elapsed,
            worker_unavailable_reason=None if elapsed is not None else "INVALID_MONOTONIC_CLOCK",
            processor_elapsed_ms=self.processor_elapsed, processor_outcome=self.processor_outcome,
            processor_unavailable_reason=self.processor_reason,
            queue_elapsed_ms=self.queue_elapsed, queue_unavailable_reason=self.queue_reason,
            phases=list(self.phases), backend=self.backend,
            synchronizations=list(self.synchronizations), memory_snapshots=list(self.memory_snapshots),
            python_version=".".join(map(str, sys.version_info[:3])), loaded_package_versions=versions,
            observations_truncated=self.truncated,
        )


_RECORDER: ContextVar[ExecutionRecorder | None] = ContextVar("beauty_execution_recorder", default=None)


def _active() -> ExecutionRecorder | None:
    recorder = _RECORDER.get()
    return recorder if recorder is not None and recorder.thread_id == threading.get_ident() else None


def is_recording() -> bool:
    return _active() is not None


@contextmanager
def recording(*, job_id: UUID, project_id: UUID, request: ImageRequest,
              created_at: str | None = None, claimed_at: str | None = None) -> Iterator[ExecutionRecorder]:
    recorder = ExecutionRecorder(job_id, project_id, request, created_at=created_at, claimed_at=claimed_at)
    token = _RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _RECORDER.reset(token)


@contextmanager
def phase(name: PhaseName) -> Iterator[None]:
    recorder = _active()
    if recorder is None:
        yield
        return
    if name not in _PHASES:
        raise ValueError("Unknown execution phase")
    start, outcome = time.monotonic(), "failed"
    try:
        yield
        outcome = "completed"
    finally:
        elapsed = _duration(start)
        recorder.append(recorder.phases, PhaseMeasurement(
            name=name, elapsed_ms=elapsed, outcome=outcome,
            unavailable_reason=None if elapsed is not None else "INVALID_MONOTONIC_CLOCK"), 64)


def set_backend(device: Device, model_revision: str, reused: bool) -> None:
    recorder = _active()
    if recorder is not None:
        recorder.backend = BackendObservation(device=device, model_revision=model_revision, reused=reused)


def device_synchronized(device: Device, success: bool = True) -> None:
    recorder = _active()
    if recorder is not None:
        recorder.append(recorder.synchronizations, Synchronization(device=device, success=success), 64)


def record_memory(device: Device, allocated_bytes: int | None = None,
                  driver_allocated_bytes: int | None = None, reserved_bytes: int | None = None,
                  unavailable_reason: MemoryReason | None = None) -> None:
    recorder = _active()
    if recorder is None:
        return
    try:
        snapshot = MemorySnapshot(device=device, allocated_bytes=allocated_bytes,
                                  driver_allocated_bytes=driver_allocated_bytes, reserved_bytes=reserved_bytes,
                                  unavailable_reason=unavailable_reason)
    except ValueError:
        snapshot = MemorySnapshot(device=device, unavailable_reason="INVALID_MEMORY_VALUE")
    recorder.append(recorder.memory_snapshots, snapshot, 16)
