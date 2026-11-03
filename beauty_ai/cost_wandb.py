"""Explicit W&B publication of saved cost reports; never an image executor.

The exact JSON artifact retains decimal strings. W&B numbers are derived display
values only. An inaccessible or unfinished remote run is never permission to
overwrite it, and local preparation never authenticates or contacts W&B.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from .cost_accounting import canonical_bytes, validate_identifier
from .wandb_experiments import ensure_private_project


SCHEMA_VERSION = "beautyai-cost-wandb-sync-v1"
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_REMOTE_RUNS = 10000
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_DESTINATION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,99})(?:\.[0-9]{1,9})?\Z")
_SECRET = re.compile(
    r"\b(?:sk|rk)-[A-Za-z0-9_-]{8,}|\bgh[pousr]_[A-Za-z0-9_]+"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_SECRET_KEY = re.compile(r"api[_-]?key|authorization|password|secret|credential|access[_-]?token", re.I)
_ARM_METRICS = ("total_cost_usd", "known_partial_usd", "accepted_ads", "unit_cost_usd",
                "cost_item_count", "known_cost_count", "missing_cost_count", "missing_batch_count")
TABLE_COLUMNS = ["basis"] + [f"{arm}_{metric}" for arm in ("baseline", "candidate")
                              for metric in _ARM_METRICS] + [
    "unit_cost_reduction_pct", "counterfactual_difference_usd", "reason_codes",
]
GENERATION_TABLE_COLUMNS = [
    "comparison_id", "estimate_rate_version", "baseline_generation_count", "candidate_generation_count",
    "baseline_total_estimated_cost_usd", "candidate_total_estimated_cost_usd",
    "baseline_estimated_cost_per_generation_usd", "candidate_estimated_cost_per_generation_usd",
    "estimated_cost_reduction_pct", "estimated_cost_difference_usd", "per_generation_difference_usd",
    "input_record_count", "planned_pair_count", "compared_pair_count", "compared_record_count",
    "excluded_pair_count", "excluded_record_count", "coverage_pct", "assurance", "reason_codes",
]
_GENERATION_COUNTS = ("input_record_count", "planned_pair_count", "compared_pair_count",
                      "compared_record_count", "excluded_pair_count", "excluded_record_count")
_GENERATION_AMOUNTS = ("estimated_cost_reduction_pct", "estimated_cost_difference_usd",
                       "per_generation_difference_usd", "coverage_pct")
_GENERATION_ARM_FIELDS = {"generation_count", "total_estimated_cost_usd",
                          "estimated_cost_per_generation_usd"}
_GENERATION_FIELDS = {"schema_version", "comparison_id", "estimate_rate_version", "baseline",
                      "candidate", "excluded_records", "reason_codes", "assurance",
                      *_GENERATION_COUNTS, *_GENERATION_AMOUNTS}
_REPORT_FIELDS = {"schema_version", "contract_version", "report_id", "mode", "data_kind",
                  "inputs", "scope", "overview", "comparison", "sources"}


class _SyncError(ValueError):
    """Only errors constructed here may cross the receipt boundary."""


def _fail(code: str) -> None:
    raise _SyncError(code)


def _safe_path(value: Path) -> Path:
    path = Path(os.path.abspath(value))
    if any(part.is_symlink() for part in (path, *path.parents)):
        _fail("WANDB_COST_UNSAFE_PATH")
    return path


def _read(path: Path) -> bytes:
    path = _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_REPORT_BYTES:
            _fail("WANDB_COST_REPORT_SIZE_INVALID")
        data = source.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        _fail("WANDB_COST_REPORT_SIZE_INVALID")
    return data


def _write(path: Path, data: bytes) -> None:
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_path(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cost-wandb-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _safe_path(path)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail("WANDB_COST_REPORT_INVALID")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=pairs,
                            parse_constant=lambda _: _fail("WANDB_COST_REPORT_INVALID"))
    except (ValueError, UnicodeError, RecursionError):
        _fail("WANDB_COST_REPORT_INVALID")
    if type(result) is not dict:
        _fail("WANDB_COST_REPORT_INVALID")
    return result


def _numeric(value: Any, *, count: bool = False) -> int | float | None:
    if value is None:
        return None
    if count:
        if type(value) is not int or not 0 <= value <= 2**53:
            _fail("WANDB_COST_REPORT_INVALID")
        return value
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        _fail("WANDB_COST_REPORT_INVALID")
    result = float(Decimal(value))
    if not math.isfinite(result):
        _fail("WANDB_COST_REPORT_INVALID")
    return result


def _validate_tree(value: Any, depth: int = 0) -> None:
    if depth > 20:
        _fail("WANDB_COST_REPORT_INVALID")
    if type(value) is dict:
        for key, child in value.items():
            if _SECRET_KEY.search(key):
                _fail("WANDB_COST_REPORT_PRIVATE_DATA")
            _validate_tree(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _validate_tree(child, depth + 1)
    elif type(value) is str:
        if len(value) > 4096 or _SECRET.search(value):
            _fail("WANDB_COST_REPORT_PRIVATE_DATA")
    elif value is not None and type(value) not in (int, bool):
        # Decimal source amounts must remain strings, including in the artifact.
        _fail("WANDB_COST_REPORT_INVALID")


def _generation_row(result: Any) -> list:
    """Validate the optional saved result before any SDK/authentication work."""
    if (type(result) is not dict or set(result) != _GENERATION_FIELDS
            or result["schema_version"] != "beautyai-generation-estimate-v1"
            or result["assurance"] != "OBSERVATIONAL_NOT_QUALITY_CONTROLLED"):
        _fail("WANDB_COST_REPORT_INVALID")
    for key in ("comparison_id", "estimate_rate_version"):
        try:
            validate_identifier(result[key])
        except ValueError:
            _fail("WANDB_COST_REPORT_INVALID")
    values = {}
    for key in _GENERATION_COUNTS:
        if type(result[key]) is not int:
            _fail("WANDB_COST_REPORT_INVALID")
        values[key] = _numeric(result[key], count=True)
    for key in _GENERATION_AMOUNTS:
        values[key] = _numeric(result[key])
    for arm in ("baseline", "candidate"):
        data = result[arm]
        if (type(data) is not dict or set(data) != _GENERATION_ARM_FIELDS
                or type(data["generation_count"]) is not int):
            _fail("WANDB_COST_REPORT_INVALID")
        values[f"{arm}_generation_count"] = _numeric(data["generation_count"], count=True)
        for key in ("total_estimated_cost_usd", "estimated_cost_per_generation_usd"):
            value = _numeric(data[key])
            if value is not None and value < 0:
                _fail("WANDB_COST_REPORT_INVALID")
            values[f"{arm}_{key}"] = value
        if (data["generation_count"] != result["compared_pair_count"]
                or any((data[key] is None) != (data["generation_count"] == 0)
                       for key in ("total_estimated_cost_usd", "estimated_cost_per_generation_usd"))):
            _fail("WANDB_COST_REPORT_INVALID")

    def reasons(codes):
        if (type(codes) is not list or any(type(code) is not str or
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code) is None for code in codes)
                or codes != sorted(set(codes))):
            _fail("WANDB_COST_REPORT_INVALID")
        return ",".join(codes)

    values["reason_codes"] = reasons(result["reason_codes"])
    excluded = result["excluded_records"]
    if type(excluded) is not list:
        _fail("WANDB_COST_REPORT_INVALID")
    excluded_ids = set()
    for record in excluded:
        if type(record) is not dict or set(record) != {"cost_id", "reason_codes"}:
            _fail("WANDB_COST_REPORT_INVALID")
        try:
            cost_id = validate_identifier(record["cost_id"])
        except ValueError:
            _fail("WANDB_COST_REPORT_INVALID")
        if cost_id in excluded_ids or not record["reason_codes"]:
            _fail("WANDB_COST_REPORT_INVALID")
        excluded_ids.add(cost_id)
        reasons(record["reason_codes"])
    if (result["planned_pair_count"] == 0
            or result["compared_record_count"] != result["compared_pair_count"] * 2
            or result["planned_pair_count"] != result["compared_pair_count"] + result["excluded_pair_count"]
            or result["input_record_count"] != result["compared_record_count"] + result["excluded_record_count"]
            or len(excluded) != result["excluded_record_count"]
            or result["planned_pair_count"] * 2 > result["input_record_count"]
            or (values["coverage_pct"] is None) != (result["input_record_count"] == 0)
            or (values["coverage_pct"] is not None and not 0 <= values["coverage_pct"] <= 100)
            or any((result[key] is None) != (result["compared_pair_count"] == 0)
                   for key in ("estimated_cost_difference_usd", "per_generation_difference_usd"))
            or (result["compared_pair_count"] == 0 and result["estimated_cost_reduction_pct"] is not None)):
        _fail("WANDB_COST_REPORT_INVALID")
    values.update({key: result[key] for key in ("comparison_id", "estimate_rate_version", "assurance")})
    return [values[key] for key in GENERATION_TABLE_COLUMNS]


def _report(raw: bytes) -> tuple[dict, list[list], list[list] | None]:
    report = _json(raw)
    _validate_tree(report)
    has_generation = "generation_comparison" in report
    expected_fields = _REPORT_FIELDS | ({"generation_comparison"} if has_generation else set())
    if (set(report) != expected_fields
            or report.get("schema_version") != "beautyai-cost-report-v1"
            or report.get("contract_version") != "beautyai-finops-v1"
            or type(report.get("report_id")) is not str
            or not _HASH.fullmatch(report["report_id"])
            or report.get("mode") not in ("OVERVIEW", "COMPARISON")
            or report.get("data_kind") not in ("SYNTHETIC_FIXTURE", "DECLARED_DATA")
            or type(report.get("scope")) is not dict
            or type(report.get("sources")) is not list
            or type(report.get("overview")) is not dict
            or type(report.get("comparison")) is not dict):
        _fail("WANDB_COST_REPORT_INVALID")
    inputs = report.get("inputs")
    expected_inputs = {"dataset_sha256", "comparison_sha256"} | (
        {"generation_comparison_sha256"} if has_generation else set())
    if (type(inputs) is not dict or set(inputs) != expected_inputs
            or type(inputs["dataset_sha256"]) is not str
            or not _HASH.fullmatch(inputs["dataset_sha256"])):
        _fail("WANDB_COST_REPORT_INVALID")
    comparison_hash = inputs["comparison_sha256"]
    if report["mode"] == "OVERVIEW":
        if comparison_hash is not None:
            _fail("WANDB_COST_REPORT_INVALID")
    elif type(comparison_hash) is not str or not _HASH.fullmatch(comparison_hash):
        _fail("WANDB_COST_REPORT_INVALID")
    generation_rows = None
    if has_generation:
        generation_hash = inputs["generation_comparison_sha256"]
        if type(generation_hash) is not str or not _HASH.fullmatch(generation_hash):
            _fail("WANDB_COST_REPORT_INVALID")
        generation_rows = [_generation_row(report["generation_comparison"])]
    rows = []
    for basis in ("estimated", "actual"):
        section = report["comparison"].get(basis)
        if type(section) is not dict:
            _fail("WANDB_COST_REPORT_INVALID")
        row = [basis]
        for arm in ("baseline", "candidate"):
            data = section.get(arm)
            if type(data) is not dict or not set(_ARM_METRICS).issubset(data):
                _fail("WANDB_COST_REPORT_INVALID")
            row.extend(_numeric(data[metric], count=not metric.endswith("_usd"))
                       for metric in _ARM_METRICS)
        for metric in ("unit_cost_reduction_pct", "counterfactual_difference_usd"):
            if metric not in section:
                _fail("WANDB_COST_REPORT_INVALID")
            row.append(_numeric(section[metric]))
        reasons = section.get("reason_codes")
        if (type(reasons) is not list or any(type(code) is not str or
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code) is None for code in reasons)):
            _fail("WANDB_COST_REPORT_INVALID")
        row.append(",".join(reasons))
        rows.append(row)
    return report, rows, generation_rows


def _identity(report: dict, file_hash: str) -> dict:
    return {key: report[key] for key in ("schema_version", "contract_version", "report_id", "mode", "data_kind")} | {
        "report_file_sha256": file_hash, **report["inputs"], "new_image_model_calls": 0,
    }


def _summary(identity: dict, rows: list[list], generation_rows: list[list] | None) -> dict:
    result = dict(identity, comparison_row_count=len(rows))
    for row in rows:
        result.update({f"{row[0]}_{key}": value for key, value in zip(TABLE_COLUMNS[1:], row[1:], strict=True)})
    if generation_rows is not None:
        result.update({f"generation_{key}": value for key, value in
                       zip(GENERATION_TABLE_COLUMNS, generation_rows[0], strict=True)})
    return result


def _same(actual: Any, expected: Any) -> bool:
    if type(expected) in (int, float):
        return type(actual) in (int, float) and math.isfinite(actual) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _check_fields(actual: Any, expected: dict, code: str, *, allow_missing_null: bool = False) -> None:
    actual = dict(actual)
    if any((key not in actual and not (allow_missing_null and value is None))
           or (key in actual and not _same(actual[key], value))
           for key, value in expected.items()):
        _fail(code)


def _find_run(api: Any, destination: str, run_id: str) -> Any:
    # Exhaustion, not a caught lookup exception, is the proof of absence. A
    # permission error on any lazy page must prevent creating a replacement.
    found = None
    for count, run in enumerate(api.runs(destination, per_page=100), start=1):
        if count > MAX_REMOTE_RUNS:
            _fail("WANDB_COST_REMOTE_LIST_LIMIT")
        if run.id == run_id:
            if found is not None:
                _fail("WANDB_COST_REMOTE_RUN_AMBIGUOUS")
            found = run
    return found


def _verify(api: Any, remote_path: str, run_id: str, identity: dict,
            summary: dict, rows: list[list], generation_rows: list[list] | None, root: Path) -> None:
    remote = api.run(remote_path)
    if remote.id != run_id or remote.state != "finished":
        _fail("WANDB_COST_REMOTE_RUN_UNFINISHED")
    _check_fields(remote.config, identity, "WANDB_COST_REMOTE_CONFIG_MISMATCH")
    # W&B omits null summary keys on readback; the exact report and Tables still
    # retain their null values and use strict verification below.
    _check_fields(remote.summary, summary, "WANDB_COST_REMOTE_SUMMARY_MISMATCH", allow_missing_null=True)
    artifacts = list(remote.logged_artifacts())
    reports = [artifact for artifact in artifacts if artifact.type == "cost-comparison"]
    if len(reports) != 1:
        _fail("WANDB_COST_REMOTE_ARTIFACT_MISMATCH")
    artifact = reports[0]
    _check_fields(artifact.metadata, identity, "WANDB_COST_REMOTE_ARTIFACT_MISMATCH")
    root = _safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    downloaded = _safe_path(Path(artifact.download(root=str(root))))
    if not downloaded.is_relative_to(root):
        _fail("WANDB_COST_REMOTE_DOWNLOAD_PATH_INVALID")
    raw = _read(downloaded / "report.json")
    if hashlib.sha256(raw).hexdigest() != identity["report_file_sha256"]:
        _fail("WANDB_COST_REMOTE_FILE_MISMATCH")
    _verify_table(artifacts, "comparison", TABLE_COLUMNS, rows)
    if generation_rows is not None:
        _verify_table(artifacts, "generation_cost_estimate", GENERATION_TABLE_COLUMNS, generation_rows)


def _verify_table(artifacts: list, key: str, columns: list[str], rows: list[list]) -> None:
    tables = [artifact for artifact in artifacts if artifact.type == "run_table"
              and artifact.name.split(":", 1)[0].endswith("-" + key)]
    if len(tables) != 1:
        _fail("WANDB_COST_REMOTE_TABLE_MISMATCH")
    table = tables[0].get(key)
    if table is None or list(table.columns) != columns or len(table.data) != len(rows):
        _fail("WANDB_COST_REMOTE_TABLE_MISMATCH")
    for actual, expected in zip(table.data, rows, strict=True):
        if len(actual) != len(expected) or any(not _same(a, b) for a, b in zip(actual, expected, strict=True)):
            _fail("WANDB_COST_REMOTE_TABLE_MISMATCH")


def _previous(receipt_path: Path) -> dict | None:
    if not receipt_path.exists():
        return None
    previous = _json(_read(receipt_path))
    if previous.get("schema_version") != SCHEMA_VERSION:
        _fail("WANDB_COST_RECEIPT_INVALID")
    if previous.get("status") != "READBACK_VERIFIED":
        previous = previous.get("previous_verified_receipt")
    if previous is None:
        return None
    keys = ("schema_version", "status", "report_id", "report_file_sha256", "entity", "project",
            "run_id", "overview_url", "finished_at", "readback_verified")
    result = {key: previous[key] for key in keys if key in previous}
    _validate_tree(result)
    return result


def sync_cost_report(report_path: Path, *, entity: str, project: str,
                     publish: bool = False, sdk: Any = None, api: Any = None) -> dict:
    """Prepare or explicitly publish the exact saved report, with safe receipts.

    Repeating a verified publication performs remote readback without init,
    upload, or image calls. A changed/unfinished/unreachable remote is INCOMPLETE.
    """
    if any(type(value) is not str or not _DESTINATION.fullmatch(value) for value in (entity, project)):
        raise ValueError("WANDB_DESTINATION_INVALID")
    if type(publish) is not bool:
        raise ValueError("WANDB_PUBLISH_FLAG_INVALID")
    receipt = {"schema_version": SCHEMA_VERSION, "entity": entity, "project": project,
               "status": "PREPARED", "readback_verified": False, "new_image_model_calls": 0,
               "started_at": datetime.now(timezone.utc).isoformat()}
    receipt_path = None
    stage = None
    try:
        source = _safe_path(report_path)
        receipt_path = _safe_path(source.parent / "cost-wandb-sync.json")
        if source == receipt_path:
            _fail("WANDB_COST_UNSAFE_PATH")
        previous = _previous(receipt_path)
        if previous:
            receipt["previous_verified_receipt"] = previous
        raw = _read(source)
        report, rows, generation_rows = _report(raw)
        file_hash = hashlib.sha256(raw).hexdigest()
        identity = _identity(report, file_hash)
        summary = _summary(identity, rows, generation_rows)
        run_id = "compare-" + report["report_id"][:20]
        receipt.update(report_id=report["report_id"], report_file_sha256=file_hash,
                       run_id=run_id, comparison_row_count=len(rows), deduplicated=False)
        stage = _safe_path(source.parent / "cost-wandb" / report["report_id"])
        staged_report = _safe_path(stage / "report.json")
        if staged_report.exists():
            if _read(staged_report) != raw:
                _fail("WANDB_COST_STAGED_REPORT_MISMATCH")
        else:
            _write(staged_report, raw)
        _write(receipt_path, canonical_bytes(receipt) + b"\n")
        if publish:
            if api is None:
                if sdk is None:
                    import wandb as sdk
                api = sdk.Api(timeout=45)
            try:
                privacy = ensure_private_project(api, entity, project)
            except ValueError:
                _fail("WANDB_COST_PROJECT_PRIVACY_UNVERIFIED")
            if privacy.get("access") != "PRIVATE":
                _fail("WANDB_COST_PROJECT_PRIVACY_UNVERIFIED")
            receipt["project_access"] = "PRIVATE"
            existing = _find_run(api, f"{entity}/{project}", run_id)
            if _read(source) != raw or _read(staged_report) != raw:
                _fail("WANDB_COST_STAGED_REPORT_MISMATCH")
            if existing is None:
                if sdk is None:
                    import wandb as sdk
                settings = sdk.Settings(console="off", disable_code=True, disable_git=True, save_code=False,
                                        x_disable_meta=True, x_disable_stats=True, silent=True,
                                        init_timeout=60, finish_timeout=120, finish_timeout_raises=True)
                run = sdk.init(entity=entity, project=project, id=run_id, resume="never", mode="online",
                               name=f"Cost comparison / {report['report_id'][:12]}",
                               job_type="cost-comparison", tags=["beautyai", "finops", "declared-evidence"],
                               config=identity, dir=str(stage), settings=settings)
                try:
                    if _read(staged_report) != raw:
                        _fail("WANDB_COST_STAGED_REPORT_MISMATCH")
                    artifact = sdk.Artifact(run_id, type="cost-comparison", metadata=identity)
                    artifact.add_file(str(staged_report), name="report.json")
                    run.log_artifact(artifact)
                    run.log({"comparison": sdk.Table(columns=TABLE_COLUMNS, data=rows)})
                    if generation_rows is not None:
                        run.log({"generation_cost_estimate": sdk.Table(
                            columns=GENERATION_TABLE_COLUMNS, data=generation_rows)})
                    run.summary.update(summary)
                except Exception:
                    try:
                        run.finish(exit_code=1)
                    except Exception:
                        pass
                    raise
                run.finish()
                receipt["status"] = "UPLOADED"
                _write(receipt_path, canonical_bytes(receipt) + b"\n")
            else:
                receipt["deduplicated"] = True
            api.flush()
            _verify(api, f"{entity}/{project}/{run_id}", run_id, identity, summary, rows,
                    generation_rows, stage / "readback")
            receipt.update(status="READBACK_VERIFIED", readback_verified=True,
                           overview_url=f"https://wandb.ai/{entity}/{project}/runs/{run_id}")
    except _SyncError as error:
        receipt.update(status="INCOMPLETE", error_code=str(error), readback_verified=False)
    except Exception:
        receipt.update(status="INCOMPLETE", error_code="WANDB_COST_SYNC_FAILED", readback_verified=False)
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    if receipt_path is not None:
        try:
            if receipt["status"] == "READBACK_VERIFIED":
                verified = _safe_path(stage / "verified-receipt.json")
                if not verified.exists():
                    _write(verified, canonical_bytes(receipt) + b"\n")
            _write(receipt_path, canonical_bytes(receipt) + b"\n")
        except Exception:
            receipt.update(status="INCOMPLETE", error_code="WANDB_COST_RECEIPT_WRITE_FAILED", readback_verified=False)
            try:
                _write(receipt_path, canonical_bytes(receipt) + b"\n")
            except Exception:
                pass
    return receipt
