"""Pure, detached validation for declared advertising-work accounting records.

No acceptance, provider billing, or external identity is attested by this module.
All validation failures expose only bounded WORK_* codes, never source values.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import re
from uuid import UUID

from . import cost_accounting as _cost

DATASET_SCHEMA_VERSION = "beautyai-work-dataset-v1"
RECEIPT_SCHEMA_VERSION = "beautyai-work-receipt-v1"
RATE_SCHEMA_VERSION = "beautyai-work-rates-v1"
SUMMARY_SCHEMA_VERSION = "beautyai-work-summary-v1"
SCHEMA_VERSION = DATASET_SCHEMA_VERSION
STAGES = ("prompt_build", "image_generation", "review", "revision", "composition")
DEFAULT_REQUIRED_STAGES = ("prompt_build", "image_generation", "review", "composition")
MAX_ATTEMPTS_PER_WORK = 1000
MAX_WORKS = 1000
MAX_RECEIPTS = 20000
MAX_JSON_BYTES = 1024 * 1024
MAX_TOKEN_COUNT = 2**63 - 1
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "input_text_tokens",
              "input_image_tokens", "cached_input_tokens", "reasoning_tokens")
TEXT_OPERATION_STAGES = {"analyze_product": "prompt_build", "draft_campaign": "prompt_build",
                         "review_copy": "review", "rewrite_copy": "revision"}
_WORK_KEYS = {"work_id", "project_id", "product_ref", "task_pair_id", "arm",
              "required_stages", "status", "created_at", "closed_at", "completion"}
_CREATE_KEYS = {"product_ref", "task_pair_id", "arm", "required_stages"}
_ATTEMPT_SPEC_KEYS = {"attempt_id", "stage", "operation", "provider", "configured_model",
                      "prompt_version", "request_hash", "parent_attempt_id"}
_ATTEMPT_KEYS = _ATTEMPT_SPEC_KEYS | {"work_id", "project_id", "started_at"}
_RECEIPT_KEYS = {"schema_version", "attempt_id", "provider_request_id", "model", "usage",
                 "elapsed_seconds", "status", "error_code", "artifact_sha256",
                 "estimated_cost_usd", "rate_version", "actual_cost_usd",
                 "billing_evidence_ref", "source_ref", "source_sha256"}
_COMPLETION_KEYS = {"artifact_sha256", "reviewer_ref", "quality_policy_version",
                    "quality_pass", "expected_attempt_ids"}
_RATE_KEYS = {"mode", "input_usd", "input_text_usd", "input_image_usd", "output_usd"}
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


def _error(reason: str) -> ValueError:
    return ValueError("WORK_" + reason)


def _wrapped(function, *args):
    try:
        return function(*args)
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise _error("VALUE_INVALID") from None


def canonical_bytes(value) -> bytes:
    """Reuse the accounting JSON codec, replacing its private error namespace."""
    return _wrapped(_cost.canonical_bytes, value)


def digest(value) -> str:
    return _wrapped(_cost.digest, value)


def usd_decimal(value: str) -> Decimal:
    return _wrapped(_cost.usd_decimal, value)


def usd_string(value: Decimal) -> str:
    return _wrapped(_cost.usd_string, value)


def validate_identifier(value) -> str:
    return _wrapped(_cost.validate_identifier, value)


def validate_hash(value) -> str:
    if type(value) is not str or not _HASH.fullmatch(value):
        raise _error("HASH_INVALID")
    return value


def normalize_uuid(value) -> str:
    """Return a canonical UUID string; UUID objects are accepted for store APIs."""
    if isinstance(value, UUID):
        return str(value)
    if type(value) is not str:
        raise _error("ID_INVALID")
    try:
        result = UUID(value)
    except (ValueError, AttributeError):
        raise _error("ID_INVALID") from None
    if str(result) != value:
        raise _error("ID_INVALID")
    return value


def validate_timestamp(value) -> str:
    """Require timezone-aware ISO timestamps and normalize the instant to UTC."""
    if type(value) is not str or not _DATETIME.fullmatch(value):
        raise _error("TIMESTAMP_INVALID")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        raise _error("TIMESTAMP_INVALID") from None


def _tree(value, depth=0):
    if depth > 20:
        raise _error("INPUT_INVALID")
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise _error("INPUT_INVALID")
            if _cost._SECRET.search(key):
                raise _error("PRIVATE_DATA")
            _tree(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _tree(child, depth + 1)
    elif type(value) is str:
        if len(value) > 4096 or _cost._SECRET.search(value):
            raise _error("PRIVATE_DATA")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise _error("INPUT_INVALID") from None
    elif type(value) is float:
        if not math.isfinite(value):
            raise _error("INPUT_INVALID")
    elif value is not None and type(value) not in (int, bool):
        raise _error("INPUT_INVALID")


def parse_work_json(raw: bytes | str):
    """Read one bounded JSON value with duplicate-key and nonfinite rejection."""
    if type(raw) not in (str, bytes):
        raise _error("INPUT_INVALID")
    try:
        encoded = raw.encode("utf-8") if type(raw) is str else raw
        if len(encoded) > MAX_JSON_BYTES:
            raise _error("INPUT_TOO_LARGE")

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise _error("INPUT_INVALID")
                result[key] = value
            return result

        def invalid(_):
            raise _error("INPUT_INVALID")

        result = json.loads(encoded, object_pairs_hook=unique, parse_constant=invalid)
        _tree(result)
        return result
    except ValueError as error:
        if re.fullmatch(r"WORK_[A-Z_]+", str(error)):
            raise
        raise _error("INPUT_INVALID") from None
    except (UnicodeError, TypeError, RecursionError, OverflowError):
        raise _error("INPUT_INVALID") from None


def _fields(data, expected, *, required=None):
    if (type(data) is not dict or not set(data).issubset(expected)
            or not (expected if required is None else required).issubset(data)):
        raise _error("SCHEMA_INVALID")
    _tree(data)


def _choice(value, values):
    if type(value) is not str or value not in values:
        raise _error("SCHEMA_INVALID")
    return value


def _nullable(value, validator):
    return None if value is None else validator(value)


def _list(value, maximum):
    if type(value) is not list:
        raise _error("SCHEMA_INVALID")
    if len(value) > maximum:
        raise _error("LIMIT_EXCEEDED")
    return value


def _stages(value):
    if value is None:
        return list(DEFAULT_REQUIRED_STAGES)
    values = [_choice(stage, STAGES) for stage in _list(value, len(STAGES))]
    if not values or len(set(values)) != len(values):
        raise _error("STAGES_INVALID")
    return [stage for stage in STAGES if stage in values]


def normalize_create_spec(data: dict) -> dict:
    _fields(data, _CREATE_KEYS, required={"product_ref"})
    return {"product_ref": validate_identifier(data["product_ref"]),
            "task_pair_id": _nullable(data.get("task_pair_id"), validate_identifier),
            "arm": _nullable(data.get("arm"), lambda value: _choice(value, {"baseline", "candidate"})),
            "required_stages": _stages(data.get("required_stages"))}


def normalize_attempt_spec(data: dict) -> dict:
    _fields(data, _ATTEMPT_SPEC_KEYS, required=_ATTEMPT_SPEC_KEYS - {"parent_attempt_id"})
    result = {key: validate_identifier(data[key]) for key in
              ("operation", "provider", "configured_model", "prompt_version")}
    result.update(attempt_id=normalize_uuid(data["attempt_id"]), stage=_choice(data["stage"], STAGES),
                  request_hash=validate_hash(data["request_hash"]),
                  parent_attempt_id=_nullable(data.get("parent_attempt_id"), normalize_uuid))
    if result["parent_attempt_id"] == result["attempt_id"]:
        raise _error("PARENT_INVALID")
    if (result["operation"] in TEXT_OPERATION_STAGES
            and result["stage"] != TEXT_OPERATION_STAGES[result["operation"]]):
        raise _error("STAGES_INVALID")
    if (result["provider"] == "local") != (result["configured_model"] == "local"):
        raise _error("LOCAL_INVALID")
    return result


def normalize_attempt(data: dict) -> dict:
    _fields(data, _ATTEMPT_KEYS)
    result = normalize_attempt_spec({key: data[key] for key in _ATTEMPT_SPEC_KEYS})
    result.update(project_id=normalize_uuid(data["project_id"]), work_id=normalize_uuid(data["work_id"]),
                  started_at=validate_timestamp(data["started_at"]))
    return result


def normalize_usage(data: dict | None) -> dict | None:
    if data is None:
        return None
    _fields(data, set(USAGE_KEYS), required=set())
    result = {key: data.get(key) for key in USAGE_KEYS}
    for value in result.values():
        if value is not None and (type(value) is not int or not 0 <= value <= MAX_TOKEN_COUNT):
            raise _error("USAGE_INVALID")
    input_count, output_count, total = (result[key] for key in USAGE_KEYS[:3])
    if total is not None:
        known = [value for value in (input_count, output_count) if value is not None]
        if sum(known) > total or (len(known) == 2 and sum(known) != total):
            raise _error("USAGE_INVALID")
    for part, whole in ((result["cached_input_tokens"], input_count),
                        (result["reasoning_tokens"], output_count)):
        if part is not None and whole is not None and part > whole:
            raise _error("USAGE_INVALID")
    split = [result[key] for key in ("input_text_tokens", "input_image_tokens")]
    if input_count is not None and sum(value for value in split if value is not None) > input_count:
        raise _error("USAGE_INVALID")
    return result


def normalize_receipt(data: dict) -> dict:
    _fields(data, _RECEIPT_KEYS)
    if data["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise _error("SCHEMA_INVALID")
    result = {"schema_version": RECEIPT_SCHEMA_VERSION,
              "attempt_id": normalize_uuid(data["attempt_id"]),
              "provider_request_id": _nullable(data["provider_request_id"], validate_identifier),
              "model": validate_identifier(data["model"]),
              "usage": normalize_usage(data["usage"]),
              "elapsed_seconds": data["elapsed_seconds"],
              "status": _choice(data["status"], {"SUCCEEDED", "FAILED", "UNKNOWN"}),
              "error_code": data["error_code"],
              "artifact_sha256": _nullable(data["artifact_sha256"], validate_hash),
              "source_ref": validate_identifier(data["source_ref"]),
              "source_sha256": validate_hash(data["source_sha256"])}
    elapsed = result["elapsed_seconds"]
    if elapsed is not None:
        try:
            valid_elapsed = type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0
        except (ValueError, OverflowError):
            valid_elapsed = False
        if not valid_elapsed:
            raise _error("DURATION_INVALID")
    code = result["error_code"]
    if code is not None and (type(code) is not str or not _ERROR_CODE.fullmatch(code)):
        raise _error("ERROR_CODE_INVALID")
    if result["status"] == "SUCCEEDED" and code is not None:
        raise _error("RECEIPT_INVALID")
    for amount_key, evidence_key in (("estimated_cost_usd", "rate_version"),
                                     ("actual_cost_usd", "billing_evidence_ref")):
        result[amount_key] = _nullable(data[amount_key], lambda amount: usd_string(usd_decimal(amount)))
        result[evidence_key] = _nullable(data[evidence_key], validate_identifier)
        if result[amount_key] is not None and result[evidence_key] is None:
            raise _error("EVIDENCE_REQUIRED")
    return result


def normalize_completion(data: dict) -> dict:
    _fields(data, _COMPLETION_KEYS)
    if data["quality_pass"] is not True:
        raise _error("COMPLETION_INVALID")
    expected = [normalize_uuid(value) for value in _list(data["expected_attempt_ids"], MAX_ATTEMPTS_PER_WORK)]
    if len(set(expected)) != len(expected):
        raise _error("COMPLETION_INVALID")
    return {"artifact_sha256": validate_hash(data["artifact_sha256"]),
            "reviewer_ref": validate_identifier(data["reviewer_ref"]),
            "quality_policy_version": validate_identifier(data["quality_policy_version"]),
            "quality_pass": True, "expected_attempt_ids": sorted(expected)}


def normalize_work(data: dict) -> dict:
    _fields(data, _WORK_KEYS)
    result = normalize_create_spec({key: data[key] for key in _CREATE_KEYS})
    result.update(work_id=normalize_uuid(data["work_id"]), project_id=normalize_uuid(data["project_id"]),
                  status=_choice(data["status"], {"OPEN", "COMPLETED", "CANCELLED"}),
                  created_at=validate_timestamp(data["created_at"]),
                  closed_at=_nullable(data["closed_at"], validate_timestamp),
                  completion=_nullable(data["completion"], normalize_completion))
    is_complete = result["status"] == "COMPLETED"
    if ((result["completion"] is not None) != is_complete
            or (result["closed_at"] is None) != (result["status"] == "OPEN")):
        raise _error("STATE_INVALID")
    if result["closed_at"] is not None and _instant(result["closed_at"]) < _instant(result["created_at"]):
        raise _error("TIMESTAMP_INVALID")
    return result


def normalize_rate_book(data: dict | None) -> dict | None:
    if data is None:
        return None
    _fields(data, {"schema_version", "rate_version", "models"})
    if data["schema_version"] != RATE_SCHEMA_VERSION or type(data["models"]) is not dict:
        raise _error("SCHEMA_INVALID")
    if len(data["models"]) > MAX_RECEIPTS:
        raise _error("LIMIT_EXCEEDED")
    models = {}
    for model, values in sorted(data["models"].items()):
        validate_identifier(model)
        _fields(values, _RATE_KEYS)
        mode = _choice(values["mode"], {"combined", "split"})
        row = {key: _nullable(values[key], lambda value: usd_string(usd_decimal(value)))
               for key in _RATE_KEYS - {"mode"}}
        row["mode"] = mode
        if row["output_usd"] is None:
            raise _error("RATES_INVALID")
        if ((mode == "combined" and (row["input_text_usd"] is not None or row["input_image_usd"] is not None))
                or (mode == "split" and row["input_usd"] is not None)):
            raise _error("RATES_INVALID")
        models[model] = row
    return {"schema_version": RATE_SCHEMA_VERSION, "rate_version": validate_identifier(data["rate_version"]),
            "models": models}


def _instant(value):
    return datetime.fromisoformat(value)


def _index(rows, key, validator):
    result = {}
    for data in rows:
        row = validator(data)
        if row[key] in result:
            raise _error("DUPLICATE_ID")
        result[row[key]] = row
    return result


def _local_receipt(identity, receipt):
    if identity["provider"] != "local":
        if receipt["model"] == "local":
            raise _error("LOCAL_INVALID")
        return
    usage = receipt["usage"]
    if (receipt["model"] != "local" or usage is None
            or usage["input_tokens"] != 0 or usage["output_tokens"] != 0
            or any(value not in (None, 0) for value in usage.values())
            or receipt["estimated_cost_usd"] != "0.000000000"
            or receipt["rate_version"] != "local-api-zero-v1"):
        raise _error("LOCAL_INVALID")


def normalize_work_dataset(data: dict) -> dict:
    """Validate one project and all declared references, returning a detached copy."""
    _fields(data, {"schema_version", "data_kind", "works", "attempts", "receipts", "image_bindings", "rate_book"})
    if data["schema_version"] != DATASET_SCHEMA_VERSION:
        raise _error("SCHEMA_INVALID")
    kind = _choice(data["data_kind"], {"DECLARED_DATA", "SYNTHETIC_FIXTURE"})
    works = _index(_list(data["works"], MAX_WORKS), "work_id", normalize_work)
    attempts = _index(_list(data["attempts"], MAX_WORKS * MAX_ATTEMPTS_PER_WORK), "attempt_id", normalize_attempt)
    receipts = _index(_list(data["receipts"], MAX_RECEIPTS), "attempt_id", normalize_receipt)
    if len({row["project_id"] for row in works.values()}) > 1:
        raise _error("PROJECT_CONFLICT")
    work_attempts = {work_id: {} for work_id in works}
    pairs = set()
    for row in works.values():
        if row["task_pair_id"] is not None and row["arm"] is not None:
            pair = (row["task_pair_id"], row["arm"])
            if pair in pairs:
                raise _error("PAIR_CONFLICT")
            pairs.add(pair)
    for row in attempts.values():
        work = works.get(row["work_id"])
        if work is None or row["project_id"] != work["project_id"]:
            raise _error("WORK_REFERENCE_INVALID")
        if _instant(row["started_at"]) < _instant(work["created_at"]):
            raise _error("TIMESTAMP_INVALID")
        work_attempts[row["work_id"]][row["attempt_id"]] = row
        if len(work_attempts[row["work_id"]]) > MAX_ATTEMPTS_PER_WORK:
            raise _error("LIMIT_EXCEEDED")
        parent = attempts.get(row["parent_attempt_id"]) if row["parent_attempt_id"] is not None else None
        if row["parent_attempt_id"] is not None and (parent is None or parent["work_id"] != row["work_id"]
                or _instant(parent["started_at"]) > _instant(row["started_at"])):
            raise _error("PARENT_INVALID")
    # Iterative coloring rejects cycles without recursive depth or quadratic walks.
    visited = set()
    for attempt_id in attempts:
        path, on_path, current = [], set(), attempt_id
        while current is not None and current not in visited:
            if current in on_path:
                raise _error("PARENT_INVALID")
            path.append(current)
            on_path.add(current)
            current = attempts[current]["parent_attempt_id"]
        visited.update(path)
    provider_ids = set()
    for attempt_id, row in receipts.items():
        identity = attempts.get(attempt_id)
        if identity is None:
            raise _error("ATTEMPT_REFERENCE_INVALID")
        _local_receipt(identity, row)
        if row["provider_request_id"] is not None:
            key = (identity["provider"], row["provider_request_id"])
            if key in provider_ids:
                raise _error("PROVIDER_REQUEST_CONFLICT")
            provider_ids.add(key)
    bindings, job_ids, bound_attempts = [], set(), set()
    for raw in _list(data["image_bindings"], MAX_WORKS * MAX_ATTEMPTS_PER_WORK):
        _fields(raw, {"project_id", "work_id", "job_id", "attempt_id"})
        row = {key: _nullable(value, normalize_uuid) if key == "attempt_id" else normalize_uuid(value)
               for key, value in raw.items()}
        work = works.get(row["work_id"])
        if work is None or work["project_id"] != row["project_id"]:
            raise _error("WORK_REFERENCE_INVALID")
        if row["job_id"] in job_ids:
            raise _error("IMAGE_BINDING_CONFLICT")
        job_ids.add(row["job_id"])
        if row["attempt_id"] is not None:
            identity = attempts.get(row["attempt_id"])
            if (identity is None or identity["work_id"] != row["work_id"]
                    or identity["stage"] != "image_generation" or identity["provider"] == "local"
                    or row["attempt_id"] in bound_attempts):
                raise _error("IMAGE_BINDING_CONFLICT")
            bound_attempts.add(row["attempt_id"])
        bindings.append(row)
    binding_groups = {work_id: [] for work_id in works}
    for row in bindings:
        binding_groups[row["work_id"]].append(row)
    for work_id, work in works.items():
        if work["status"] != "COMPLETED":
            continue
        identities, completion = work_attempts[work_id], work["completion"]
        if (set(completion["expected_attempt_ids"]) != set(identities)
                or any(attempt_id not in receipts for attempt_id in identities)
                or any(row["attempt_id"] not in receipts for row in binding_groups[work_id])):
            raise _error("COMPLETION_INVALID")
        succeeded = [identity for attempt_id, identity in identities.items()
                     if receipts[attempt_id]["status"] == "SUCCEEDED"]
        if not set(work["required_stages"]).issubset({identity["stage"] for identity in succeeded}):
            raise _error("MISSING_STAGE")
        if not any(identity["stage"] in {"image_generation", "composition"}
                   and receipts[identity["attempt_id"]]["artifact_sha256"] == completion["artifact_sha256"]
                   for identity in succeeded):
            raise _error("COMPLETION_INVALID")
    return {"schema_version": DATASET_SCHEMA_VERSION, "data_kind": kind,
            "works": [works[key] for key in sorted(works)],
            "attempts": [attempts[key] for key in sorted(attempts)],
            "receipts": [receipts[key] for key in sorted(receipts)],
            "image_bindings": sorted(bindings, key=lambda row: (row["project_id"], row["job_id"])),
            "rate_book": normalize_rate_book(data["rate_book"])}
