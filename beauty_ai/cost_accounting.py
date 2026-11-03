"""Pure, strict accounting records and exact USD allocation.

Sources and acceptance are declarations, not provider billing or human review
attestations. Normalization preserves validated revision history; consumers use
latest_records for calculation while retaining original evidence files.
"""
from __future__ import annotations

from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
import math
import re


SCHEMA_VERSION = "beautyai-cost-dataset-v1"
_NANOS = 1_000_000_000
_QUANTUM = Decimal("0.000000001")
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,9})?\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9.][A-Za-z0-9._:/ -]{0,511}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_SECRET = re.compile(
    r"api[_-]?key|authorization|password|secret|credential|access[_-]?token"
    r"|(?:^|[/.:])(?:sk|rk)-[A-Za-z0-9_-]{8,}"
    r"|(?:^|[/.:])gh[pousr]_[A-Za-z0-9_]+"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{20,}"
    r"|(?:^|[/.:])eyJ[A-Za-z0-9_-]+\.", re.IGNORECASE,
)
_COST_FIELDS = frozenset({
    "cost_id", "revision", "supersedes_revision", "source_ref", "source_sha256",
    "kind", "category", "estimated_cost_usd", "rate_version", "actual_cost_usd",
    "billing_status", "billing_evidence_ref", "allocations", "state",
})
_BATCH_FIELDS = frozenset({"batch_id", "product_id", "expected_cost_ids"})
_ACCEPTANCE_FIELDS = frozenset({
    "ad_id", "revision", "supersedes_revision", "batch_id", "action",
    "artifact_sha256", "reviewer_ref", "quality_policy_version", "quality_pass",
})


def _error(reason: str) -> ValueError:
    return ValueError("COST_" + reason)


def canonical_bytes(value) -> bytes:
    """Encode JSON data deterministically, rejecting non-JSON and nonfinite data."""
    def validate(node):
        if type(node) is dict:
            if any(type(key) is not str for key in node):
                raise _error("CANONICAL_INVALID")
            for child in node.values():
                validate(child)
        elif type(node) is list:
            for child in node:
                validate(child)
        elif type(node) is float:
            if not math.isfinite(node):
                raise _error("CANONICAL_INVALID")
        elif node is not None and type(node) not in (str, int, bool):
            raise _error("CANONICAL_INVALID")

    try:
        validate(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise _error("CANONICAL_INVALID") from None


def digest(value) -> str:
    """SHA-256 of canonical JSON data (use hashlib directly for original bytes)."""
    return sha256(canonical_bytes(value)).hexdigest()


def usd_decimal(value: str) -> Decimal:
    """Validate one nonnegative cost amount without float or exponent coercion."""
    if type(value) is not str or not _MONEY.fullmatch(value):
        raise _error("AMOUNT_INVALID")
    amount = Decimal(value)
    if amount > Decimal("1000000000"):
        raise _error("AMOUNT_INVALID")
    return amount


def usd_string(value: Decimal) -> str:
    """Format amounts or signed derived totals to nine places, half-even rounded.

    Derived totals may exceed the per-item maximum. A private Decimal context
    keeps formatting independent of the caller's precision and rounding mode.
    """
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _error("AMOUNT_INVALID")
    precision = max(40, len(value.as_tuple().digits) + max(value.adjusted(), 0) + 12)
    try:
        with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
            rounded = value.quantize(_QUANTUM)
            return format(abs(rounded) if not rounded else rounded, ".9f")
    except (DecimalException, ValueError, OverflowError):
        raise _error("AMOUNT_INVALID") from None


def _identifier(value) -> str:
    if (type(value) is not str or not _IDENTIFIER.fullmatch(value)
            or _SECRET.search(value) or ".." in value.split("/") or "://" in value):
        raise _error("IDENTIFIER_INVALID")
    return value


def validate_identifier(value) -> str:
    """Validate a bounded public alias without publishing input in error text."""
    return _identifier(value)


def _reference(value) -> str:
    if (type(value) is not str or not _REFERENCE.fullmatch(value)
            or value != value.strip() or _SECRET.search(value) or "://" in value
            or ".." in value.split("/") or re.match(r"[A-Za-z]:/", value)
            or value in (".", "..")):
        raise _error("REFERENCE_INVALID")
    return value


def _sha256(value) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise _error("HASH_INVALID")
    return value


def _fields(value, expected):
    if type(value) is not dict or value.keys() != expected:
        raise _error("SCHEMA_INVALID")


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise _error("SCHEMA_INVALID")
    return value


def _revision(record):
    revision, previous = record["revision"], record["supersedes_revision"]
    if (type(revision) is not int or revision < 1
            or (revision == 1 and previous is not None)
            or (revision > 1 and (type(previous) is not int or previous != revision - 1))):
        raise _error("REVISION_INVALID")


def _allocations(value, *, production=True):
    if type(value) is not dict:
        raise _error("ALLOCATION_INVALID")
    for batch_id, weight in value.items():
        _identifier(batch_id)
        if type(weight) is not int or not 0 <= weight <= 10000:
            raise _error("ALLOCATION_INVALID")
    if (production and sum(value.values()) != 10000) or (not production and value):
        raise _error("ALLOCATION_INVALID")
    return dict(sorted(value.items()))


def allocate_usd(amount: str, allocations: dict[str, int]) -> dict[str, str]:
    """Truncate shares to nanodollars, then assign the residual to the last ID."""
    usd_decimal(amount)
    weights = _allocations(allocations)
    whole, _, fraction = amount.partition(".")
    nanos = int(whole) * _NANOS + int(fraction.ljust(9, "0"))
    shares = {batch_id: nanos * weight // 10000 for batch_id, weight in weights.items()}
    shares[next(reversed(shares))] += nanos - sum(shares.values())
    return {batch_id: f"{value // _NANOS}.{value % _NANOS:09d}"
            for batch_id, value in shares.items()}


def _cost(record):
    _fields(record, _COST_FIELDS)
    _revision(record)
    result = dict(record)
    result["cost_id"] = _identifier(record["cost_id"])
    result["source_ref"] = _reference(record["source_ref"])
    result["source_sha256"] = _sha256(record["source_sha256"])
    _choice(record["kind"], {"image", "text", "review", "compute"})
    _choice(record["category"], {"production", "research"})
    _choice(record["state"], {"SUBMITTING", "SUCCEEDED", "FAILED", "UNKNOWN", "MISSING", "RECORDED"})
    _choice(record["billing_status"], {"UNRECONCILED", "PARTIAL", "RECONCILED"})
    if record["rate_version"] is not None:
        _identifier(record["rate_version"])
    if record["billing_evidence_ref"] is not None:
        _reference(record["billing_evidence_ref"])
    for amount_field, evidence_field in (("estimated_cost_usd", "rate_version"),
                                         ("actual_cost_usd", "billing_evidence_ref")):
        if record[amount_field] is not None:
            result[amount_field] = usd_string(usd_decimal(record[amount_field]))
            if record[evidence_field] is None:
                raise _error("BILLING_EVIDENCE_REQUIRED" if amount_field == "actual_cost_usd"
                             else "RATE_VERSION_REQUIRED")
    result["allocations"] = _allocations(record["allocations"], production=record["category"] == "production")
    return result


def _batch(record):
    _fields(record, _BATCH_FIELDS)
    _identifier(record["batch_id"])
    _identifier(record["product_id"])
    expected = record["expected_cost_ids"]
    if type(expected) is not list:
        raise _error("SCHEMA_INVALID")
    for cost_id in expected:
        _identifier(cost_id)
    if len(expected) != len(set(expected)):
        raise _error("EXPECTED_COST_MISMATCH")
    return {**record, "expected_cost_ids": sorted(expected)}


def _acceptance(record):
    _fields(record, _ACCEPTANCE_FIELDS)
    _revision(record)
    for key in ("ad_id", "batch_id", "reviewer_ref", "quality_policy_version"):
        _identifier(record[key])
    _sha256(record["artifact_sha256"])
    _choice(record["action"], {"accept", "revoke"})
    if type(record["quality_pass"]) is not bool:
        raise _error("QUALITY_INVALID")
    return dict(record)


def _history(records, validator, identity, *, fixed_batch=False):
    revisions = {}
    for record in records:
        normalized = validator(record)
        key, revision = normalized[identity], normalized["revision"]
        history = revisions.setdefault(key, {})
        content = canonical_bytes(record)
        if revision in history and history[revision][0] != content:
            raise _error("REVISION_CONFLICT")
        history[revision] = (content, normalized)
    normalized_history = []
    for key in sorted(revisions):
        history = revisions[key]
        if sorted(history) != list(range(1, len(history) + 1)):
            raise _error("REVISION_CHAIN_INVALID")
        if fixed_batch and len({entry[1]["batch_id"] for entry in history.values()}) != 1:
            raise _error("ACCEPTANCE_BATCH_CONFLICT")
        normalized_history.extend(history[revision][1] for revision in sorted(history))
    return normalized_history


def _select_latest(history, identity):
    latest = {}
    for record in history:
        latest[record[identity]] = record
    return [latest[key] for key in sorted(latest)]


def latest_records(items: list[dict], id_field: str) -> list[dict]:
    """Return latest cost/ad records after validating and deduplicating history."""
    if type(items) is not list or id_field not in ("cost_id", "ad_id"):
        raise _error("SCHEMA_INVALID")
    validator = _cost if id_field == "cost_id" else _acceptance
    history = _history(items, validator, id_field, fixed_batch=id_field == "ad_id")
    return _select_latest(history, id_field)


def normalize_dataset(data: dict) -> dict:
    """Return detached, ordered, validated history and check latest-record integrity.

    Missing observations are explicit MISSING items with null costs. An absent
    expected ID or mismatched allocation is an integrity error, never a zero cost.
    Full revision history is retained so the result can be safely revalidated.
    """
    _fields(data, {"schema_version", "cost_items", "batches", "acceptances"})
    if type(data["schema_version"]) is not str or data["schema_version"] != SCHEMA_VERSION:
        raise _error("SCHEMA_INVALID")
    if any(type(data[key]) is not list for key in ("cost_items", "batches", "acceptances")):
        raise _error("SCHEMA_INVALID")
    costs = _history(data["cost_items"], _cost, "cost_id")
    acceptances = _history(data["acceptances"], _acceptance, "ad_id", fixed_batch=True)
    batches = {}
    for record in data["batches"]:
        batch = _batch(record)
        if batch["batch_id"] in batches:
            raise _error("BATCH_CONFLICT")
        batches[batch["batch_id"]] = batch
    allocated = {batch_id: set() for batch_id in batches}
    for cost in _select_latest(costs, "cost_id"):
        for batch_id in cost["allocations"]:
            if batch_id not in batches:
                raise _error("BATCH_REFERENCE_INVALID")
            allocated[batch_id].add(cost["cost_id"])
    for batch_id, batch in batches.items():
        if set(batch["expected_cost_ids"]) != allocated[batch_id]:
            raise _error("EXPECTED_COST_MISMATCH")
    if any(acceptance["batch_id"] not in batches for acceptance in acceptances):
        raise _error("BATCH_REFERENCE_INVALID")
    return {"schema_version": SCHEMA_VERSION, "cost_items": costs,
            "batches": [batches[key] for key in sorted(batches)], "acceptances": acceptances}
