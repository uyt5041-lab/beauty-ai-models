"""Read frozen experiment-runner results without credentials, network or DB writes.

This collector verifies current local runner plans. If an immutable local prompt
version is changed later, verification stops safely; it never reconstructs a
historical prompt from the replacement. Upload receipts belong in separate files.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from io import BytesIO
from pathlib import Path
import re
from uuid import UUID

from PIL import Image

from .contracts import (ExternalImageRequest, ExternalImageResult, ImageJob,
                        ImageProviderUsage, image_result_matches)
from .experiment_records import _digest, _file, _hash, _number, _read, _time, _url
from .experiment_runner import load_verified_plan

EXECUTION_SCHEMA = "beautyai-experiment-execution-v1"
_SLOT_MEANING = "RESERVED_SUBMISSION_NOT_CONFIRMED_POST_OR_CHARGE"
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}")
_FINAL = {"succeeded", "failed", "unknown", "timed_out", "runner_error"}


def _invalid() -> None:
    raise ValueError("EXPERIMENT_TRIAL_EVIDENCE_INVALID") from None


def _timestamp(value: object) -> str:
    result = _time(value)
    if result is None:
        _invalid()
    return result


def _interval(start: object, finish: object) -> tuple[str, str]:
    first, last = _timestamp(start), _timestamp(finish)
    if datetime.fromisoformat(last.replace("Z", "+00:00")) < datetime.fromisoformat(first.replace("Z", "+00:00")):
        _invalid()
    return first, last


def _usage(value: object) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict:
        _invalid()
    usage = ImageProviderUsage.model_validate(value).model_dump(mode="json")
    if usage != value:
        _invalid()
    return usage


def _job_and_receipt(entry: dict, case: dict, request: ExternalImageRequest) -> tuple[dict | None, dict | None]:
    job = entry.get("job")
    if job is not None:
        parsed = ImageJob.model_validate(job)
        if str(parsed.job_id) != case["job_id"] or parsed.request_hash != case["request_sha256"]:
            _invalid()
        if parsed.result is not None and not image_result_matches(request, parsed.result):
            _invalid()
        if parsed.error_code is not None and not _CODE.fullmatch(parsed.error_code):
            _invalid()
    receipt = entry["provider_receipt"]
    if receipt is not None:
        if type(receipt) is not dict:
            _invalid()
        if (receipt["job_id"] != case["job_id"] or receipt["request_hash"] != case["request_sha256"]
                or receipt["provider"] != "openai" or receipt["state"] not in {"submitting", "succeeded", "failed", "unknown"}
                or receipt["billing_status"] != "UNRECONCILED" or receipt["actual_cost_usd"] is not None
                or str(UUID(receipt["attempt_id"])) != receipt["attempt_id"]):
            _invalid()
        _interval(receipt["created_at"], receipt["updated_at"])
        submission = "UNKNOWN" if receipt["state"] in {"submitting", "unknown"} else "RESPONSE_RECEIVED"
        if receipt["submission_status"] != submission:
            _invalid()
        if receipt["error_code"] is not None and (type(receipt["error_code"]) is not str or not _CODE.fullmatch(receipt["error_code"])):
            _invalid()
        if receipt.get("provider_request_id") is not None and (type(receipt["provider_request_id"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", receipt["provider_request_id"])):
            _invalid()
        if _usage(receipt["usage"]) != _usage(entry["usage"]):
            _invalid()
        if receipt["result"] is not None:
            result = ExternalImageResult.model_validate(receipt["result"])
            if (not image_result_matches(request, result) or str(result.attempt_id) != receipt["attempt_id"]
                    or result.provider_request_id != receipt["provider_request_id"]
                    or (result.usage.model_dump(mode="json") if result.usage else None) != receipt["usage"]):
                _invalid()
            if receipt["state"] != "succeeded":
                _invalid()
        elif receipt["state"] == "succeeded":
            _invalid()
    elif entry["usage"] is not None:
        _invalid()
    if entry["status"] == "succeeded":
        if (job is None or job["status"] != "succeeded" or receipt is None
                or receipt["state"] != "succeeded" or job["result"] != receipt["result"]):
            _invalid()
    elif entry["status"] == "failed":
        if (job is not None and job["status"] not in {"failed", "interrupted"}
                or receipt is not None and receipt["state"] != "failed"):
            _invalid()
    elif entry["status"] == "unknown":
        if (receipt is None or receipt["state"] not in {"unknown", "submitting"}
                or job is not None and job["status"] not in {"failed", "interrupted"}):
            _invalid()
    elif entry["status"] == "timed_out":
        if job is not None or entry.get("provider_may_continue") is not True:
            _invalid()
    return job, receipt


def _output(root: Path, entry: dict, case: dict, receipt: dict | None) -> tuple[list[str], list[str]]:
    if entry["image_path"] is None:
        if entry["output_sha256"] is not None or entry["status"] == "succeeded":
            _invalid()
        return [], []
    if entry["status"] != "succeeded" or receipt is None:
        _invalid()
    expected = f"cases/{case['case_id']}/image.png"
    if entry["image_path"] != expected:
        _invalid()
    path = _file(root, root / expected, limit=20 * 1024 * 1024)
    digest = _digest(entry["output_sha256"])
    result = receipt["result"]["image"]
    if _hash(path) != digest or result["sha256"] != digest:
        _invalid()
    raw = path.read_bytes()
    with Image.open(BytesIO(raw)) as image:
        if (image.format != "PNG" or image.size != (result["width"], result["height"])
                or image.width * image.height > 16_000_000):
            _invalid()
        image.verify()
    return [str(path)], [digest]


def collect_trial(plan_path: Path, *, expected_sha256: str | None = None,
                  include_mock: bool = False) -> list[dict]:
    """Collect terminal case evidence. Previews and unrequested mocks return no rows.

    A row is a started experiment case, not proof of one HTTP POST or charge.
    Pending/partial runs stop safely. Reports, databases and images are not written.
    """
    try:
        if type(include_mock) is not bool:
            _invalid()
        path = Path(plan_path).absolute()
        if not path.is_file():
            _invalid()
        # Read the fixed files only; never initialize or open the runtime asset DB.
        root, plan, plan_sha = load_verified_plan(path, expected_sha256=expected_sha256,
                                                verify_runtime_assets=False)
        report_path = root / "report.json"
        if not report_path.exists() and not report_path.is_symlink():
            return []
        report = _read(root, report_path)
        if report.get("schema_version") != EXECUTION_SCHEMA:
            _invalid()
        mode = report.get("mode")
        if mode not in {"LIVE_USER_AUTHORIZED", "OFFLINE_MOCK"}:
            _invalid()
        if mode == "OFFLINE_MOCK" and not include_mock:
            return []
        family = "mock_api" if mode == "OFFLINE_MOCK" else "api"
        if (report["run_id"] != plan["run_id"] or report["plan_sha256"] != plan_sha
                or report["planned_calls"] != plan["planned_calls"] or type(report["planned_calls"]) is not int
                or report["max_calls"] != plan["max_calls"] or type(report["max_calls"]) is not int
                or type(report["attempt_slots"]) is not int or not 0 <= report["attempt_slots"] <= plan["max_calls"]
                or report["attempt_slot_meaning"] != _SLOT_MEANING
                or report["review_status"] != "NOT_APPROVED" or report["billing_status"] != "UNRECONCILED"
                or report["estimated_cost_usd"] is not None or report["actual_cost_usd"] is not None
                or report["estimate_status"] != "NOT_CALCULATED_NO_RATE_BASIS"
                or report["status"] not in {"SUCCEEDED", "FAILED", "UNKNOWN", "TIMED_OUT", "RUNNER_ERROR"}
                or type(report["operations"]) is not list or len(report["operations"]) != len(plan["cases"])):
            _invalid()
        report_start, report_finish = _interval(report["started_at"], report["finished_at"])
        report_sha = _hash(report_path)
        outputs_root = next(parent for parent in root.parents if parent.name == "outputs")
        source_paths = [str(_file(root, root / item["path"], limit=20 * 1024 * 1024)) for item in plan["inputs"]]
        provenance_inputs = [{key: item[key] for key in ("role", "index", "source_sha256", "normalized_sha256")}
                             for item in plan["inputs"]]
        records, stopped = [], False
        receipt_count = 0
        statuses = []
        for case, entry in zip(plan["cases"], report["operations"], strict=True):
            if type(entry) is not dict or any(entry[key] != case[key] for key in
                    ("case_id", "job_id", "preset_id", "request_sha256", "provider_prompt_sha256")):
                _invalid()
            if _read(root, root / "cases" / case["case_id"] / "report.json") != entry:
                _invalid()
            status = entry["status"]
            statuses.append(status)
            if status == "SKIPPED_AFTER_STOP":
                if (not stopped and report["status"] != "RUNNER_ERROR" or entry["error_code"] != "EXPERIMENT_EARLIER_CASE_STOPPED"
                        or any(entry[key] is not None for key in ("started_at", "finished_at", "elapsed_seconds",
                            "provider_receipt", "usage", "image_path", "output_sha256", "estimated_cost_usd", "actual_cost_usd"))):
                    _invalid()
                stopped = True
                continue
            if status not in _FINAL or stopped:
                _invalid()
            if (entry["review_status"] != "NOT_APPROVED" or entry["billing_status"] != "UNRECONCILED"
                    or entry["estimated_cost_usd"] is not None or entry["actual_cost_usd"] is not None
                    or entry["timing_scope"] != "internal_submit_poll_and_asset_download"):
                _invalid()
            error_code = entry["error_code"]
            if status == "succeeded":
                if error_code is not None:
                    _invalid()
            elif type(error_code) is not str or not _CODE.fullmatch(error_code):
                _invalid()
            started_at, finished_at = _interval(entry["started_at"], entry["finished_at"])
            if (datetime.fromisoformat(started_at.replace("Z", "+00:00")) < datetime.fromisoformat(report_start.replace("Z", "+00:00"))
                    or datetime.fromisoformat(finished_at.replace("Z", "+00:00")) > datetime.fromisoformat(report_finish.replace("Z", "+00:00"))):
                _invalid()
            elapsed = _number(entry["elapsed_seconds"])
            if elapsed is None:
                _invalid()
            request = ExternalImageRequest.model_validate(case["request"])
            job, receipt = _job_and_receipt(entry, case, request)
            receipt_count += receipt is not None
            output_paths, output_hashes = _output(root, entry, case, receipt)
            trace = entry["langfuse"]
            if type(trace) is not dict or type(trace.get("readback_verified")) is not bool:
                _invalid()
            trace_url = _url(trace.get("trace_url"))
            prompt_name = request.managed_prompt.name if request.managed_prompt else "beautyai/cosmetics/" + case["preset_id"].replace("_", "-")
            records.append({
                "record_id": hashlib.sha256((family + ":" + case["job_id"]).encode()).hexdigest(),
                "family": family, "experiment_group": root.relative_to(outputs_root).as_posix(),
                "status": status, "error_code": error_code, "operation": request.operation, "model": request.model,
                "source_paths": list(source_paths), "output_paths": output_paths, "output_sha256s": output_hashes,
                "prompt_text": case["provider_prompt"], "prompt_name": prompt_name,
                "prompt_version": case["prompt_version"], "prompt_sha256": case["provider_prompt_sha256"],
                "request_hash": case["request_sha256"], "source_report": str(report_path),
                "source_report_sha256": report_sha, "started_at": started_at, "elapsed_seconds": elapsed,
                "timing_scope": entry["timing_scope"], "estimated_cost_usd": None, "actual_cost_usd": None,
                "billing_status": "UNRECONCILED", "usage": _usage(entry["usage"]) or {},
                "cost_category": "research_experiment" if family == "api" else "offline_mock_validation",
                "settings": {"width": request.width, "height": request.height, "quality": request.quality,
                    "pipeline_revision": request.pipeline_revision, "preset_id": case["preset_id"],
                    "repeat_index": case["repeat_index"]},
                "langfuse_url": trace_url, "quality_status": "NOT_APPROVED", "review_notes": "NOT_APPROVED",
                "data_kind": "synthetic_fixture" if family == "mock_api" else "unknown",
                "data_provenance": {"execution_schema": EXECUTION_SCHEMA, "execution_mode": mode,
                    "run_id": plan["run_id"], "case_id": case["case_id"], "job_id": case["job_id"],
                    "project_id": plan["project_id"], "execution_status": report["status"],
                    "plan_sha256": plan_sha, "prompt_source": case["prompt_source"], "prompt_scope": "frozen_provider_text",
                    "template_sha256": case["template_sha256"], "source_sha256s": [item["normalized_sha256"] for item in plan["inputs"]],
                    "inputs": provenance_inputs, "started_at_basis": "operation_receipt", "finished_at": finished_at,
                    "estimate_status": report["estimate_status"], "review_status": "NOT_APPROVED",
                    "provider_state": receipt["state"] if receipt else None,
                    "provider_submission_status": receipt["submission_status"] if receipt else "NOT_RECORDED",
                    "provider_request_id": receipt.get("provider_request_id") if receipt else None,
                    "provider_attempt_id": receipt["attempt_id"] if receipt else None,
                    "provider_may_continue": entry.get("provider_may_continue", status in {"unknown", "timed_out"}),
                    "model_invocation": "MOCK_TRANSPORT" if family == "mock_api" else "PROVIDER_RESPONSE_RECORDED" if receipt and receipt["submission_status"] == "RESPONSE_RECEIVED" else "UNCONFIRMED",
                    "record_count_meaning": "STARTED_CASE_NOT_CONFIRMED_HTTP_POST_OR_CHARGE",
                    "no_new_model_calls": True},
            })
            if status != "succeeded":
                stopped = True
        if not receipt_count <= report["attempt_slots"] <= len(records):
            _invalid()
        if report["status"] == "SUCCEEDED" and any(status != "succeeded" for status in statuses):
            _invalid()
        expected_status = {"FAILED": "failed", "UNKNOWN": "unknown", "TIMED_OUT": "timed_out"}.get(report["status"])
        if expected_status is not None and expected_status not in statuses:
            _invalid()
        if _hash(report_path) != report_sha:
            _invalid()
        return records
    except Exception as error:
        if isinstance(error, ValueError) and re.fullmatch(r"EXPERIMENT_[A-Z_]+", str(error)):
            raise
        _invalid()
