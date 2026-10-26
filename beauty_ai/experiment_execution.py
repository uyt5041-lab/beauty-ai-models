"""Execute a verified experiment once; never retry, price, or approve its images."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import shlex
import time
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient

from .assets import LocalAssetStore
from .contracts import (ExternalImageRequest, ExternalImageResult, ImageProviderUsage,
                        content_hash, image_result_matches)
from .cosmetics_prompts import render_cosmetics_provider_prompt
from .errors import GatewayError
from .inference_runtime import build_app
from .langfuse_prompts import begin_image_generation
from .openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from .provider_attempts import ProviderAttemptStore, money_units

POLL_TIMEOUT_SECONDS = 330.0
POLL_INTERVAL_SECONDS = 0.05
_SETTING_KEYS = {"OPENAI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "LANGFUSE_BASE_URL", "LANGFUSE_HOST"}
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_settings(env_file: Path | None) -> dict[str, str]:
    """Read only named credentials, after the plan and budget have been checked."""
    try:
        values = {key: os.environ[key] for key in _SETTING_KEYS if key in os.environ}
        if env_file is not None:
            for line in Path(env_file).read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)", line)
                if match and match[1] in _SETTING_KEYS:
                    words = shlex.split(match[2], comments=True)
                    if len(words) != 1:
                        raise ValueError
                    values[match[1]] = words[0]
        if not values.get("OPENAI_API_KEY"):
            raise ValueError
        return values
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError("EXPERIMENT_CREDENTIALS_UNAVAILABLE") from None


@contextmanager
def _trace_environment(settings: dict[str, str]):
    """Give the optional tracing SDK its settings for this explicit live run."""
    values = {key: value for key, value in settings.items() if key.startswith("LANGFUSE_")}
    if "LANGFUSE_BASE_URL" not in values and "LANGFUSE_HOST" in values:
        values["LANGFUSE_BASE_URL"] = values["LANGFUSE_HOST"]
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError:
        raise ValueError("EXPERIMENT_OUTPUT_STORAGE_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)


def _preflight(root: Path, plan: dict) -> list[ExternalImageRequest]:
    """Recheck exact provider inputs before credentials or the durable claim."""
    requests = []
    store = LocalAssetStore(root / "runtime/assets")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("Preflight must not submit"))))
    try:
        project_id = UUID(plan["project_id"])
        if (type(plan["planned_calls"]) is not int or plan["planned_calls"] != len(plan["cases"])
                or not 1 <= plan["planned_calls"] <= plan["max_calls"] <= 30):
            raise ValueError
        for case in plan["cases"]:
            request = ExternalImageRequest.model_validate(case["request"])
            provider_text = (request.managed_prompt.rendered_text if request.managed_prompt
                             else render_cosmetics_provider_prompt(request))
            if (content_hash(request) != case["request_sha256"]
                    or provider_text != case["provider_prompt"]
                    or hashlib.sha256(provider_text.encode()).hexdigest() != case["provider_prompt_sha256"]):
                raise ValueError
            adapter.validate_input(request,
                [store.read_bytes(ref, project_id=project_id) for ref in request.product_images],
                [store.read_bytes(ref, project_id=project_id) for ref in request.reference_images])
            requests.append(request)
        return requests
    except Exception:
        raise ValueError("EXPERIMENT_EXECUTION_PREFLIGHT_FAILED") from None


def _poll_job(client: TestClient, job_id: str) -> dict | None:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/internal/v1/jobs/{job_id}")
        if response.status_code != 200:
            raise ValueError("EXPERIMENT_JOB_READ_FAILED")
        job = response.json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def _finish_trace(trace, *, disabled: bool, **values) -> dict:
    if disabled:
        return {"status": "SKIPPED_OFFLINE_OR_LOCAL", "readback_verified": False}
    if trace is None:
        return {"status": "OBSERVABILITY_FAILED", "readback_verified": False}
    try:
        receipt = trace.finish(**values)
        # The built-in helper returns only safe IDs/status. Do not store errors.
        return {key: receipt[key] for key in
                ("status", "readback_verified", "trace_id", "observation_id") if key in receipt}
    except Exception:
        return {"status": "OBSERVABILITY_FAILED", "readback_verified": False}


def execute_plan(plan_path: Path, *, expected_sha256: str,
                 env_file: Path | None = None, budget_usd: str = "0",
                 reservation_usd: str = "1", transport: httpx.MockTransport | None = None,
                 allow_live: bool = False, wandb_entity: str | None = None,
                 wandb_project: str = "beautyai-image-experiments") -> dict:
    """Execute sequential jobs once. Mock mode never reads keys or traces remotely.

    Attempt slots are durable reservations, not proof of POST delivery or bills.
    A timeout stops this plan but cannot establish provider cancellation.
    """
    offline = isinstance(transport, httpx.MockTransport)
    if wandb_entity is not None:
        from .experiment_wandb import validate_destination
        validate_destination(wandb_entity, wandb_project)
    if not offline and allow_live is not True:
        raise ValueError("EXPERIMENT_LIVE_NOT_AUTHORIZED")
    if transport is not None and not offline:
        raise ValueError("EXPERIMENT_TRANSPORT_INVALID")
    if type(expected_sha256) is not str or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None:
        raise ValueError("EXPERIMENT_EXPECTED_PLAN_HASH_REQUIRED")
    from .experiment_runner import load_verified_plan, write_json

    root, plan, plan_sha = load_verified_plan(Path(plan_path), expected_sha256=expected_sha256)
    requests = _preflight(root, plan)
    try:
        budget = money_units(budget_usd)
        reservation = money_units(reservation_usd, positive=True)
    except ValueError:
        raise ValueError("EXPERIMENT_BUDGET_INVALID") from None
    if budget < reservation * plan["planned_calls"]:
        raise ValueError("EXPERIMENT_BUDGET_INSUFFICIENT")
    settings = {} if offline else _load_settings(env_file)
    config = OpenAIImagesConfig(live_calls=not offline,
                               api_key=settings.get("OPENAI_API_KEY"), timeout_seconds=300)
    # Validate credentials/configuration without opening a provider connection.
    adapter = OpenAIImagesAdapter(config, transport=transport)
    for request in requests:
        adapter.validate_request(request)
    project_id = UUID(plan["project_id"])
    attempts = ProviderAttemptStore(root / "runtime/provider_attempts.sqlite3")
    attempts.configure_experiment(plan_sha256=plan_sha, project_id=project_id,
        jobs={UUID(case["job_id"]): case["request_sha256"] for case in plan["cases"]},
        max_attempts=plan["max_calls"])
    # A repeated call stops here without modifying the original report.
    attempts.claim_experiment(plan_sha)
    report = {"schema_version": "beautyai-experiment-execution-v1", "run_id": plan["run_id"],
        "plan_sha256": plan_sha, "mode": "OFFLINE_MOCK" if offline else "LIVE_USER_AUTHORIZED",
        "status": "RUNNING", "started_at": _now(), "finished_at": None,
        "planned_calls": plan["planned_calls"], "max_calls": plan["max_calls"],
        "attempt_slots": 0, "attempt_slot_meaning": "RESERVED_SUBMISSION_NOT_CONFIRMED_POST_OR_CHARGE",
        "review_status": "NOT_APPROVED", "billing_status": "UNRECONCILED",
        "estimated_cost_usd": None, "actual_cost_usd": None,
        "estimate_status": "NOT_CALCULATED_NO_RATE_BASIS", "operations": []}
    for case in plan["cases"]:
        report["operations"].append({key: case[key] for key in
            ("case_id", "job_id", "preset_id", "request_sha256", "provider_prompt_sha256")} | {
            "status": "NOT_STARTED", "error_code": None, "started_at": None, "finished_at": None,
            "elapsed_seconds": None, "timing_scope": "internal_submit_poll_and_asset_download",
            "provider_receipt": None, "usage": None, "image_path": None, "output_sha256": None,
            "review_status": "NOT_APPROVED", "billing_status": "UNRECONCILED",
            "estimated_cost_usd": None, "actual_cost_usd": None,
            "langfuse": {"status": "NOT_STARTED", "readback_verified": False}})

    def save() -> None:
        state = attempts.experiment_status()
        report["attempt_slots"] = state["attempt_slots"] if state else None
        for entry in report["operations"]:
            write_json(root / "cases" / entry["case_id"] / "report.json", entry)
        write_json(root / "report.json", report)

    token = secrets.token_urlsafe(32)
    try:
        save()
        context = nullcontext() if offline else _trace_environment(settings)
        with context:
            app = build_app(root / "runtime", project_id, token, openai_config=config,
                openai_transport=transport, image_budget_usd=budget_usd,
                image_reservation_usd=reservation_usd)
            with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
                for case, request, entry in zip(plan["cases"], requests, report["operations"]):
                    entry.update(status="submitting", started_at=_now())
                    started = time.monotonic()
                    trace = None
                    trace_disabled = offline or request.managed_prompt is None
                    if not trace_disabled:
                        try:
                            trace = begin_image_generation(request, run_id=plan["run_id"],
                                job_id=case["job_id"], request_hash=case["request_sha256"])
                        except Exception:
                            pass
                    save()
                    try:
                        response = client.put(f"/internal/v1/jobs/{case['job_id']}", json=case["request"])
                        if response.status_code != 202:
                            entry.update(status="failed", error_code="EXPERIMENT_SUBMISSION_REJECTED")
                        else:
                            job = _poll_job(client, case["job_id"])
                            entry["job"] = job
                            receipt = attempts.get(UUID(case["job_id"]), project_id)
                            entry["provider_receipt"] = receipt
                            if receipt is not None:
                                if receipt["request_hash"] != case["request_sha256"]:
                                    raise ValueError("EXPERIMENT_RECEIPT_HASH_MISMATCH")
                                entry["usage"] = receipt.get("usage")
                            timing = client.get(f"/internal/v1/jobs/{case['job_id']}/timing")
                            entry["job_timing"] = timing.json() if timing.status_code == 200 else None
                            if job is None:
                                entry.update(status="timed_out", error_code="EXPERIMENT_WAIT_ENDED",
                                             provider_may_continue=True)
                            elif job["status"] != "succeeded":
                                unknown = receipt is not None and receipt["state"] in {"unknown", "submitting"}
                                code = job.get("error_code")
                                entry.update(status="unknown" if unknown else "failed",
                                    error_code=code if isinstance(code, str) and _SAFE_CODE.fullmatch(code)
                                    else "EXPERIMENT_JOB_FAILED")
                            else:
                                result = ExternalImageResult.model_validate(job["result"])
                                if (receipt is None or receipt["state"] != "succeeded"
                                        or receipt["result"] != result.model_dump(mode="json")
                                        or not image_result_matches(request, result)):
                                    raise ValueError("EXPERIMENT_RESULT_RECEIPT_MISMATCH")
                                downloaded = client.get(f"/internal/v1/assets/{result.image.asset_id}")
                                if downloaded.status_code != 200:
                                    raise ValueError("EXPERIMENT_DOWNLOAD_FAILED")
                                digest = hashlib.sha256(downloaded.content).hexdigest()
                                if digest != result.image.sha256:
                                    raise ValueError("EXPERIMENT_OUTPUT_HASH_MISMATCH")
                                image_path = Path("cases") / case["case_id"] / "image.png"
                                _write_bytes(root / image_path, downloaded.content)
                                entry.update(status="succeeded", image_path=image_path.as_posix(), output_sha256=digest)
                    except Exception as error:
                        entry.update(status="runner_error", error_code="EXPERIMENT_CASE_PROCESSING_FAILED")
                        if isinstance(error, GatewayError) and _SAFE_CODE.fullmatch(error.code):
                            entry["error_code"] = error.code
                        # Fixed codes only: never exception strings or HTTP error bodies.
                    entry.update(finished_at=_now(), elapsed_seconds=round(time.monotonic() - started, 6))
                    save()  # Preserve successful bytes and receipts before optional tracing.
                    usage = ImageProviderUsage.model_validate(entry["usage"]) if entry["usage"] else None
                    entry["langfuse"] = _finish_trace(trace, disabled=trace_disabled,
                        output_sha256=entry["output_sha256"], usage=usage,
                        provider_request_id=(entry["provider_receipt"] or {}).get("provider_request_id"),
                        estimated_cost_usd=None, error_code=entry["error_code"])
                    save()
                    if entry["status"] != "succeeded":
                        report["status"] = {"failed": "FAILED", "unknown": "UNKNOWN",
                            "timed_out": "TIMED_OUT"}.get(entry["status"], "RUNNER_ERROR")
                        break
        if all(entry["status"] == "succeeded" for entry in report["operations"]):
            report["status"] = "SUCCEEDED"
    except Exception:
        # A shutdown error does not turn a recorded timeout/unknown into proof
        # that the provider stopped. Keep that uncertainty visible.
        if report["status"] == "RUNNING":
            report["status"] = "RUNNER_ERROR"
            report["error_code"] = "EXPERIMENT_EXECUTION_FAILED"
        else:
            report["cleanup_error_code"] = "EXPERIMENT_RUNTIME_CLEANUP_FAILED"
    finally:
        if report["status"] == "RUNNING":
            report["status"] = "RUNNER_ERROR"
            report["error_code"] = "EXPERIMENT_EXECUTION_INTERRUPTED"
        for entry in report["operations"]:
            if entry["status"] == "NOT_STARTED":
                entry.update(status="SKIPPED_AFTER_STOP", error_code="EXPERIMENT_EARLIER_CASE_STOPPED")
        try:
            attempts.finish_experiment(plan_sha, succeeded=report["status"] == "SUCCEEDED")
        except Exception:
            report["status"] = "RUNNER_ERROR"
            report["error_code"] = "EXPERIMENT_FINAL_STATE_UNCONFIRMED"
        report["finished_at"] = _now()
        save()
    if wandb_entity is not None:
        from .experiment_wandb import finish_recording
        finish_recording(root / "plan.json", expected_sha256=plan_sha,
            entity=wandb_entity, project=wandb_project, live=not offline)
    return report
