"""Pure, evidence-gated cost comparisons for declared production batches.

All monetary output is a decimal string. A known partial is never promoted to a
complete cost, and the counterfactual difference is not a cash-spend difference.
This module reads no files and imports no provider, model, or reporting runtime.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .cost_accounting import (
    allocate_usd, latest_records, normalize_dataset, usd_decimal, usd_string, validate_identifier,
)


COMPARISON_SCHEMA_VERSION = "beautyai-cost-comparison-v1"
REPORT_SCHEMA_VERSION = "beautyai-cost-savings-v1"
_KINDS = {"image", "text", "review", "compute"}
_MANIFEST_KEYS = {
    "schema_version", "comparison_id", "accounting_window_id", "cost_scope",
    "quality_policy_version", "estimate_rate_version", "pairs",
}


def _invalid(reason: str) -> None:
    raise ValueError(reason)


def _identifier(value: object) -> str:
    try:
        return validate_identifier(value)
    except ValueError:
        _invalid("COST_COMPARISON_IDENTIFIER_INVALID")


def _manifest(comparison: Mapping, batches: dict[str, dict]) -> dict:
    if not isinstance(comparison, Mapping) or set(comparison) != _MANIFEST_KEYS:
        _invalid("COST_COMPARISON_SCHEMA_INVALID")
    if comparison["schema_version"] != COMPARISON_SCHEMA_VERSION:
        _invalid("COST_COMPARISON_SCHEMA_INVALID")
    result = {key: _identifier(comparison[key]) for key in (
        "comparison_id", "accounting_window_id", "quality_policy_version", "estimate_rate_version",
    )}
    scope = comparison["cost_scope"]
    if (not isinstance(scope, list) or not scope
            or any(not isinstance(kind, str) or kind not in _KINDS for kind in scope)
            or len(scope) != len(set(scope))):
        _invalid("COST_COMPARISON_SCOPE_INVALID")
    result["cost_scope"] = sorted(scope)
    pairs = comparison["pairs"]
    if not isinstance(pairs, list) or not pairs:
        _invalid("COST_COMPARISON_PAIRS_INVALID")
    products, used_batches, normalized_pairs = set(), set(), []
    for pair in pairs:
        if (not isinstance(pair, Mapping)
                or set(pair) != {"product_id", "baseline_batch_id", "candidate_batch_id"}):
            _invalid("COST_COMPARISON_PAIR_INVALID")
        item = {key: _identifier(value) for key, value in pair.items()}
        product = item["product_id"]
        if product in products:
            _invalid("COST_COMPARISON_PRODUCT_DUPLICATE")
        products.add(product)
        for name in ("baseline_batch_id", "candidate_batch_id"):
            batch_id = item[name]
            if batch_id in used_batches:
                _invalid("COST_COMPARISON_BATCH_DUPLICATE")
            used_batches.add(batch_id)
            if batch_id not in batches:
                _invalid("COST_COMPARISON_BATCH_UNKNOWN")
            if batches[batch_id]["product_id"] != product:
                _invalid("COST_COMPARISON_PRODUCT_MISMATCH")
        normalized_pairs.append(item)
    result["pairs"] = sorted(normalized_pairs, key=lambda pair: pair["product_id"])
    return result


def _accepted(acceptances: list[dict], manifest: dict, arm: str) -> tuple[dict, set]:
    products_by_batch = {
        pair[f"{arm}_batch_id"]: pair["product_id"] for pair in manifest["pairs"]
    }
    counts = {product: 0 for product in products_by_batch.values()}
    reasons, artifacts = set(), {}
    # A canonical representative ensures each artifact belongs to exactly one
    # product count. Conflicting product attribution also blocks the comparison.
    ordered = sorted(
        (item for item in acceptances if item["batch_id"] in products_by_batch),
        key=lambda item: (products_by_batch[item["batch_id"]], item["batch_id"], item["ad_id"]),
    )
    for item in ordered:
        if item["action"] != "accept" or not item["quality_pass"]:
            continue
        if item["quality_policy_version"] != manifest["quality_policy_version"]:
            reasons.add("QUALITY_POLICY_MISMATCH")
            continue
        product = products_by_batch[item["batch_id"]]
        artifact = item["artifact_sha256"]
        if artifact in artifacts:
            if artifacts[artifact] != product:
                reasons.add("ARTIFACT_PRODUCT_MISMATCH")
            continue
        artifacts[artifact] = product
        counts[product] += 1
    if any(count == 0 for count in counts.values()):
        reasons.add("NO_ACCEPTED_ADS")
    return counts, reasons


def _item_reasons(item: dict, basis: str, rate: str | None) -> set[str]:
    reasons = set()
    amount = item[f"{basis}_cost_usd"]
    if amount is None:
        reasons.add(f"{basis.upper()}_COST_MISSING")
    if basis == "estimated":
        if amount is not None and rate is not None and item["rate_version"] != rate:
            reasons.add("ESTIMATE_RATE_MISMATCH")
    else:
        if item["billing_status"] != "RECONCILED":
            reasons.add("ACTUAL_COST_UNRECONCILED")
        if not item["billing_evidence_ref"]:
            reasons.add("BILLING_EVIDENCE_MISSING")
    return reasons


def _production_arm(items: list[dict], batch_ids: set, counts: dict,
                    acceptance_reasons: set, manifest: dict, basis: str) -> dict:
    selected = [item for item in items
                if item["category"] == "production" and batch_ids.intersection(item["allocations"])]
    reasons = set(acceptance_reasons)
    known_partial, known_count = Decimal(0), 0
    missing_ids = []
    for item in selected:
        item_reasons = _item_reasons(item, basis, manifest["estimate_rate_version"])
        if item["kind"] not in manifest["cost_scope"]:
            item_reasons.add("COST_SCOPE_MISMATCH")
        reasons.update(item_reasons)
        if item_reasons:
            missing_ids.append(item["cost_id"])
        amount = item[f"{basis}_cost_usd"]
        if amount is not None:
            allocated = allocate_usd(amount, item["allocations"])
            known_partial += sum((usd_decimal(value) for batch_id, value in allocated.items()
                                  if batch_id in batch_ids), Decimal(0))
            known_count += 1
    covered_batches = {batch_id for item in selected for batch_id in item["allocations"]}
    missing_batches = sorted(batch_ids - covered_batches)
    if missing_batches:
        reasons.add("NO_COST_ITEMS")
    accepted = sum(counts.values())
    total = known_partial if not reasons else None
    return {
        "total_cost_usd": usd_string(total) if total is not None else None,
        "known_partial_usd": usd_string(known_partial),
        "accepted_ads": accepted,
        "unit_cost_usd": usd_string(total / accepted) if total is not None and accepted else None,
        "cost_item_count": len(selected),
        "known_cost_count": known_count,
        "missing_cost_count": len(missing_ids),
        "missing_cost_ids": sorted(missing_ids),
        "missing_batch_count": len(missing_batches),
        "missing_batch_ids": missing_batches,
        "reason_codes": sorted(reasons),
    }


def _research(items: list[dict]) -> dict:
    research = [item for item in items if item["category"] == "research"]
    result = {"cost_item_count": len(research)}
    for basis in ("estimated", "actual"):
        total, known_count, missing_count, reasons = Decimal(0), 0, 0, set()
        for item in research:
            item_reasons = _item_reasons(item, basis, None)
            reasons.update(item_reasons)
            missing_count += bool(item_reasons)
            amount = item[f"{basis}_cost_usd"]
            if amount is not None:
                total += usd_decimal(amount)
                known_count += 1
        result[basis] = {
            "total_cost_usd": usd_string(total) if not reasons else None,
            "known_partial_usd": usd_string(total),
            "known_cost_count": known_count,
            "missing_cost_count": missing_count,
            "reason_codes": sorted(reasons),
        }
    return result


def compare_costs(dataset: Mapping, comparison: Mapping) -> dict:
    """Compare declared batches without IO, mutation, or an inferred acceptance.

    Invalid schemas, revision chains, and manifests raise ``ValueError``. Valid
    but incomplete evidence produces null totals/unit costs for the affected
    arm and null headlines, with known partials and explicit reason codes. A
    product-mix mismatch retains descriptive arm costs but blocks headlines.
    """
    normalized = normalize_dataset(dataset)
    normalized["cost_items"] = latest_records(normalized["cost_items"], "cost_id")
    normalized["acceptances"] = latest_records(normalized["acceptances"], "ad_id")
    batches = {batch["batch_id"]: batch for batch in normalized["batches"]}
    manifest = _manifest(comparison, batches)
    counts, acceptance_reasons = {}, {}
    for arm in ("baseline", "candidate"):
        counts[arm], acceptance_reasons[arm] = _accepted(normalized["acceptances"], manifest, arm)
    totals = {arm: sum(values.values()) for arm, values in counts.items()}
    mix_matches = all(
        counts["baseline"][pair["product_id"]] * totals["candidate"]
        == counts["candidate"][pair["product_id"]] * totals["baseline"]
        for pair in manifest["pairs"]
    )
    result = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "comparison_id": manifest["comparison_id"],
        "accounting_window_id": manifest["accounting_window_id"],
        "cost_scope": manifest["cost_scope"],
        "quality_policy_version": manifest["quality_policy_version"],
        "estimate_rate_version": manifest["estimate_rate_version"],
        "scope_authority": "DECLARED_SCOPE",
        "quality_authority": "DECLARED_QUALITY",
        "counterfactual_is_actual_cash_difference": False,
        "per_product_counts": [{
            **pair,
            "baseline_accepted_ads": counts["baseline"][pair["product_id"]],
            "candidate_accepted_ads": counts["candidate"][pair["product_id"]],
        } for pair in manifest["pairs"]],
    }
    # Fixed high precision also makes results independent of caller Decimal
    # context; output alone is rounded to nine fractional digits.
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        for basis in ("estimated", "actual"):
            section = {}
            for arm in ("baseline", "candidate"):
                section[arm] = _production_arm(
                    normalized["cost_items"],
                    {pair[f"{arm}_batch_id"] for pair in manifest["pairs"]},
                    counts[arm], acceptance_reasons[arm], manifest, basis,
                )
            reasons = set(section["baseline"]["reason_codes"] + section["candidate"]["reason_codes"])
            baseline_total = section["baseline"]["total_cost_usd"]
            if baseline_total is not None and Decimal(baseline_total) == 0:
                reasons.add("BASELINE_ZERO_COST")
                section["baseline"]["reason_codes"].append("BASELINE_ZERO_COST")
                section["baseline"]["reason_codes"].sort()
                section["baseline"]["total_cost_usd"] = None
                section["baseline"]["unit_cost_usd"] = None
            if not mix_matches:
                reasons.add("PRODUCT_MIX_MISMATCH")
            section["unit_cost_reduction_pct"] = None
            section["counterfactual_difference_usd"] = None
            if not reasons:
                # Use exact totals here, never previously rounded unit costs.
                baseline = Decimal(baseline_total) / totals["baseline"]
                candidate = Decimal(section["candidate"]["total_cost_usd"]) / totals["candidate"]
                section["unit_cost_reduction_pct"] = usd_string((baseline - candidate) / baseline * 100)
                section["counterfactual_difference_usd"] = usd_string((baseline - candidate) * totals["candidate"])
            section["reason_codes"] = sorted(reasons)
            result[basis] = section
        result["research"] = _research(normalized["cost_items"])
    return result
