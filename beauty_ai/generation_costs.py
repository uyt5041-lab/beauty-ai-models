"""Pure, matched-generation estimates from validated saved cost records.

These observations do not establish quality equivalence or a billing saving.
Incomplete pairs are excluded together, while all latest records count toward
coverage. No files, provider SDKs, model runtimes, or tracking clients are used.
"""
from __future__ import annotations

from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .cost_accounting import (
    latest_records, normalize_dataset, usd_decimal, usd_string, validate_identifier,
)


COMPARISON_SCHEMA_VERSION = "beautyai-generation-comparison-v1"
REPORT_SCHEMA_VERSION = "beautyai-generation-estimate-v1"
_MANIFEST_KEYS = {"schema_version", "comparison_id", "estimate_rate_version", "pairs"}
_PAIR_KEYS = {"pair_id", "baseline_cost_id", "candidate_cost_id"}


def _invalid(reason: str) -> None:
    raise ValueError("COST_GENERATION_" + reason)


def _identifier(value: object) -> str:
    try:
        return validate_identifier(value)
    except ValueError:
        _invalid("IDENTIFIER_INVALID")


def normalize_generation_manifest(manifest: dict) -> dict:
    """Validate and detach a manifest, ordering pairs by their explicit ID."""
    if (type(manifest) is not dict or manifest.keys() != _MANIFEST_KEYS
            or type(manifest["schema_version"]) is not str
            or manifest["schema_version"] != COMPARISON_SCHEMA_VERSION):
        _invalid("SCHEMA_INVALID")
    result = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "comparison_id": _identifier(manifest["comparison_id"]),
        "estimate_rate_version": _identifier(manifest["estimate_rate_version"]),
    }
    if type(manifest["pairs"]) is not list or not manifest["pairs"]:
        _invalid("PAIRS_INVALID")
    pair_ids, cost_ids, pairs = set(), set(), []
    for pair in manifest["pairs"]:
        if type(pair) is not dict or pair.keys() != _PAIR_KEYS:
            _invalid("PAIR_INVALID")
        item = {key: _identifier(pair[key]) for key in _PAIR_KEYS}
        if item["pair_id"] in pair_ids:
            _invalid("PAIR_DUPLICATE")
        pair_ids.add(item["pair_id"])
        for key in ("baseline_cost_id", "candidate_cost_id"):
            if item[key] in cost_ids:
                _invalid("COST_DUPLICATE")
            cost_ids.add(item[key])
        pairs.append(item)
    result["pairs"] = sorted(pairs, key=lambda pair: pair["pair_id"])
    return result


def _record_reasons(record: dict, rate_version: str) -> set[str]:
    reasons = set()
    if record["estimated_cost_usd"] is None:
        reasons.add("COST_MISSING")
    elif record["rate_version"] != rate_version:
        reasons.add("RATE_MISMATCH")
    if record["state"] != "SUCCEEDED":
        reasons.add("NOT_SUCCEEDED")
    if record["kind"] != "image":
        reasons.add("NOT_IMAGE")
    return reasons


def compare_generations(dataset: dict, manifest: dict) -> dict:
    """Compare eligible explicit pairs without dropping either pair member.

    Validation and canonical ordering reuse the accounting layer. Calculation
    then indexes each latest record once and visits every planned pair once.
    """
    normalized = normalize_dataset(dataset)
    comparison = normalize_generation_manifest(manifest)
    records = latest_records(normalized["cost_items"], "cost_id")
    by_id = {record["cost_id"]: record for record in records}
    reasons_by_id = {
        record["cost_id"]: _record_reasons(record, comparison["estimate_rate_version"])
        for record in records
    }
    assigned, included = set(), set()
    totals = {"baseline": Decimal(0), "candidate": Decimal(0)}
    compared_pairs = 0
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        for pair in comparison["pairs"]:
            ids = (pair["baseline_cost_id"], pair["candidate_cost_id"])
            if any(cost_id not in by_id for cost_id in ids):
                _invalid("COST_UNKNOWN")
            assigned.update(ids)
            if any(reasons_by_id[cost_id] for cost_id in ids):
                for cost_id in ids:
                    if not reasons_by_id[cost_id]:
                        reasons_by_id[cost_id].add("PAIRED_RECORD_EXCLUDED")
                continue
            compared_pairs += 1
            included.update(ids)
            for arm, cost_id in zip(("baseline", "candidate"), ids):
                totals[arm] += usd_decimal(by_id[cost_id]["estimated_cost_usd"])

        excluded = []
        reasons = set()
        for cost_id in sorted(by_id.keys() - included):
            item_reasons = reasons_by_id[cost_id]
            if cost_id not in assigned:
                item_reasons.add("UNPAIRED_RECORD")
            reasons.update(item_reasons)
            excluded.append({"cost_id": cost_id, "reason_codes": sorted(item_reasons)})

        averages = {}
        arms = {}
        for arm in ("baseline", "candidate"):
            averages[arm] = totals[arm] / compared_pairs if compared_pairs else None
            arms[arm] = {
                "generation_count": compared_pairs,
                "total_estimated_cost_usd": usd_string(totals[arm]) if compared_pairs else None,
                "estimated_cost_per_generation_usd": (
                    usd_string(averages[arm]) if compared_pairs else None
                ),
            }
        reduction = difference = per_generation = None
        if not compared_pairs:
            reasons.add("NO_COMPARABLE_PAIRS")
        else:
            difference = usd_string(totals["baseline"] - totals["candidate"])
            per_generation = usd_string(averages["baseline"] - averages["candidate"])
            if totals["baseline"] == 0:
                reasons.add("BASELINE_ZERO_COST")
            else:
                reduction = usd_string(
                    (averages["baseline"] - averages["candidate"])
                    / averages["baseline"] * 100
                )
        compared_records = compared_pairs * 2
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "comparison_id": comparison["comparison_id"],
            "estimate_rate_version": comparison["estimate_rate_version"],
            "input_record_count": len(records),
            "planned_pair_count": len(comparison["pairs"]),
            "compared_pair_count": compared_pairs,
            "compared_record_count": compared_records,
            "excluded_pair_count": len(comparison["pairs"]) - compared_pairs,
            "excluded_record_count": len(records) - compared_records,
            "coverage_pct": usd_string(Decimal(compared_records) / len(records) * 100) if records else None,
            **arms,
            "estimated_cost_reduction_pct": reduction,
            "estimated_cost_difference_usd": difference,
            "per_generation_difference_usd": per_generation,
            "excluded_records": excluded,
            "reason_codes": sorted(reasons),
            "assurance": "OBSERVATIONAL_NOT_QUALITY_CONTROLLED",
        }
