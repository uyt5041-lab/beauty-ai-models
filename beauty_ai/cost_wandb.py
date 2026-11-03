"""Explicit W&B publication of saved cost reports; never an image executor.

The exact JSON artifact retains decimal strings. W&B numbers are derived display
values only. An inaccessible or unfinished remote run is never permission to
overwrite it, and local preparation never authenticates or contacts W&B.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
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
from .work_contracts import (STAGES, normalize_attempt, normalize_receipt, normalize_uuid,
                             usd_string)
from .work_metrics import (WORK_TABLE_COLUMNS as _WORK_COLUMNS,
                           STAGE_TABLE_COLUMNS as _STAGE_COLUMNS, compare_work_rows, summarize_work_dataset)


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
WORK_TABLE_COLUMNS = list(_WORK_COLUMNS)
STAGE_TABLE_COLUMNS = list(_STAGE_COLUMNS)
_WORK_SUMMARY_FIELDS = {
    "work_count", "completed_work_count", "open_work_count", "cancelled_work_count", "completed_ad_count",
    "estimated_total_usd", "known_partial_estimated_usd", "actual_total_usd", "known_partial_actual_usd",
    "input_tokens", "output_tokens", "known_input_tokens", "known_output_tokens", "missing_usage_count",
    "missing_cost_count", "cost_per_completed_ad_usd",
}
_WORK_COMPARISON_FIELDS = {
    "scope", "planned_pair_count", "compared_pair_count", "excluded_pair_count", "excluded_work_count",
    "baseline", "candidate", "estimated_cost_reduction_pct", "estimated_cost_difference_usd",
    "excluded_pairs", "reason_codes",
}
_WORK_REASONS = {
    "UNPAIRED_WORK", "NOT_COMPLETED", "PRODUCT_MISMATCH", "QUALITY_MISMATCH", "STAGES_MISMATCH",
    "COST_MISSING", "RATE_MISMATCH", "DUPLICATE_FINAL_ARTIFACT", "NO_COMPARABLE_PAIRS", "BASELINE_ZERO_COST",
}
_WORK_MONEY = re.compile(r"-?(?:0|[1-9][0-9]{0,99})\.[0-9]{9}\Z")
_WORK_OPTIONAL_TOKENS = {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"}


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


def _validate_tree(value: Any, depth: int = 0, *, work: bool = False) -> None:
    if depth > 20:
        _fail("WANDB_COST_REPORT_INVALID")
    if type(value) is dict:
        for key, child in value.items():
            if _SECRET_KEY.search(key):
                _fail("WANDB_COST_REPORT_PRIVATE_DATA")
            _validate_tree(child, depth + 1, work=work)
    elif type(value) is list:
        for child in value:
            _validate_tree(child, depth + 1, work=work)
    elif type(value) is str:
        if len(value) > 4096 or _SECRET.search(value):
            _fail("WANDB_COST_REPORT_PRIVATE_DATA")
    elif work and type(value) is float and math.isfinite(value):
        pass  # Strict receipt validation below restricts floats to elapsed_seconds.
    elif value is not None and type(value) not in (int, bool):
        # Decimal source amounts must remain strings, including in the artifact.
        _fail("WANDB_COST_REPORT_INVALID")


def _work_count(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        _fail("WANDB_COST_REPORT_INVALID")
    if value > 2**53:
        _fail("WORK_WANDB_COUNT_RANGE")
    return value


def _work_money(value, *, nullable=True, signed=False):
    if value is None and nullable:
        return None
    if (type(value) is not str or not _WORK_MONEY.fullmatch(value)
            or not signed and value.startswith("-")):
        _fail("WANDB_COST_REPORT_INVALID")
    return _numeric(value)


def _work_cell(key, value):
    if key.endswith("_usd"):
        return _work_money(value, nullable=not key.startswith("known_"))
    if key.endswith("_count") or key.endswith("_tokens"):
        return _work_count(value, nullable=key in _WORK_OPTIONAL_TOKENS)
    return value


def _work_reasons(value):
    if (type(value) is not list or any(type(code) is not str or code not in _WORK_REASONS for code in value)
            or value != sorted(set(value))):
        _fail("WANDB_COST_REPORT_INVALID")


def _work_rows(result) -> tuple[list[list], list[list], dict]:
    """Validate saved work facts and derived wire values before any remote IO."""
    try:
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            return _validated_work_rows(result)
    except _SyncError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _fail("WANDB_COST_REPORT_INVALID")


def _validated_work_rows(result):
    if (type(result) is not dict or set(result) != {"schema_version", "scope", "authority", "summary", "works", "comparison"}
            or result["schema_version"] != "beautyai-work-summary-v1"
            or result["scope"] != "MODEL_API_COST_ONLY" or result["authority"] != "DECLARED_WORK_ACCEPTANCE"
            or type(result["summary"]) is not dict or set(result["summary"]) != _WORK_SUMMARY_FIELDS
            or type(result["works"]) is not list or len(result["works"]) > 1000):
        _fail("WANDB_COST_REPORT_INVALID")
    summary = {key: _work_cell(key, value) for key, value in result["summary"].items()}
    works, attempts, receipts, stage_rows = [], [], [], []
    identities = set()
    for row in result["works"]:
        if (type(row) is not dict or set(row) != set(WORK_TABLE_COLUMNS) | {
                "stages", "attempts", "required_stages", "quality_policy_version", "rate_versions"}
                or type(row["stages"]) is not list or type(row["attempts"]) is not list
                or len(row["attempts"]) > 1000 or type(row["rate_versions"]) is not list):
            _fail("WANDB_COST_REPORT_INVALID")
        for key in WORK_TABLE_COLUMNS:
            _work_cell(key, row[key])
        normalize_uuid(row["work_id"])
        for version in row["rate_versions"]:
            validate_identifier(version)
        if row["rate_versions"] != sorted(set(row["rate_versions"])):
            _fail("WANDB_COST_REPORT_INVALID")
        stamps, row_ids = [], []
        for detail in row["attempts"]:
            if type(detail) is not dict or "receipt" not in detail:
                _fail("WANDB_COST_REPORT_INVALID")
            identity = normalize_attempt({key: value for key, value in detail.items() if key != "receipt"})
            if identity["work_id"] != row["work_id"] or identity["attempt_id"] in identities:
                _fail("WANDB_COST_REPORT_INVALID")
            identities.add(identity["attempt_id"])
            attempts.append(identity)
            row_ids.append(identity["attempt_id"])
            stamps.append(identity["started_at"])
            if detail["receipt"] is not None:
                receipt = normalize_receipt(detail["receipt"])
                if receipt != detail["receipt"] or receipt["attempt_id"] != identity["attempt_id"]:
                    _fail("WANDB_COST_REPORT_INVALID")
                if receipt["usage"] is not None:
                    for token in receipt["usage"].values():
                        _work_count(token, nullable=True)
                receipts.append(receipt)
        for stage in row["stages"]:
            if type(stage) is not dict or set(stage) != set(STAGE_TABLE_COLUMNS) or stage["work_id"] != row["work_id"]:
                _fail("WANDB_COST_REPORT_INVALID")
            if stage["stage"] not in STAGES:
                _fail("WANDB_COST_REPORT_INVALID")
            for key in ("provider", "model"):
                validate_identifier(stage[key])
            stage_rows.append([_work_cell(key, stage[key]) for key in STAGE_TABLE_COLUMNS])
        if row["quality_policy_version"] is not None:
            validate_identifier(row["quality_policy_version"])
        stamp = min(stamps) if stamps else "1970-01-01T00:00:00+00:00"
        project = row["attempts"][0]["project_id"] if row["attempts"] else None
        works.append({"work_id": row["work_id"], "project_id": project, "product_ref": row["product_ref"],
            "task_pair_id": row["task_pair_id"], "arm": row["arm"], "required_stages": row["required_stages"],
            "status": row["status"], "created_at": stamp,
            "closed_at": None if row["status"] == "OPEN" else max(stamps, default=stamp),
            "completion": ({"artifact_sha256": row["final_artifact_sha256"], "reviewer_ref": "declared-reviewer",
                "quality_policy_version": row["quality_policy_version"], "quality_pass": True,
                "expected_attempt_ids": row_ids} if row["status"] == "COMPLETED" else None)})
        if row["status"] != "COMPLETED" and (row["final_artifact_sha256"] is not None or row["quality_policy_version"] is not None):
            _fail("WANDB_COST_REPORT_INVALID")
    project = attempts[0]["project_id"] if attempts else "00000000-0000-0000-0000-000000000000"
    for work in works:
        work["project_id"] = work["project_id"] or project
    derived = summarize_work_dataset({"schema_version": "beautyai-work-dataset-v1", "data_kind": "DECLARED_DATA",
        "works": works, "attempts": attempts, "receipts": receipts, "image_bindings": [], "rate_book": None})
    expected_rows = {row["work_id"]: row for row in derived["works"]}
    for row in result["works"]:
        expected = expected_rows[row["work_id"]]
        unobserved = row["unobserved_image_job_count"]
        if row["status"] == "COMPLETED" and unobserved:
            _fail("WANDB_COST_REPORT_INVALID")
        for key in WORK_TABLE_COLUMNS:
            if key.endswith("_usd") or key in {"unobserved_image_job_count", "missing_cost_count"}:
                continue
            value = expected[key]
            if key == "missing_usage_count":
                value += unobserved
            elif key in {"input_tokens", "output_tokens"} and unobserved:
                value = None
            if row[key] != value or type(row[key]) is not type(value):
                _fail("WANDB_COST_REPORT_INVALID")
        if row["required_stages"] != expected["required_stages"]:
            _fail("WANDB_COST_REPORT_INVALID")
        if not set(expected["rate_versions"]).issubset(row["rate_versions"]) or (
                row["missing_cost_count"] == expected["missing_cost_count"] + unobserved
                and row["rate_versions"] != expected["rate_versions"]):
            _fail("WANDB_COST_REPORT_INVALID")
        if len(row["stages"]) != len(expected["stages"]):
            _fail("WANDB_COST_REPORT_INVALID")
        for actual_stage, expected_stage in zip(row["stages"], expected["stages"], strict=True):
            for key in STAGE_TABLE_COLUMNS:
                if not key.endswith("_usd") and key != "missing_cost_count" and actual_stage[key] != expected_stage[key]:
                    _fail("WANDB_COST_REPORT_INVALID")
            members = [detail for detail in row["attempts"]
                       if detail["stage"] == actual_stage["stage"]
                       and detail["provider"] == actual_stage["provider"]
                       and (detail["receipt"]["model"] if detail["receipt"] else detail["configured_model"]) == actual_stage["model"]]
            _check_work_estimates(actual_stage, expected_stage, members, 0)
        _check_work_estimates(row, expected, row["attempts"], unobserved)
        if (Decimal(row["known_partial_estimated_usd"]) != sum(
                (Decimal(stage["known_partial_estimated_usd"]) for stage in row["stages"]), Decimal(0))
                or row["missing_cost_count"] != unobserved + sum(stage["missing_cost_count"] for stage in row["stages"])):
            _fail("WANDB_COST_REPORT_INVALID")
        for key in ("actual_total_usd", "known_partial_actual_usd"):
            value = None if key == "actual_total_usd" and unobserved else expected[key]
            if row[key] != value:
                _fail("WANDB_COST_REPORT_INVALID")
    totals = result["summary"]
    for key, value in derived["summary"].items():
        if key.endswith("_usd") or key in {"missing_cost_count", "missing_usage_count", "input_tokens", "output_tokens"}:
            continue
        if totals[key] != value:
            _fail("WANDB_COST_REPORT_INVALID")
    for key in ("missing_usage_count", "missing_cost_count"):
        if totals[key] != sum(row[key] for row in result["works"]):
            _fail("WANDB_COST_REPORT_INVALID")
    for key in ("input_tokens", "output_tokens", "estimated_total_usd", "actual_total_usd"):
        values = [row[key] for row in result["works"]]
        if key.endswith("_usd"):
            expected = None if None in values else usd_string(sum((Decimal(value) for value in values), Decimal(0)))
        else:
            expected = None if None in values else sum(values)
        if totals[key] != expected:
            _fail("WANDB_COST_REPORT_INVALID")
    for key in ("known_partial_estimated_usd", "known_partial_actual_usd"):
        if totals[key] != usd_string(sum((Decimal(row[key]) for row in result["works"]), Decimal(0))):
            _fail("WANDB_COST_REPORT_INVALID")
    expected_unit = (None if totals["estimated_total_usd"] is None or not totals["completed_ad_count"] else
                     usd_string(Decimal(totals["estimated_total_usd"]) / totals["completed_ad_count"]))
    if totals["cost_per_completed_ad_usd"] != expected_unit:
        _fail("WANDB_COST_REPORT_INVALID")
    _validate_work_comparison(result["comparison"], result["works"])
    work_summary = {f"work_{key}": value for key, value in summary.items()}
    work_summary.update(
        work_comparison_estimated_cost_reduction_pct=_work_money(
            result["comparison"]["estimated_cost_reduction_pct"], signed=True),
        work_compared_pair_count=result["comparison"]["compared_pair_count"],
        work_excluded_pair_count=result["comparison"]["excluded_pair_count"])
    return ([[_work_cell(key, row[key]) for key in WORK_TABLE_COLUMNS] for row in result["works"]],
            stage_rows, work_summary)


def _check_work_estimates(row, expected, details, unobserved):
    missing = row["missing_cost_count"]
    forced_missing = unobserved
    for detail in details:
        receipt = detail["receipt"]
        if receipt is None or receipt["estimated_cost_usd"] is None and (
                receipt["usage"] is None or receipt["usage"]["input_tokens"] is None
                or receipt["usage"]["output_tokens"] is None):
            forced_missing += 1
    if (not forced_missing <= missing <= expected["missing_cost_count"] + unobserved
            or Decimal(row["known_partial_estimated_usd"]) < Decimal(expected["known_partial_estimated_usd"])):
        _fail("WANDB_COST_REPORT_INVALID")
    if (row["estimated_total_usd"] is None) != (missing != 0):
        _fail("WANDB_COST_REPORT_INVALID")
    if not missing and row["estimated_total_usd"] != row["known_partial_estimated_usd"]:
        _fail("WANDB_COST_REPORT_INVALID")
    # Without any newly priced receipt, the saved subtotal must equal the
    # explicit receipt facts. A rate snapshot may price previously missing facts.
    if missing == expected["missing_cost_count"] + unobserved and (
            row["known_partial_estimated_usd"] != expected["known_partial_estimated_usd"]):
        _fail("WANDB_COST_REPORT_INVALID")


def _validate_work_comparison(comparison, rows):
    if (type(comparison) is not dict or set(comparison) != _WORK_COMPARISON_FIELDS
            or comparison["scope"] != "MATCHED_COMPLETED_WORKS" or type(comparison["excluded_pairs"]) is not list):
        _fail("WANDB_COST_REPORT_INVALID")
    for key in ("planned_pair_count", "compared_pair_count", "excluded_pair_count", "excluded_work_count"):
        _work_count(comparison[key])
    for key in ("estimated_cost_reduction_pct", "estimated_cost_difference_usd"):
        _work_money(comparison[key], signed=True)
    _work_reasons(comparison["reason_codes"])
    for pair in comparison["excluded_pairs"]:
        if type(pair) is not dict or set(pair) != {"task_pair_id", "work_ids", "reason_codes"}:
            _fail("WANDB_COST_REPORT_INVALID")
        if pair["task_pair_id"] is not None:
            validate_identifier(pair["task_pair_id"])
        if type(pair["work_ids"]) is not list or not pair["work_ids"]:
            _fail("WANDB_COST_REPORT_INVALID")
        for work_id in pair["work_ids"]:
            normalize_uuid(work_id)
        _work_reasons(pair["reason_codes"])
    for arm in ("baseline", "candidate"):
        data = comparison[arm]
        if type(data) is not dict or set(data) != {
                "completed_work_count", "total_estimated_cost_usd", "estimated_cost_per_completed_ad_usd"}:
            _fail("WANDB_COST_REPORT_INVALID")
        _work_count(data["completed_work_count"])
        _work_money(data["total_estimated_cost_usd"])
        _work_money(data["estimated_cost_per_completed_ad_usd"])
    # The pure shared comparison owns eligibility, exclusions and exact amounts.
    if comparison != compare_work_rows(rows):
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


def _report(raw: bytes) -> tuple[dict, list[list], list[list] | None, tuple | None]:
    report = _json(raw)
    _validate_tree({key: value for key, value in report.items() if key != "work_costs"})
    has_generation = "generation_comparison" in report
    has_work = "work_costs" in report
    expected_fields = (_REPORT_FIELDS | ({"generation_comparison"} if has_generation else set())
                       | ({"work_costs"} if has_work else set()))
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
    expected_inputs = ({"dataset_sha256", "comparison_sha256"}
                       | ({"generation_comparison_sha256"} if has_generation else set())
                       | ({"work_dataset_sha256"} if has_work else set()))
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
    work_data = None
    if has_work:
        work_hash = inputs["work_dataset_sha256"]
        if type(work_hash) is not str or not _HASH.fullmatch(work_hash):
            _fail("WANDB_COST_REPORT_INVALID")
        _validate_tree(report["work_costs"], work=True)
        work_data = _work_rows(report["work_costs"])
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
    return report, rows, generation_rows, work_data


def _identity(report: dict, file_hash: str) -> dict:
    return {key: report[key] for key in ("schema_version", "contract_version", "report_id", "mode", "data_kind")} | {
        "report_file_sha256": file_hash, **report["inputs"], "new_image_model_calls": 0,
    }


def _summary(identity: dict, rows: list[list], generation_rows: list[list] | None,
             work_data: tuple | None = None) -> dict:
    result = dict(identity, comparison_row_count=len(rows))
    for row in rows:
        result.update({f"{row[0]}_{key}": value for key, value in zip(TABLE_COLUMNS[1:], row[1:], strict=True)})
    if generation_rows is not None:
        result.update({f"generation_{key}": value for key, value in
                       zip(GENERATION_TABLE_COLUMNS, generation_rows[0], strict=True)})
    if work_data is not None:
        result.update(work_data[2])
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
            summary: dict, rows: list[list], generation_rows: list[list] | None, root: Path,
            work_data: tuple | None = None) -> None:
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
    if work_data is not None:
        _verify_table(artifacts, "work_costs", WORK_TABLE_COLUMNS, work_data[0])
        _verify_table(artifacts, "work_stage_usage", STAGE_TABLE_COLUMNS, work_data[1])


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
        report, rows, generation_rows, work_data = _report(raw)
        file_hash = hashlib.sha256(raw).hexdigest()
        identity = _identity(report, file_hash)
        summary = _summary(identity, rows, generation_rows, work_data)
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
                    if work_data is not None:
                        run.log({"work_costs": sdk.Table(columns=WORK_TABLE_COLUMNS, data=work_data[0])})
                        run.log({"work_stage_usage": sdk.Table(columns=STAGE_TABLE_COLUMNS, data=work_data[1])})
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
                    generation_rows, stage / "readback", work_data)
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
