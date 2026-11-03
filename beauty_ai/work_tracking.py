"""Optional work-scoped observation; never submits or retries a provider call.

The provider/job/budget stores keep their existing authority. This module only
links immutable, sanitized observations and offers receipt-only crash recovery.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from uuid import UUID, uuid4

from .contracts import content_hash
from .errors import GatewayError
from .work_contracts import (RECEIPT_SCHEMA_VERSION, TEXT_OPERATION_STAGES, USAGE_KEYS,
                             canonical_bytes, digest, normalize_receipt, normalize_usage,
                             normalize_uuid, parse_work_json, validate_identifier)
from .work_ledger import AdWorkLedger


def _error(code):
    return GatewayError(code, "Work observation requires reconciliation.")


@dataclass(frozen=True)
class WorkScope:
    ledger: AdWorkLedger
    project_id: UUID
    work_id: UUID


_ACTIVE: ContextVar[WorkScope | None] = ContextVar("beautyai_ad_work", default=None)


def current_ad_work() -> WorkScope | None:
    return _ACTIVE.get()


@contextmanager
def track_ad_work(ledger: AdWorkLedger, project_id, work_id):
    """Use the same project/work for nested async calls, restoring it on exit."""
    scope = WorkScope(ledger, UUID(normalize_uuid(project_id)), UUID(normalize_uuid(work_id)))
    previous = current_ad_work()
    if previous is not None and (previous.project_id != scope.project_id
            or previous.work_id != scope.work_id or previous.ledger.path != ledger.path):
        raise _error("WORK_SCOPE_CONFLICT")
    reconcile_work_receipts(ledger, scope.project_id, scope.work_id)
    ledger.require_open(scope.project_id, scope.work_id)
    token = _ACTIVE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE.reset(token)


def resolve_image_work(work_ledger, project_id, job_id, work_id=None) -> WorkScope | None:
    """Resolve explicit, active and durable ownership before any asset/provider IO.

Existing bindings may be read on closed works. The processor must independently
allow only a verified already-successful result replay in that case.
"""
    project = UUID(normalize_uuid(project_id))
    job = UUID(normalize_uuid(job_id))
    active = current_ad_work()
    if active is not None:
        if active.project_id != project:
            raise _error("WORK_SCOPE_CONFLICT")
        if work_ledger is not None and work_ledger.path != active.ledger.path:
            raise _error("WORK_SCOPE_CONFLICT")
        work_ledger = work_ledger or active.ledger
    if work_ledger is None:
        if work_id is not None:
            raise _error("WORK_SERVICE_UNAVAILABLE")
        return None
    requested = normalize_uuid(work_id) if work_id is not None else None
    bound = work_ledger.get_image_binding(project, job)
    ids = {value for value in (requested, bound, str(active.work_id) if active else None)
           if value is not None}
    if len(ids) > 1:
        raise _error("WORK_SCOPE_CONFLICT")
    if not ids:
        return None
    chosen = UUID(ids.pop())
    work_ledger.get_work(project, chosen)
    work_ledger.bind_image_job(project, chosen, job)
    return WorkScope(work_ledger, project, chosen)


def _safe_identifier(value, fallback=None):
    if value is None:
        return fallback
    try:
        return validate_identifier(value)
    except ValueError:
        return fallback


def _usage(raw, normalized=None):
    """Preserve normalized facts first, filling only absent provider details."""
    if type(raw) is not dict:
        raw = {}
    details = raw.get("input_tokens_details")
    details = details if type(details) is dict else {}
    output_details = raw.get("output_tokens_details")
    output_details = output_details if type(output_details) is dict else {}
    observed = {key: raw.get(key) for key in USAGE_KEYS}
    for key, value in (("input_text_tokens", details.get("text_tokens")),
                       ("input_image_tokens", details.get("image_tokens")),
                       ("cached_input_tokens", details.get("cached_tokens")),
                       ("reasoning_tokens", output_details.get("reasoning_tokens"))):
        if observed[key] is None:
            observed[key] = value
    if type(normalized) is dict:
        for key in USAGE_KEYS:
            if normalized.get(key) is not None:
                observed[key] = normalized[key]
    if all(value is None for value in observed.values()):
        return None
    return normalize_usage(observed)


def make_work_receipt(attempt_id, *, model, status, usage=None, provider_request_id=None,
                      elapsed_seconds=None, error_code=None, artifact_sha256=None,
                      estimated_cost_usd=None, rate_version=None, actual_cost_usd=None,
                      billing_evidence_ref=None) -> dict:
    """Stable provenance over safe facts only; no clocks/reuse events in its hash."""
    receipt = {"schema_version": RECEIPT_SCHEMA_VERSION,
        "attempt_id": normalize_uuid(attempt_id), "provider_request_id": provider_request_id,
        "model": model, "usage": usage, "elapsed_seconds": elapsed_seconds, "status": status,
        "error_code": error_code, "artifact_sha256": artifact_sha256,
        "estimated_cost_usd": estimated_cost_usd, "rate_version": rate_version,
        "actual_cost_usd": actual_cost_usd, "billing_evidence_ref": billing_evidence_ref}
    # Normalize amounts/usage before hashing, so an identical replay stays identical.
    provisional = normalize_receipt({**receipt, "source_ref": "work-receipt:" + receipt["attempt_id"],
                                     "source_sha256": "0" * 64})
    facts = {key: value for key, value in provisional.items()
             if key not in {"source_ref", "source_sha256"}}
    return {**provisional, "source_sha256": digest(facts)}


def sync_image_receipt(ledger, project_id, job_id, provider_record, request) -> None:
    """Collect a durable image receipt without making any provider request."""
    if provider_record is None:
        return
    work_id = ledger.get_image_binding(project_id, job_id)
    if work_id is None:
        raise _error("WORK_NOT_FOUND")
    if (provider_record.get("job_id") != str(job_id)
            or provider_record.get("request_hash") != content_hash(request)):
        raise _error("WORK_SCOPE_CONFLICT")
    meta = provider_record.get("metadata") or {}
    prompt_version = (str(request.managed_prompt.version) if request.managed_prompt else
                      request.prompt.prompt_version if request.prompt else "unmanaged-v1")
    identity = {"project_id": normalize_uuid(project_id), "work_id": work_id,
        "attempt_id": provider_record["attempt_id"], "stage": "image_generation",
        "operation": request.operation, "provider": "openai", "configured_model": request.model,
        "prompt_version": prompt_version, "request_hash": content_hash(request),
        "parent_attempt_id": None, "started_at": provider_record["created_at"]}
    state = provider_record.get("state")
    if state not in {"submitting", "succeeded", "failed", "unknown"}:
        raise _error("WORK_RECEIPT_INVALID")
    receipt = None
    if state != "submitting":
        result = provider_record.get("result") or {}
        image = result.get("image") or {}
        receipt = make_work_receipt(identity["attempt_id"],
            model=_safe_identifier(meta.get("model"), request.model),
            status={"succeeded": "SUCCEEDED", "failed": "FAILED", "unknown": "UNKNOWN"}[state],
            usage=_usage(provider_record.get("raw_usage"), provider_record.get("usage")),
            provider_request_id=provider_record.get("provider_request_id"),
            elapsed_seconds=provider_record.get("elapsed_seconds"),
            error_code=provider_record.get("error_code"), artifact_sha256=image.get("sha256"))
    ledger.sync_image_attempt(project_id, job_id, identity, receipt)


class WorkCapturePending(GatewayError):
    """Contains only the sanitized receipt, suitable for the authenticated API."""
    def __init__(self, receipt, *, recovery_saved):
        super().__init__("WORK_CAPTURE_PENDING", "Recover the receipt before another provider request.")
        self.receipt = receipt
        self.recovery_saved = recovery_saved


def _inbox(ledger, project_id):
    base = ledger.path.parent
    path = base / ".cache" / "work-receipts" / digest({"ledger": str(ledger.path)}) / str(project_id)
    # Never follow a substituted inbox directory to another location.
    current = base
    for part in path.relative_to(base).parts:
        current /= part
        if current.is_symlink():
            raise OSError("unsafe inbox")
    return path


def _save_receipt(ledger, project_id, work_id, receipt):
    directory = _inbox(ledger, project_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (receipt["attempt_id"] + ".json")
    tmp = directory / (str(uuid4()) + ".tmp")
    value = {"schema_version": "beautyai-work-recovery-v1", "project_id": str(project_id),
             "work_id": str(work_id), "receipt": receipt}
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def _persist_receipt(ledger, project_id, work_id, receipt):
    path = None
    try:
        path = _save_receipt(ledger, project_id, work_id, receipt)
        ledger.record_receipt(project_id, work_id, receipt)
    except (GatewayError, OSError, ValueError):
        raise WorkCapturePending(receipt, recovery_saved=path is not None) from None
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Exact replay of an already-committed receipt is safe on next recovery.
        pass


def reconcile_work_receipts(ledger, project_id, work_id) -> int:
    """Re-import matching safe receipts only; never touches an SDK/client."""
    project_id, work_id = normalize_uuid(project_id), normalize_uuid(work_id)
    try:
        directory = _inbox(ledger, project_id)
        if not directory.exists():
            return 0
        paths = list(directory.glob("*.json"))
        if len(paths) > 20000:
            raise _error("WORK_LIMIT")
        count = 0
        for path in paths:
            try:
                if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                    raise _error("WORK_CAPTURE_PENDING")
                value = parse_work_json(path.read_bytes())
            except FileNotFoundError:
                # Another collector committed/consumed the immutable receipt.
                continue
            if (type(value) is not dict or set(value) != {"schema_version", "project_id", "work_id", "receipt"}
                    or value["schema_version"] != "beautyai-work-recovery-v1"
                    or value["project_id"] != project_id):
                raise _error("WORK_CAPTURE_PENDING")
            if value["work_id"] != work_id:
                continue
            receipt = normalize_receipt(value["receipt"])
            if path.name != receipt["attempt_id"] + ".json":
                raise _error("WORK_CAPTURE_PENDING")
            ledger.record_receipt(project_id, work_id, receipt)
            path.unlink(missing_ok=True)
            count += 1
        return count
    except (OSError, ValueError):
        raise _error("WORK_CAPTURE_PENDING") from None


class TextWorkCapture:
    """One provider attempt; callers retain control of parsing/business policy."""
    def __init__(self, scope, *, operation, model, prompt_version, request_payload):
        self.scope, self.operation = scope, operation
        self.configured_model = self.model = model
        self.prompt_version = prompt_version
        self.request_hash = digest(request_payload)
        self.attempt_id = uuid4()
        self.usage = self.provider_request_id = None
        self.started_at = None

    def start(self):
        scope = self.scope
        reconcile_work_receipts(scope.ledger, scope.project_id, scope.work_id)
        scope.ledger.start_attempt(scope.project_id, scope.work_id, self.attempt_id,
            TEXT_OPERATION_STAGES[self.operation], self.operation, "openai", self.configured_model,
            self.prompt_version, self.request_hash, reject_repeated_request=True)
        self.started_at = time.monotonic()

    def observe(self, raw):
        """Inspect SDK raw HTTP JSON before its structured-output parser executes."""
        response = getattr(raw, "http_response", None)
        if response is None:
            return
        self.provider_request_id = _safe_identifier(response.headers.get("x-request-id"))
        try:
            body = response.json()
        except (ValueError, UnicodeError):
            return
        if type(body) is not dict:
            return
        self.model = _safe_identifier(body.get("model"), self.configured_model)
        try:
            self.usage = _usage(body.get("usage"))
        except ValueError:
            # Invalid provider counts do not become zero or fabricated estimates.
            self.usage = None
            raise _error("WORK_USAGE_INVALID") from None

    def finish(self, status, error_code=None):
        if self.started_at is None:
            return
        receipt = make_work_receipt(self.attempt_id, model=self.model, status=status,
            usage=self.usage, provider_request_id=self.provider_request_id,
            elapsed_seconds=max(0.0, time.monotonic() - self.started_at), error_code=error_code)
        _persist_receipt(self.scope.ledger, self.scope.project_id, self.scope.work_id, receipt)


def record_local_stage(ledger, project_id, work_id, *, stage="composition", operation="compose_ad",
                       artifact_sha256=None, request_hash=None, prompt_version="local-v1",
                       status="SUCCEEDED", error_code=None, elapsed_seconds=None,
                       attempt_id=None, parent_attempt_id=None) -> str:
    """Explicitly observe a local step. Zero means model API cost only."""
    attempt_id = attempt_id or uuid4()
    request_hash = request_hash or digest({"operation": operation, "artifact_sha256": artifact_sha256})
    receipt = make_work_receipt(attempt_id, model="local", status=status,
        usage={"input_tokens": 0, "output_tokens": 0}, elapsed_seconds=elapsed_seconds,
        error_code=error_code, artifact_sha256=artifact_sha256,
        estimated_cost_usd="0.000000000", rate_version="local-api-zero-v1")
    ledger.start_attempt(project_id, work_id, attempt_id, stage, operation, "local", "local",
                         prompt_version, request_hash, parent_attempt_id)
    _persist_receipt(ledger, project_id, work_id, receipt)
    return str(attempt_id)
