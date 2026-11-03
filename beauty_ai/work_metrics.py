"""Pure work-level token coverage, exact API cost, and matched comparisons.

Only declared receipts and the supplied rate snapshot contribute observations.
The normalized, detached history remains in the result alongside its aggregates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from .work_contracts import normalize_work_dataset, usd_decimal, usd_string


WORK_TABLE_COLUMNS = (
    "work_id", "product_ref", "task_pair_id", "arm", "status", "attempt_count",
    "model_api_attempt_count", "local_stage_count", "pending_count", "failed_count",
    "revision_attempt_count", "unobserved_image_job_count", "known_usage_count",
    "missing_usage_count", "input_tokens", "output_tokens", "known_input_tokens",
    "known_output_tokens", "estimated_total_usd", "known_partial_estimated_usd",
    "missing_cost_count", "actual_total_usd", "known_partial_actual_usd",
    "final_artifact_sha256",
)
STAGE_TABLE_COLUMNS = (
    "work_id", "stage", "provider", "model", "attempt_count", "known_usage_count",
    "missing_usage_count", "input_tokens", "output_tokens", "known_input_tokens",
    "known_output_tokens", "cached_input_tokens", "reasoning_tokens",
    "estimated_total_usd", "known_partial_estimated_usd", "missing_cost_count",
)
_ZERO = Decimal(0)
_MILLION = Decimal(1_000_000)


def _estimate(receipt: dict | None, rate_book: dict | None) -> tuple[Decimal | None, str | None]:
    if receipt is None:
        return None, None
    if receipt["estimated_cost_usd"] is not None:
        return usd_decimal(receipt["estimated_cost_usd"]), receipt["rate_version"]
    usage = receipt["usage"]
    if rate_book is None or usage is None:
        return None, None
    rate = rate_book["models"].get(receipt["model"])
    if rate is None or usage["input_tokens"] is None or usage["output_tokens"] is None:
        return None, None
    if rate["mode"] == "combined":
        if rate["input_usd"] is None:
            return None, None
        input_cost = Decimal(usage["input_tokens"]) * usd_decimal(rate["input_usd"])
    else:
        text, image = usage["input_text_tokens"], usage["input_image_tokens"]
        if (text is None or image is None or text + image != usage["input_tokens"]
                or rate["input_text_usd"] is None or rate["input_image_usd"] is None):
            return None, None
        input_cost = (Decimal(text) * usd_decimal(rate["input_text_usd"])
                      + Decimal(image) * usd_decimal(rate["input_image_usd"]))
    if rate["output_usd"] is None:
        return None, None
    total = (input_cost + Decimal(usage["output_tokens"]) * usd_decimal(rate["output_usd"])) / _MILLION
    # Each receipt's estimate uses the same nanodollar unit as explicit costs.
    return Decimal(usd_string(total)), rate_book["rate_version"]


def _fact(attempt: dict, receipt: dict | None, rate_book: dict | None) -> dict:
    estimate, version = _estimate(receipt, rate_book)
    usage = receipt["usage"] if receipt is not None else None
    return {
        "identity": attempt, "receipt": receipt, "local": attempt["provider"] == "local",
        "model": receipt["model"] if receipt is not None else attempt["configured_model"],
        "status": receipt["status"] if receipt is not None else "PENDING",
        "usage": usage or {}, "estimate": estimate, "rate_version": version,
        "actual": (usd_decimal(receipt["actual_cost_usd"])
                   if receipt is not None and receipt["actual_cost_usd"] is not None else None),
    }


def _observed_sum(facts: list[dict], key: str) -> int:
    return sum(fact["usage"].get(key) or 0 for fact in facts)


def _complete_token_sum(facts: list[dict], key: str, unobserved: int = 0) -> int | None:
    if unobserved or any(fact["usage"].get(key) is None for fact in facts):
        return None
    return _observed_sum(facts, key)


def _optional_observed_sum(facts: list[dict], key: str) -> int | None:
    if not any(fact["usage"].get(key) is not None for fact in facts):
        return None
    return _observed_sum(facts, key)


def _aggregate(facts: list[dict], unobserved: int = 0, *, local_stage: bool = False) -> dict:
    billable = [fact for fact in facts if not fact["local"]]
    tokens = facts if local_stage else billable
    known_usage = sum(fact["usage"].get("input_tokens") is not None
                      and fact["usage"].get("output_tokens") is not None for fact in billable)
    missing_cost = unobserved + sum(fact["estimate"] is None for fact in facts)
    known_estimate = sum((fact["estimate"] for fact in facts if fact["estimate"] is not None), _ZERO)
    known_actual = sum((fact["actual"] for fact in facts if fact["actual"] is not None), _ZERO)
    actual_missing = unobserved + sum(fact["actual"] is None for fact in facts)
    return {
        "attempt_count": len(facts), "known_usage_count": known_usage,
        "missing_usage_count": len(billable) + unobserved - known_usage,
        "input_tokens": _complete_token_sum(tokens, "input_tokens", unobserved),
        "output_tokens": _complete_token_sum(tokens, "output_tokens", unobserved),
        "known_input_tokens": _observed_sum(tokens, "input_tokens"),
        "known_output_tokens": _observed_sum(tokens, "output_tokens"),
        "cached_input_tokens": _optional_observed_sum(facts, "cached_input_tokens"),
        "reasoning_tokens": _optional_observed_sum(facts, "reasoning_tokens"),
        "estimated_total_usd": usd_string(known_estimate) if not missing_cost else None,
        "known_partial_estimated_usd": usd_string(known_estimate),
        "missing_cost_count": missing_cost,
        "actual_total_usd": usd_string(known_actual) if not actual_missing else None,
        "known_partial_actual_usd": usd_string(known_actual),
    }


def _work_row(work: dict, facts: list[dict], unobserved: int) -> dict:
    aggregate = _aggregate(facts, unobserved)
    completion = work["completion"]
    row = {
        "work_id": work["work_id"], "product_ref": work["product_ref"],
        "task_pair_id": work["task_pair_id"], "arm": work["arm"], "status": work["status"],
        **{key: aggregate[key] for key in WORK_TABLE_COLUMNS if key in aggregate},
        "model_api_attempt_count": sum(not fact["local"] for fact in facts),
        "local_stage_count": sum(fact["local"] for fact in facts),
        "pending_count": sum(fact["status"] == "PENDING" for fact in facts),
        "failed_count": sum(fact["status"] == "FAILED" for fact in facts),
        "revision_attempt_count": sum(fact["identity"]["stage"] == "revision" for fact in facts),
        "unobserved_image_job_count": unobserved,
        "final_artifact_sha256": completion["artifact_sha256"] if completion is not None else None,
    }
    groups = defaultdict(list)
    for fact in facts:
        identity = fact["identity"]
        groups[(identity["stage"], identity["provider"], fact["model"])].append(fact)
    stages = []
    for (stage, provider, model), group in sorted(groups.items()):
        values = {"work_id": work["work_id"], "stage": stage, "provider": provider, "model": model,
                  **_aggregate(group, local_stage=provider == "local")}
        stages.append({key: values[key] for key in STAGE_TABLE_COLUMNS})
    return {
        **{key: row[key] for key in WORK_TABLE_COLUMNS},
        "stages": stages,
        "attempts": [{**fact["identity"], "receipt": fact["receipt"]} for fact in facts],
        "required_stages": work["required_stages"],
        "quality_policy_version": completion["quality_policy_version"] if completion is not None else None,
        "rate_versions": sorted({fact["rate_version"] for fact in facts
                                 if not fact["local"] and fact["rate_version"] is not None}),
    }


def compare_work_rows(rows: list[dict]) -> dict:
    """Compare validated work-summary rows; shared by reporting and readback.

Callers validate the wire first. Unknown applied rates also imply an unpriced
nonlocal stage. Unobserved bindings alone do not invent a rate observation.
"""
    groups = defaultdict(list)
    artifacts = Counter((row["arm"], row["final_artifact_sha256"]) for row in rows
                        if row["status"] == "COMPLETED" and row["arm"] is not None)
    for row in rows:
        key = (row["task_pair_id"], "") if row["task_pair_id"] is not None else (None, row["work_id"])
        groups[key].append(row)
    totals = {"baseline": _ZERO, "candidate": _ZERO}
    compared = 0
    excluded, reasons = [], set()
    for key, members in sorted(groups.items(), key=lambda item: (item[0][0] or "", item[0][1])):
        pair_reasons = set()
        by_arm = {row["arm"]: row for row in members}
        paired = len(members) == 2 and set(by_arm) == {"baseline", "candidate"}
        if not paired:
            pair_reasons.add("UNPAIRED_WORK")
        if any(row["status"] != "COMPLETED" for row in members):
            pair_reasons.add("NOT_COMPLETED")
        if any(row["estimated_total_usd"] is None for row in members):
            pair_reasons.add("COST_MISSING")
        if any(row["status"] == "COMPLETED"
               and artifacts[(row["arm"], row["final_artifact_sha256"])] > 1 for row in members):
            pair_reasons.add("DUPLICATE_FINAL_ARTIFACT")
        if paired:
            baseline, candidate = by_arm["baseline"], by_arm["candidate"]
            for field, reason in (("product_ref", "PRODUCT_MISMATCH"),
                                  ("quality_policy_version", "QUALITY_MISMATCH")):
                if baseline[field] != candidate[field]:
                    pair_reasons.add(reason)
            if set(baseline["required_stages"]) != set(candidate["required_stages"]):
                pair_reasons.add("STAGES_MISMATCH")
            unknown_rate = any(stage["missing_cost_count"] > 0 for row in members
                               for stage in row["stages"] if stage["provider"] != "local")
            if unknown_rate or baseline["rate_versions"] != candidate["rate_versions"]:
                pair_reasons.add("RATE_MISMATCH")
        if pair_reasons:
            reasons.update(pair_reasons)
            excluded.append({"task_pair_id": key[0], "work_ids": sorted(row["work_id"] for row in members),
                             "reason_codes": sorted(pair_reasons)})
            continue
        compared += 1
        for arm in totals:
            # Sum the same exact values published in the work rows.
            totals[arm] += Decimal(by_arm[arm]["estimated_total_usd"])
    arms = {
        arm: {"completed_work_count": compared,
              "total_estimated_cost_usd": usd_string(total) if compared else None,
              "estimated_cost_per_completed_ad_usd": usd_string(total / compared) if compared else None}
        for arm, total in totals.items()
    }
    reduction = difference = None
    if not compared:
        reasons.add("NO_COMPARABLE_PAIRS")
    else:
        difference = usd_string(totals["baseline"] - totals["candidate"])
        if totals["baseline"] == 0:
            reasons.add("BASELINE_ZERO_COST")
        else:
            reduction = usd_string((totals["baseline"] - totals["candidate"]) / totals["baseline"] * 100)
    return {
        "scope": "MATCHED_COMPLETED_WORKS", "planned_pair_count": len(groups),
        "compared_pair_count": compared, "excluded_pair_count": len(excluded),
        "excluded_work_count": len(rows) - compared * 2, **arms,
        "estimated_cost_reduction_pct": reduction, "estimated_cost_difference_usd": difference,
        "excluded_pairs": excluded, "reason_codes": sorted(reasons),
    }


def summarize_work_dataset(data: dict) -> dict:
    """Validate a bounded project dataset and return its exact report wire shape."""
    normalized = normalize_work_dataset(data)
    receipts = {receipt["attempt_id"]: receipt for receipt in normalized["receipts"]}
    facts_by_work = defaultdict(list)
    unobserved_by_work = Counter(binding["work_id"] for binding in normalized["image_bindings"]
                                 if binding["attempt_id"] is None)
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        facts = []
        for attempt in normalized["attempts"]:
            fact = _fact(attempt, receipts.get(attempt["attempt_id"]), normalized["rate_book"])
            facts_by_work[attempt["work_id"]].append(fact)
            facts.append(fact)
        rows = [_work_row(work, facts_by_work[work["work_id"]], unobserved_by_work[work["work_id"]])
                for work in normalized["works"]]
        aggregate = _aggregate(facts, sum(unobserved_by_work.values()))
        completed_ads = len({row["final_artifact_sha256"] for row in rows if row["status"] == "COMPLETED"})
        summary_keys = (
            "estimated_total_usd", "known_partial_estimated_usd", "actual_total_usd",
            "known_partial_actual_usd", "input_tokens", "output_tokens", "known_input_tokens",
            "known_output_tokens", "missing_usage_count", "missing_cost_count",
        )
        summary = {
            "work_count": len(rows), "completed_work_count": sum(row["status"] == "COMPLETED" for row in rows),
            "open_work_count": sum(row["status"] == "OPEN" for row in rows),
            "cancelled_work_count": sum(row["status"] == "CANCELLED" for row in rows),
            "completed_ad_count": completed_ads,
            **{key: aggregate[key] for key in summary_keys},
            "cost_per_completed_ad_usd": (
                usd_string(Decimal(aggregate["estimated_total_usd"]) / completed_ads)
                if completed_ads and aggregate["estimated_total_usd"] is not None else None),
        }
        return {
            "schema_version": "beautyai-work-summary-v1", "scope": "MODEL_API_COST_ONLY",
            "authority": "DECLARED_WORK_ACCEPTANCE", "summary": summary, "works": rows,
            "comparison": compare_work_rows(rows),
        }
