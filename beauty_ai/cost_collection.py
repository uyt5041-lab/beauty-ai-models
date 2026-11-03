"""Read explicit saved evidence into cost datasets without contacting providers.

Collection observes attempts, never acceptance or a reduction in billed cost.
Only this module's explicit CLI output paths are written; source evidence is read
in place, including SQLite WAL snapshots opened with ``mode=ro``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from .cost_accounting import (canonical_bytes, normalize_dataset, usd_decimal, usd_string,
                              validate_identifier)
from .wandb_experiments import historical_estimate


SCHEMA_VERSION = "beautyai-cost-dataset-v1"
HISTORICAL_RATE_VERSION = "historical-b12-2026-10-23"
MAX_JSON_BYTES = 1024 * 1024
MAX_RECORDS = 10000
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
_SECRET_KEY = re.compile(r"api[_-]?key|authorization|password|secret|credential|access[_-]?token", re.I)
_SECRET_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN .*PRIVATE KEY-----")
_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "input_image_tokens", "input_text_tokens")
_STATES = {"SUBMITTING", "SUCCEEDED", "FAILED", "UNKNOWN", "MISSING", "RECORDED"}
_COLUMNS = ("job_id", "project_id", "request_hash", "attempt_id", "state", "reservation_units",
            "provider_request_id", "usage_json", "result_json", "error_code", "created_at", "updated_at",
            "raw_usage_json", "metadata_json", "elapsed_seconds", "account_reservation_id")


def _error(code: str) -> ValueError:
    return ValueError("COST_COLLECTION_" + code)


def _identifier(value: Any, *, component: bool = False) -> str:
    pattern = _COMPONENT if component else _ID
    if (type(value) is not str or not pattern.fullmatch(value)
            or any(part in {".", ".."} for part in value.split("/"))
            or _SECRET_VALUE.search(value) or "//" in value):
        raise _error("IDENTIFIER_INVALID")
    try:
        return validate_identifier(value)
    except ValueError:
        raise _error("IDENTIFIER_INVALID") from None


def _checked(value: Any, depth: int = 0, count: list[int] | None = None) -> None:
    count = [0] if count is None else count
    count[0] += 1
    if depth > 16 or count[0] > 10000:
        raise _error("JSON_LIMIT")
    if isinstance(value, dict):
        for key, item in value.items():
            if type(key) is not str or len(key) > 200 or _SECRET_KEY.search(key):
                raise _error("UNSAFE_FIELD")
            _checked(item, depth + 1, count)
    elif isinstance(value, list):
        for item in value:
            _checked(item, depth + 1, count)
    elif isinstance(value, str):
        if len(value) > MAX_JSON_BYTES or _SECRET_VALUE.search(value):
            raise _error("UNSAFE_VALUE")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _error("JSON_INVALID")
    elif value is not None and type(value) not in (bool, int):
        raise _error("JSON_INVALID")


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise _error("JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _json(raw: bytes | str) -> dict:
    if len(raw if isinstance(raw, bytes) else raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise _error("JSON_LIMIT")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(_error("JSON_INVALID")))
        if type(value) is not dict:
            raise _error("JSON_INVALID")
        _checked(value)
        return value
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise _error("JSON_INVALID") from None


def _object(value: Any) -> dict:
    if value is None:
        return {}
    if type(value) is not dict:
        raise _error("OBSERVATION_INVALID")
    return value


def _usage(value: Any) -> dict | None:
    if value is None:
        return None
    value = _object(value)
    if set(value) - set(_USAGE_FIELDS):
        raise _error("USAGE_INVALID")
    result = {key: value.get(key) for key in _USAGE_FIELDS}
    if any(v is not None and (type(v) is not int or v < 0) for v in result.values()):
        raise _error("USAGE_INVALID")
    return result


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def _state(value: Any) -> str:
    return value.upper() if type(value) is str and value.upper() in _STATES else "UNKNOWN"


@dataclass(frozen=True)
class _Observation:
    item: dict
    fingerprint: bytes


def _observation(report: dict, receipt: dict, cost_id: str, source_ref: str,
                 source_sha256: str) -> _Observation:
    metadata = _object(receipt.get("metadata"))
    result = _object(receipt.get("result"))
    model = _first(receipt.get("model"), metadata.get("model"), result.get("model"), report.get("model"))
    usage = _usage(_first(receipt.get("usage"), report.get("usage"), report.get("usage_tokens")))
    state = _state(_first(receipt.get("state"), report.get("status")))
    recorded_estimate = _first(receipt.get("estimated_cost_usd"), report.get("estimated_cost_usd"))
    estimate, origin, basis = historical_estimate({"family": "api", "model": model, "usage": usage,
                                                  "estimated_cost_usd": recorded_estimate})
    known = origin == "RECONSTRUCTED_WITH_RECORDED_B12_RATES" and basis is not None
    estimate = usd_string(Decimal(str(estimate))) if known else None
    if estimate is not None:
        usd_decimal(estimate)
    evidence = _first(receipt.get("billing_evidence_ref"), report.get("billing_evidence_ref"))
    actual = _first(receipt.get("actual_cost_usd"), report.get("actual_cost_usd"),
                    report.get("actual_billed_cost_usd"))
    billing = _first(receipt.get("billing_status"), report.get("billing_status"))
    if evidence is not None and actual is not None and billing in {"PARTIAL", "RECONCILED"}:
        evidence = _identifier(evidence)
        actual = usd_string(usd_decimal(actual))
    else:
        actual, evidence, billing = None, None, "UNRECONCILED"
    item = {"cost_id": _identifier(cost_id), "revision": 1, "supersedes_revision": None,
            "source_ref": _identifier(source_ref), "source_sha256": source_sha256,
            "kind": "image", "category": "research", "estimated_cost_usd": estimate,
            "rate_version": HISTORICAL_RATE_VERSION if known else None,
            "actual_cost_usd": actual, "billing_status": billing, "billing_evidence_ref": evidence,
            "allocations": {}, "state": state}
    # Format-specific paths and surrounding report timestamps are not attempt
    # observations. Every known attempt field below participates in conflicts.
    dimensions = report.get("size")
    dimensions = dimensions if type(dimensions) is list and len(dimensions) == 2 else [None, None]
    saved_state = _first(receipt.get("state"), report.get("status"))
    observed = {"state": state, "unrecognized_state": saved_state if _state(saved_state) == "UNKNOWN" else None,
        "model": model, "model_requested": report.get("model_requested"),
        "usage": usage, "raw_usage": receipt.get("raw_usage"), "metadata": metadata or None,
        "parameters": {"operation": _first(metadata.get("operation"), result.get("operation"), report.get("operation")),
            "quality": _first(metadata.get("quality"), report.get("quality")),
            "width": _first(metadata.get("width"), report.get("width"), dimensions[0]),
            "height": _first(metadata.get("height"), report.get("height"), dimensions[1]),
            "prompt_version": _first(metadata.get("prompt_version"), report.get("prompt_version"))},
        "request_hash": _first(receipt.get("request_hash"), receipt.get("request_sha256"), report.get("request_sha256")),
        "job_id": _first(receipt.get("job_id"), receipt.get("model_job_id"), report.get("job_id")),
        "project_id": _first(receipt.get("project_id"), report.get("project_id")),
        "provider_request_id": receipt.get("provider_request_id"), "error_code": receipt.get("error_code"),
        "elapsed_seconds": _first(receipt.get("elapsed_seconds"),
            receipt.get("elapsed_ms") / 1000 if type(receipt.get("elapsed_ms")) in (int, float) else None),
        "account_reservation_id": receipt.get("account_reservation_id"),
        "reservation_usd": receipt.get("reservation_usd"), "result": result or None,
        "created_at": receipt.get("created_at"), "updated_at": receipt.get("updated_at"),
        "recorded_estimate": recorded_estimate,
        "recorded_actual": _first(receipt.get("actual_cost_usd"), report.get("actual_cost_usd"),
                                  report.get("actual_billed_cost_usd")),
        "cost": {k: v for k, v in item.items() if k not in {"source_ref", "source_sha256"}}}
    _checked(observed)
    return _Observation(item, canonical_bytes(observed))


def _generation_observations(root: str | Path, group_id: str) -> list[_Observation]:
    group_id = _identifier(group_id, component=True)
    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise _error("SOURCE_UNAVAILABLE")
        paths = sorted(root.glob("*/generation-result.json"))
        if len(paths) > MAX_RECORDS:
            raise _error("RECORD_LIMIT")
        observations = []
        for path in paths:
            if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
                raise _error("SOURCE_OUTSIDE_ROOT")
            case = _identifier(path.parent.name, component=True)
            if path.stat().st_size > MAX_JSON_BYTES:
                raise _error("JSON_LIMIT")
            with path.open("rb") as source:
                raw = source.read(MAX_JSON_BYTES + 1)
            report = _json(raw)
            if report.get("receipt") is not None and report.get("provider_receipt") is not None:
                raise _error("RECEIPT_AMBIGUOUS")
            receipt = _object(_first(report.get("provider_receipt"), report.get("receipt")))
            report_result = _object(report.get("result"))
            attempt_ids = {_identifier(value) for value in
                (receipt.get("attempt_id"), report.get("attempt_id"), report_result.get("attempt_id"))
                if value is not None}
            if len(attempt_ids) > 1:
                raise _error("ATTEMPT_CONFLICT")
            attempt = next(iter(attempt_ids), None)
            cost_id = "attempt:" + _identifier(attempt) if attempt is not None else f"legacy:{group_id}/{case}"
            observations.append(_observation(report, receipt, cost_id,
                f"generation:{group_id}/{case}/generation-result.json", hashlib.sha256(raw).hexdigest()))
        return observations
    except OSError:
        raise _error("SOURCE_UNAVAILABLE") from None


def _attempt_observations(sqlite_path: str | Path, source_id: str) -> list[_Observation]:
    source_id = _identifier(source_id, component=True)
    try:
        path = Path(sqlite_path).resolve(strict=True)
        if not path.is_file():
            raise _error("SOURCE_UNAVAILABLE")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            columns = {row[1] for row in db.execute("PRAGMA table_info(provider_attempts)")}
            if not {"attempt_id", "state"}.issubset(columns):
                raise _error("SCHEMA_INVALID")
            # SQL identifiers only come from this module's closed allowlist.
            projection = ",".join(name if name in columns else f"NULL AS {name}" for name in _COLUMNS)
            cursor = db.execute(f"SELECT {projection} FROM provider_attempts ORDER BY attempt_id")
            observations = []
            for row in cursor:
                if len(observations) >= MAX_RECORDS:
                    raise _error("RECORD_LIMIT")
                raw = dict(row)
                _checked(raw)
                snapshot = canonical_bytes(raw)
                if len(snapshot) > MAX_JSON_BYTES:
                    raise _error("JSON_LIMIT")
                receipt = {key: raw[key] for key in _COLUMNS if not key.endswith("_json")}
                for key in ("usage", "raw_usage", "metadata", "result"):
                    receipt[key] = _json(raw[key + "_json"]) if raw[key + "_json"] is not None else None
                reservation = raw["reservation_units"]
                if reservation is not None and (type(reservation) is not int or reservation < 0):
                    raise _error("OBSERVATION_INVALID")
                receipt["reservation_usd"] = (f"{reservation // 1000000}.{reservation % 1000000:06d}"
                                              if reservation is not None else None)
                attempt = _identifier(raw["attempt_id"])
                observations.append(_observation({}, receipt, "attempt:" + attempt,
                    f"attempts:{source_id}/{attempt}", hashlib.sha256(snapshot).hexdigest()))
            db.rollback()
            return observations
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        raise _error("SOURCE_UNAVAILABLE") from None


def _merge(observations: Iterable[_Observation]) -> list[dict]:
    seen: dict[str, _Observation] = {}
    for observation in observations:
        key = observation.item["cost_id"]
        previous = seen.get(key)
        if previous is not None and previous.fingerprint != observation.fingerprint:
            raise _error("ATTEMPT_CONFLICT")
        if previous is None or (observation.item["source_ref"], observation.item["source_sha256"]) < (
                previous.item["source_ref"], previous.item["source_sha256"]):
            seen[key] = observation
    return [seen[key].item for key in sorted(seen)]


def collect_generation_results(root: str | Path, group_id: str, category: str = "research") -> list[dict]:
    """Collect one explicit directory; production needs a separate allocation edit."""
    if category != "research":
        raise _error("EXPLICIT_ALLOCATION_REQUIRED")
    return _merge(_generation_observations(root, group_id))


def collect_attempt_snapshot(sqlite_path: str | Path, source_id: str) -> list[dict]:
    """Read every attempt state in one consistent SQLite read transaction."""
    return _merge(_attempt_observations(sqlite_path, source_id))


def collect_dataset(*, generation_roots: Iterable[tuple[str, str | Path]] = (),
                    attempt_dbs: Iterable[tuple[str, str | Path]] = ()) -> dict:
    """Merge explicit sources, rejecting changed observations of one attempt."""
    return collect_evidence(generation_roots=generation_roots, attempt_dbs=attempt_dbs)[0]


def collect_evidence(*, generation_roots: Iterable[tuple[str, str | Path]] = (),
                     attempt_dbs: Iterable[tuple[str, str | Path]] = ()) -> tuple[dict, dict]:
    """Return a dataset plus all source hashes, including deduplicated evidence."""
    observations = []
    inventory = []
    for group_id, path in generation_roots:
        collected = _generation_observations(path, group_id)
        observations.extend(collected)
        inventory.append({"kind": "generation_results", "source_id": group_id,
                          "records": [{key: o.item[key] for key in ("cost_id", "source_ref", "source_sha256")}
                                      for o in collected]})
    for source_id, path in attempt_dbs:
        collected = _attempt_observations(path, source_id)
        observations.extend(collected)
        inventory.append({"kind": "attempt_snapshot", "source_id": source_id,
                          "records": [{key: o.item[key] for key in ("cost_id", "source_ref", "source_sha256")}
                                      for o in collected]})
    provenance = {"schema_version": "beautyai-cost-sources-v1", "sources":
                  sorted(inventory, key=lambda entry: (entry["kind"], entry["source_id"], canonical_bytes(entry)))}
    dataset = normalize_dataset({"schema_version": SCHEMA_VERSION, "cost_items": _merge(observations),
                                 "batches": [], "acceptances": []})
    return dataset, provenance


def _source_argument(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not path:
        raise argparse.ArgumentTypeError("Expected SOURCE=PATH")
    try:
        return _identifier(name, component=True), Path(path)
    except ValueError:
        raise argparse.ArgumentTypeError("Invalid source alias") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-root", action="append", type=_source_argument, default=[], metavar="GROUP=PATH")
    parser.add_argument("--attempt-db", action="append", type=_source_argument, default=[], metavar="SOURCE=PATH")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.generation_root and not args.attempt_db:
        parser.error("At least one explicit source is required")
    try:
        dataset, provenance = collect_evidence(generation_roots=args.generation_root, attempt_dbs=args.attempt_db)
        output = args.output.resolve()
        # Never overwrite an evidence file, database, or a second output.
        sources = [Path(path).resolve() for _, path in args.generation_root + args.attempt_db]
        targets = [output, output.with_name(output.stem + ".sources.json"),
                   output.with_name(output.stem + ".acceptance-example.json"),
                   output.with_name(output.stem + ".comparison-example.json")]
        if any(target == source or (source.is_dir() and target.is_relative_to(source))
               for target in targets for source in sources):
            raise _error("OUTPUT_OVERWRITES_SOURCE")
        examples = [dataset, provenance,
            {"status": "TEMPLATE_NOT_EVALUATED", "acceptances": [],
             "instructions": "Add explicit reviewed artifact evidence; generation success is not acceptance."},
            {"status": "TEMPLATE_NOT_EVALUATED", "comparison": {
                "schema_version": "beautyai-cost-comparison-v1", "comparison_id": None,
                "accounting_window_id": None, "cost_scope": ["image"], "quality_policy_version": None,
                "estimate_rate_version": HISTORICAL_RATE_VERSION, "pairs": []}}]
        output.parent.mkdir(parents=True, exist_ok=True)
        for target, payload in zip(targets, examples):
            target.write_bytes(canonical_bytes(payload))
    except (ValueError, OSError) as exc:
        # Exception text from source JSON, paths, or SQLite is never echoed.
        code = str(exc) if str(exc).startswith("COST_COLLECTION_") else "COST_COLLECTION_INVALID"
        parser.exit(2, code + "\n")
    print(json.dumps({"status": "COLLECTED", "cost_item_count": len(dataset["cost_items"]),
                      "acceptance_count": 0, "new_provider_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
