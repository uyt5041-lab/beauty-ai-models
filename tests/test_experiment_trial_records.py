"""Saved runner evidence contracts. Fixtures are synthetic; no provider is called."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import sqlite3
from uuid import uuid4

from PIL import Image
import pytest

from beauty_ai.experiment_runner import prepare_experiment
from beauty_ai.experiment_trial_records import collect_trial

START = "2026-10-23T01:00:00+00:00"
FINISH = "2026-10-23T01:00:02+00:00"
USAGE = {"input_tokens": 8, "output_tokens": 7, "total_tokens": 15,
         "input_image_tokens": 5, "input_text_tokens": 3}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def save_report(path, report):
    write(path.parent / "report.json", report)
    for entry in report["operations"]:
        write(path.parent / "cases" / entry["case_id"] / "report.json", entry)


@pytest.fixture
def trial(tmp_path):
    def create(statuses=("succeeded",), mode="LIVE_USER_AUTHORIZED"):
        sources = []
        for index, color in enumerate(("#8855aa", "#4488bb", "#cc7722")):
            source = tmp_path / f"source-{index}.png"
            with Image.new("RGB", (32, 40), color) as image:
                image.save(source)
            sources.append(source)
        path = prepare_experiment(products=sources[:2], references=sources[2:],
            output=tmp_path / "outputs" / ("trial-" + uuid4().hex),
            product_description="One fictional cosmetic bottle. Preserve its original material and label.",
            presets=["vivid_pop", "clean_studio", "premium_minimal"][:len(statuses)],
            prompt_version="cosmetics-v1", max_calls=len(statuses))
        envelope = json.loads(path.read_text())
        plan = envelope["plan"]
        entries = []
        for case, status in zip(plan["cases"], statuses, strict=True):
            entry = {key: case[key] for key in
                ("case_id", "job_id", "preset_id", "request_sha256", "provider_prompt_sha256")}
            entry.update(status=status, error_code=None, started_at=START, finished_at=FINISH,
                elapsed_seconds=2.0, timing_scope="internal_submit_poll_and_asset_download",
                provider_receipt=None, usage=None, image_path=None, output_sha256=None,
                review_status="NOT_APPROVED", billing_status="UNRECONCILED",
                estimated_cost_usd=None, actual_cost_usd=None,
                langfuse={"status": "SKIPPED_OFFLINE_OR_LOCAL", "readback_verified": False})
            attempt_id = str(uuid4())
            result = None
            if status == "succeeded":
                image_path = path.parent / "cases" / case["case_id"] / "image.png"
                with Image.new("RGB", (case["request"]["width"], case["request"]["height"]), "#224488") as image:
                    image.save(image_path)
                digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
                result = {"kind": "api_image", "operation": "edit", "provider": "openai",
                    "model": case["request"]["model"], "attempt_id": attempt_id,
                    "provider_request_id": "fixture-response", "usage": dict(USAGE),
                    "needs_manual_check": True, "warnings": [],
                    "image": {"asset_id": str(uuid4()), "sha256": digest,
                        "width": case["request"]["width"], "height": case["request"]["height"], "mime_type": "image/png"}}
                entry.update(image_path=f"cases/{case['case_id']}/image.png", output_sha256=digest, usage=dict(USAGE))
            elif status == "SKIPPED_AFTER_STOP":
                entry.update(error_code="EXPERIMENT_EARLIER_CASE_STOPPED", started_at=None,
                             finished_at=None, elapsed_seconds=None)
            else:
                entry["error_code"] = {"failed": "OPENAI_REQUEST_FAILED", "unknown": "OPENAI_OUTCOME_UNKNOWN",
                    "timed_out": "EXPERIMENT_WAIT_ENDED", "runner_error": "EXPERIMENT_CASE_PROCESSING_FAILED"}[status]
            if status in {"succeeded", "failed", "unknown", "timed_out"}:
                state = "submitting" if status == "timed_out" else status
                entry["provider_receipt"] = {"job_id": case["job_id"], "attempt_id": attempt_id,
                    "request_hash": case["request_sha256"], "state": state, "provider": "openai",
                    "provider_request_id": "fixture-response" if status == "succeeded" else None,
                    "reservation_usd": "1.000000", "billing_status": "UNRECONCILED", "actual_cost_usd": None,
                    "submission_status": "UNKNOWN" if state in {"unknown", "submitting"} else "RESPONSE_RECEIVED",
                    "usage": dict(USAGE) if status == "succeeded" else None, "result": result,
                    "error_code": entry["error_code"], "created_at": START, "updated_at": FINISH}
            if status in {"succeeded", "failed", "unknown"}:
                entry["job"] = {"job_id": case["job_id"], "request_hash": case["request_sha256"],
                    "status": "succeeded" if status == "succeeded" else "failed",
                    "result": result, "error_code": entry["error_code"]}
            elif status == "timed_out":
                entry.update(job=None, provider_may_continue=True)
            entries.append(entry)
        failure = next((status for status in statuses if status != "succeeded"), None)
        report = {"schema_version": "beautyai-experiment-execution-v1", "run_id": plan["run_id"],
            "plan_sha256": envelope["sha256"], "mode": mode,
            "status": "SUCCEEDED" if failure is None else {"failed": "FAILED", "unknown": "UNKNOWN",
                "timed_out": "TIMED_OUT", "runner_error": "RUNNER_ERROR", "SKIPPED_AFTER_STOP": "RUNNER_ERROR"}[failure],
            "started_at": START, "finished_at": FINISH, "planned_calls": len(entries), "max_calls": len(entries),
            "attempt_slots": sum(entry["provider_receipt"] is not None for entry in entries),
            "attempt_slot_meaning": "RESERVED_SUBMISSION_NOT_CONFIRMED_POST_OR_CHARGE",
            "review_status": "NOT_APPROVED", "billing_status": "UNRECONCILED",
            "estimated_cost_usd": None, "actual_cost_usd": None,
            "estimate_status": "NOT_CALCULATED_NO_RATE_BASIS", "operations": entries}
        save_report(path, report)
        return path, plan, report, envelope["sha256"]
    return create


def test_saved_result_preserves_full_prompt_inputs_version_timing_and_unknown_cost(trial):
    path, plan, report, digest = trial()
    records = collect_trial(path, expected_sha256=digest)
    assert len(records) == 1
    record, case = records[0], plan["cases"][0]
    assert record["record_id"] == hashlib.sha256(("api:" + case["job_id"]).encode()).hexdigest()
    assert record["prompt_text"] == case["provider_prompt"]
    assert record["prompt_sha256"] == case["provider_prompt_sha256"]
    assert record["prompt_version"] == "cosmetics-v1"
    assert record["request_hash"] == case["request_sha256"]
    assert record["started_at"] == START and record["elapsed_seconds"] == 2
    assert record["timing_scope"] == "internal_submit_poll_and_asset_download"
    assert record["usage"] == USAGE
    assert record["estimated_cost_usd"] is None and record["actual_cost_usd"] is None
    assert record["billing_status"] == "UNRECONCILED" and record["quality_status"] == "NOT_APPROVED"
    assert record["source_report_sha256"] == hashlib.sha256((path.parent / "report.json").read_bytes()).hexdigest()
    assert len(record["source_paths"]) == 3 and len(record["output_paths"]) == 1
    provenance = record["data_provenance"]
    assert provenance["plan_sha256"] == digest
    assert provenance["execution_schema"] == "beautyai-experiment-execution-v1"
    assert [(item["role"], item["index"]) for item in provenance["inputs"]] == [
        ("product", 1), ("product", 2), ("reference", 1)]
    assert provenance["source_sha256s"] == [item["normalized_sha256"] for item in plan["inputs"]]
    assert provenance["record_count_meaning"] == "STARTED_CASE_NOT_CONFIRMED_HTTP_POST_OR_CHARGE"
    assert provenance["estimate_status"] == "NOT_CALCULATED_NO_RATE_BASIS"


def test_preview_and_default_mock_are_not_collected(trial):
    path, *_ = trial(mode="OFFLINE_MOCK")
    assert collect_trial(path) == []
    (path.parent / "report.json").unlink()
    assert collect_trial(path, include_mock=True) == []


def test_explicit_mock_is_separate_from_api_quality_and_real_data(trial):
    path, plan, *_ = trial(mode="OFFLINE_MOCK")
    record = collect_trial(path, include_mock=True)[0]
    assert record["family"] == "mock_api" and record["data_kind"] == "synthetic_fixture"
    assert record["quality_status"] == "NOT_APPROVED"
    assert record["data_provenance"]["model_invocation"] == "MOCK_TRANSPORT"
    assert record["record_id"] != hashlib.sha256(("api:" + plan["cases"][0]["job_id"]).encode()).hexdigest()


@pytest.mark.parametrize("status", ["failed", "unknown", "timed_out", "runner_error"])
def test_unsuccessful_cases_preserve_status_and_exclude_following_unstarted_case(trial, status):
    path, _, report, _ = trial((status, "SKIPPED_AFTER_STOP"))
    records = collect_trial(path)
    assert len(records) == 1 and records[0]["status"] == status
    assert records[0]["output_paths"] == [] and records[0]["output_sha256s"] == []
    assert records[0]["estimated_cost_usd"] is None
    assert records[0]["data_provenance"]["provider_may_continue"] is (status in {"unknown", "timed_out"})


def test_completed_cases_before_stop_are_retained(trial):
    path, *_ = trial(("succeeded", "failed", "SKIPPED_AFTER_STOP"))
    assert [record["status"] for record in collect_trial(path)] == ["succeeded", "failed"]


def test_collector_does_not_read_credentials_network_databases_or_write_inputs(trial, monkeypatch):
    path, *_ = trial()
    shutil.rmtree(path.parent / "runtime")
    before = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("Collector cannot access credentials, databases or network")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    import os
    monkeypatch.setattr(os, "getenv", forbidden)
    assert len(collect_trial(path)) == 1
    after = {p.relative_to(path.parent): p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.parametrize("field,value", [
    ("run_id", "run-incorrect"), ("plan_sha256", "0" * 64), ("planned_calls", True),
    ("max_calls", 30), ("attempt_slots", 0), ("finished_at", None),
    ("status", "RUNNING"), ("actual_cost_usd", 0), ("estimated_cost_usd", 0.1),
    ("mode", "pretend-live"), ("billing_status", "RECONCILED"),
])
def test_invalid_report_fields_are_rejected(trial, field, value):
    path, _, report, _ = trial()
    report[field] = value
    save_report(path, report)
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        collect_trial(path)


@pytest.mark.parametrize("change", ["job_id", "request_hash", "receipt_job_id", "receipt_hash", "receipt_result",
    "receipt_usage", "entry_usage", "entry_case", "prompt_hash", "time", "negative_elapsed", "bool_elapsed",
    "error_code", "output_hash", "output_path", "output_bytes", "case_report", "missing_case_report"])
def test_case_receipt_file_and_identity_tampering_stops_safely(trial, change):
    path, _, report, _ = trial()
    entry = report["operations"][0]
    if change == "job_id":
        entry["job"]["job_id"] = str(uuid4())
    elif change == "request_hash":
        entry["job"]["request_hash"] = "0" * 64
    elif change == "receipt_job_id":
        entry["provider_receipt"]["job_id"] = str(uuid4())
    elif change == "receipt_hash":
        entry["provider_receipt"]["request_hash"] = "0" * 64
    elif change == "receipt_result":
        entry["provider_receipt"]["result"] = deepcopy(entry["provider_receipt"]["result"])
        entry["provider_receipt"]["result"]["image"]["sha256"] = "0" * 64
    elif change == "receipt_usage":
        entry["provider_receipt"]["usage"]["input_tokens"] = 999
    elif change == "entry_usage":
        entry["usage"]["input_tokens"] = True
    elif change == "entry_case":
        entry["preset_id"] = "other"
    elif change == "prompt_hash":
        entry["provider_prompt_sha256"] = "0" * 64
    elif change == "time":
        entry["finished_at"] = "2026-10-23T00:00:00+00:00"
    elif change == "negative_elapsed":
        entry["elapsed_seconds"] = -1
    elif change == "bool_elapsed":
        entry["elapsed_seconds"] = True
    elif change == "error_code":
        entry["error_code"] = "sk-private-fixture-value-must-never-appear"
    elif change == "output_hash":
        entry["output_sha256"] = "0" * 64
    elif change == "output_path":
        entry["image_path"] = "../../private-secret.png"
    elif change == "output_bytes":
        (path.parent / entry["image_path"]).write_bytes(b"changed")
    save_report(path, report)
    case_report = path.parent / "cases" / entry["case_id"] / "report.json"
    if change == "case_report":
        write(case_report, {})
    elif change == "missing_case_report":
        case_report.unlink()
    with pytest.raises(ValueError, match="^EXPERIMENT_") as caught:
        collect_trial(path)
    assert "private" not in str(caught.value) and "sk-" not in str(caught.value)


def test_symlink_image_is_not_followed(trial, tmp_path):
    path, _, report, _ = trial()
    output = path.parent / report["operations"][0]["image_path"]
    outside = tmp_path / "same-image.png"
    output.rename(outside)
    output.symlink_to(outside)
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        collect_trial(path)


@pytest.mark.parametrize("raw", ['{"schema_version":"a","schema_version":"b"}', '{"x":NaN}', '[]'])
def test_ambiguous_or_nonfinite_json_is_rejected(trial, raw):
    path, *_ = trial()
    (path.parent / "report.json").write_text(raw)
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        collect_trial(path)


def test_new_started_case_after_a_failure_is_inconsistent(trial):
    path, *_ = trial(("failed", "succeeded"))
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        collect_trial(path)


def test_expected_plan_hash_is_enforced(trial):
    path, *_ = trial()
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        collect_trial(path, expected_sha256="0" * 64)


def test_trace_url_cannot_include_credentials_or_unapproved_hosts(trial):
    path, _, report, _ = trial()
    report["operations"][0]["langfuse"]["trace_url"] = "https://private-user@example.test/private"
    save_report(path, report)
    with pytest.raises(ValueError, match="^EXPERIMENT_") as caught:
        collect_trial(path)
    assert "private-user" not in str(caught.value)
