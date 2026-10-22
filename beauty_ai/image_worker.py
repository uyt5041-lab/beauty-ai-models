"""One processor at a time on a single host and shared local filesystem.

The SQLite ledger is the queue. A process-lifetime file lock, rather than a lease
timeout, proves that the previous worker no longer owns execution. Processes that
target the same device must share the same canonical ledger path. This module
does not coordinate multiple hosts, terminate GPU calls, or certify image quality.
"""
from __future__ import annotations

import fcntl
import math
import os
from pathlib import Path
import sqlite3
import threading
from typing import Callable
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from .assets import LocalAssetStore
from .contracts import BackgroundResult, CutoutResult, ImageRequest, ImageResult
from .errors import GatewayError
from .job_ledger import JobLedger


Processor = Callable[[ImageRequest, UUID], ImageResult]
_RESULT_ADAPTER = TypeAdapter(ImageResult)
_SAFE_PROCESSOR_CODES = frozenset({
    "ASSET_NOT_FOUND", "ASSET_INTEGRITY_ERROR", "ASSET_STORAGE_ERROR",
    "INVALID_IMAGE", "IMAGE_TOO_LARGE", "IMAGE_PROFILE_NOT_FOUND",
    "UNSUPPORTED_PROFILE", "MODEL_UNAVAILABLE", "IMAGE_MODEL_UNAVAILABLE",
    "INVALID_IMAGE_REQUEST", "IMAGE_QUALITY_REJECTED",
    "PRODUCT_NOT_FOUND", "PRODUCT_APPROVAL_REQUIRED", "PRODUCT_APPROVAL_CONFLICT",
    "PRODUCT_STORAGE_ERROR",
})


class SingleHostWorker:
    """Run a synchronous processor under an exclusive POSIX execution lock.

    ``stop`` requests a drain of the current processor call. A timeout returns
    False while the thread and lock remain alive; it never claims cancellation.
    The service must call stop during shutdown. A non-daemon thread prevents a
    normal interpreter exit from abandoning a live operation without cleanup.
    Lock files are never unlinked: removing a locked inode would permit a second
    process to lock a new file at the same path and run concurrently.
    The processor must wait for its native/GPU work before returning; detached
    subprocesses or asynchronous device operations are not tracked by this lock.
    """

    def __init__(self, ledger: JobLedger, assets: LocalAssetStore, processor: Processor,
                 *, poll_seconds: float = 0.1):
        if not callable(processor):
            raise TypeError("Processor must be callable")
        if not math.isfinite(poll_seconds) or poll_seconds <= 0:
            raise ValueError("Polling interval must be finite and positive")
        self.ledger = ledger
        self.assets = assets
        self.processor = processor
        self.poll_seconds = poll_seconds
        self.lock_path = Path(str(ledger.path.resolve()) + ".worker.lock")
        self.fatal_error_code: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._lock_fd: int | None = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self.fatal_error_code = None
            self._stop.clear()
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if descriptor is not None:
                    os.close(descriptor)
                return False
            except OSError:
                if descriptor is not None:
                    os.close(descriptor)
                self.fatal_error_code = "WORKER_LOCK_ERROR"
                return False
            self._lock_fd = descriptor
            owner_id = str(uuid4())
            self._thread = threading.Thread(
                target=self._run, args=(owner_id, descriptor),
                name=f"image-worker-{owner_id}", daemon=False,
            )
            try:
                self._thread.start()
            except RuntimeError:
                self._release_lock(descriptor)
                self._lock_fd = None
                self.fatal_error_code = "WORKER_START_FAILED"
                return False
            return True

    def stop(self, timeout: float | None = None) -> bool:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("Shutdown timeout must be finite and nonnegative")
        with self._state_lock:
            thread = self._thread
            self._stop.set()
        if thread is None:
            return True
        if thread is threading.current_thread():
            # A processor may request shutdown, but must finish before release.
            return False
        thread.join(timeout=timeout)
        return not thread.is_alive()

    @staticmethod
    def _release_lock(descriptor: int) -> None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _run(self, owner_id: str, descriptor: int) -> None:
        try:
            # Only the process holding the execution lock may recover running jobs.
            self.ledger.recover_interrupted()
            while not self._stop.is_set():
                claimed = self.ledger.claim_next(owner_id)
                if claimed is None:
                    self._stop.wait(self.poll_seconds)
                    continue
                self._execute(claimed.job.job_id, claimed.project_id,
                              claimed.request, owner_id)
        except (sqlite3.Error, GatewayError):
            # The completion commit may be ambiguous. Stop and let the next
            # exclusive owner inspect durable state; do not double-execute here.
            self.fatal_error_code = "WORKER_LEDGER_ERROR"
        except BaseException:
            # No exception messages, payloads, tokens or paths are exposed.
            self.fatal_error_code = "WORKER_FATAL_ERROR"
        finally:
            try:
                self._release_lock(descriptor)
            except OSError:
                self.fatal_error_code = "WORKER_LOCK_ERROR"
            finally:
                with self._state_lock:
                    self._lock_fd = None

    def _execute(self, job_id: UUID, project_id: UUID, request: ImageRequest,
                 owner_id: str) -> None:
        try:
            result = self.processor(request, project_id)
        except Exception as exc:
            self.ledger.finish(job_id, owner_id, error_code=self._processor_code(exc))
            return
        try:
            if not isinstance(result, (CutoutResult, BackgroundResult)):
                raise ValueError("Processor did not return an image result")
            result = _RESULT_ADAPTER.validate_python(result.model_dump())
            if result.kind != request.kind:
                raise ValueError("Result kind does not match request")
            refs = ((result.product_rgba, result.preserve_mask)
                    if isinstance(result, CutoutResult)
                    else (result.scene_underlay, result.product_rgba, result.composed_preview))
            for reference in refs:
                image = self.assets.read(reference, project_id=project_id)
                image.close()
        except GatewayError as exc:
            code = exc.code if exc.code in _SAFE_PROCESSOR_CODES else "IMAGE_RESULT_INVALID"
            self.ledger.finish(job_id, owner_id, error_code=code)
            return
        except MemoryError:
            self.ledger.finish(job_id, owner_id, error_code="IMAGE_OUT_OF_MEMORY")
            return
        except (ValidationError, ValueError, TypeError, OSError):
            self.ledger.finish(job_id, owner_id, error_code="IMAGE_RESULT_INVALID")
            return
        self.ledger.finish(job_id, owner_id, result=result)

    @staticmethod
    def _processor_code(error: Exception) -> str:
        if isinstance(error, MemoryError) or type(error).__name__ == "OutOfMemoryError":
            return "IMAGE_OUT_OF_MEMORY"
        if isinstance(error, RuntimeError):
            try:
                if "out of memory" in str(error).lower():
                    return "IMAGE_OUT_OF_MEMORY"
            except Exception:
                pass
        if isinstance(error, GatewayError) and error.code in _SAFE_PROCESSOR_CODES:
            return error.code
        return "IMAGE_PROCESSING_FAILED"
