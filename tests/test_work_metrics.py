"""Work accounting preserves partial evidence and excludes unmatched savings."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from beauty_ai.work_metrics import STAGE_TABLE_COLUMNS, WORK_TABLE_COLUMNS, summarize_work_dataset


PROJECT = str(uuid5(NAMESPACE_URL, "work-metrics-project"))
USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "input_text_tokens",
              "input_image_tokens", "cached_input_tokens", "reasoning_tokens")


def uid(name):
    return str(uuid5(NAMESPACE_URL, name))


def hashed(name):
    return sha256(name.encode()).hexdigest()


def dataset():
    return {"schema_version": "beautyai-work-dataset-v1", "data_kind": "SYNTHETIC_FIXTURE",
            "works": [], "attempts": [], "receipts": [], "image_bindings": [], "rate_book": None}


def add_work(data, name, *, pair=None, arm=None, **changes):
    item = {"project_id": PROJECT, "work_id": uid(name), "product_ref": "product-1",
            "task_pair_id": pair, "arm": arm, "required_stages": ["image_generation"],
            "status": "OPEN", "created_at": "2026-11-03T00:00:00Z",
            "closed_at": None, "completion": None, **changes}
    data["works"].append(item)
    return item


def add_attempt(data, work, name, *, status="SUCCEEDED", amount="1", actual=None,
                stage="image_generation", model="model-a", provider="openai", usage=None,
                no_usage=False, rate="rates-v1", artifact=None):
    identity = {"attempt_id": uid(name), "project_id": PROJECT, "work_id": work["work_id"],
                "stage": stage, "operation": "generate", "provider": provider,
                "configured_model": model, "prompt_version": "prompt-v1", "request_hash": hashed(name),
                "parent_attempt_id": None, "started_at": "2026-11-03T00:01:00Z"}
    data["attempts"].append(identity)
    if status == "PENDING":
        return identity
    token_usage = {key: None for key in USAGE_KEYS}
    token_usage.update({"input_tokens": 100, "output_tokens": 20} if usage is None else usage)
    receipt = {"schema_version": "beautyai-work-receipt-v1", "attempt_id": identity["attempt_id"],
               "provider_request_id": "request-" + name, "model": model,
               "usage": None if no_usage else token_usage, "elapsed_seconds": 0.5,
               "status": status, "error_code": "FIXTURE_FAILURE" if status != "SUCCEEDED" else None,
               "artifact_sha256": artifact or (hashed(name + "-artifact") if status == "SUCCEEDED" else None),
               "estimated_cost_usd": amount, "rate_version": rate if amount is not None else None,
               "actual_cost_usd": actual, "billing_evidence_ref": "bill-" + name if actual is not None else None,
               "source_ref": "fixture:" + name, "source_sha256": hashed(name + "-source")}
    data["receipts"].append(receipt)
    return identity


def complete(data, work, *, artifact=None, policy="quality-v1"):
    work["status"], work["closed_at"] = "COMPLETED", "2026-11-03T00:02:00Z"
    attempts = [item for item in data["attempts"] if item["work_id"] == work["work_id"]]
    if artifact is None:
        ids = {item["attempt_id"] for item in attempts}
        artifact = next(item["artifact_sha256"] for item in data["receipts"]
                        if item["attempt_id"] in ids and item["status"] == "SUCCEEDED")
    work["completion"] = {"artifact_sha256": artifact, "reviewer_ref": "reviewer-1",
                          "quality_policy_version": policy, "quality_pass": True,
                          "expected_attempt_ids": [item["attempt_id"] for item in attempts]}


def pair(data, name="pair-1", *, baseline="2", candidate="1", baseline_model="model-a", candidate_model="model-a"):
    result = []
    for arm, amount, model in (("baseline", baseline, baseline_model), ("candidate", candidate, candidate_model)):
        work = add_work(data, name + "-" + arm, pair=name, arm=arm)
        add_attempt(data, work, name + "-" + arm, amount=amount, model=model)
        complete(data, work)
        result.append(work)
    return result


def rate_book(**models):
    return {"schema_version": "beautyai-work-rates-v1", "rate_version": "rates-v1", "models": models}


def rate(*, mode="combined", input_usd="2", output_usd="4", text=None, image=None):
    return {"mode": mode, "input_usd": input_usd, "input_text_usd": text,
            "input_image_usd": image, "output_usd": output_usd}


def test_failed_revision_cancelled_and_pending_costs_stay_in_global_totals():
    data = dataset()
    done = add_work(data, "done", required_stages=["image_generation", "composition"])
    add_attempt(data, done, "done-image", amount="1.25", actual="1.1")
    add_attempt(data, done, "done-revision", status="FAILED", stage="revision", amount="0.1",
                usage={"input_tokens": 50, "output_tokens": 3})
    add_attempt(data, done, "done-composition", provider="local", model="local", stage="composition",
                amount="0", rate="local-api-zero-v1", usage={"input_tokens": 0, "output_tokens": 0})
    complete(data, done)
    cancelled = add_work(data, "cancelled", status="CANCELLED", closed_at="2026-11-03T00:02:00Z")
    add_attempt(data, cancelled, "cancelled-call", status="FAILED", amount="2.25", actual="2",
                usage={"input_tokens": 4, "output_tokens": 1})
    pending = add_work(data, "pending")
    attempt = add_attempt(data, pending, "pending-call", status="PENDING")
    data["image_bindings"] = [
        {"project_id": PROJECT, "work_id": pending["work_id"], "job_id": uid("bound-pending"),
         "attempt_id": attempt["attempt_id"]},
        {"project_id": PROJECT, "work_id": pending["work_id"], "job_id": uid("unobserved"), "attempt_id": None},
    ]
    result = summarize_work_dataset(data)
    summary = result["summary"]
    assert (summary["work_count"], summary["completed_work_count"], summary["open_work_count"],
            summary["cancelled_work_count"], summary["completed_ad_count"]) == (3, 1, 1, 1, 1)
    assert summary["estimated_total_usd"] is None
    assert summary["known_partial_estimated_usd"] == "3.600000000"
    assert summary["actual_total_usd"] is None
    assert summary["known_partial_actual_usd"] == "3.100000000"
    assert (summary["missing_cost_count"], summary["missing_usage_count"]) == (2, 2)
    assert (summary["known_input_tokens"], summary["known_output_tokens"]) == (154, 24)
    assert summary["input_tokens"] is summary["output_tokens"] is summary["cost_per_completed_ad_usd"] is None
    by_id = {item["work_id"]: item for item in result["works"]}
    row = by_id[done["work_id"]]
    assert (row["attempt_count"], row["model_api_attempt_count"], row["local_stage_count"],
            row["failed_count"], row["revision_attempt_count"], row["known_usage_count"]) == (3, 2, 1, 1, 1, 2)
    local = next(item for item in row["stages"] if item["provider"] == "local")
    assert (local["known_usage_count"], local["missing_usage_count"], local["input_tokens"], local["output_tokens"]) == (0, 0, 0, 0)
    assert local["cached_input_tokens"] is local["reasoning_tokens"] is None
    pending_row = by_id[pending["work_id"]]
    assert (pending_row["pending_count"], pending_row["unobserved_image_job_count"], pending_row["missing_usage_count"]) == (1, 1, 2)
    assert pending_row["attempts"][0]["receipt"] is None


def test_partial_usage_keeps_independent_input_sum_and_ignores_total_cache_reasoning_as_addends():
    data = dataset()
    work = add_work(data, "partial")
    add_attempt(data, work, "partial-call", status="FAILED", amount="0.1",
                usage={"input_tokens": 1024, "output_tokens": None, "total_tokens": 2000,
                       "cached_input_tokens": 500, "reasoning_tokens": 10})
    row = summarize_work_dataset(data)["works"][0]
    assert (row["input_tokens"], row["output_tokens"], row["known_input_tokens"], row["known_output_tokens"]) == (1024, None, 1024, 0)
    assert (row["known_usage_count"], row["missing_usage_count"]) == (0, 1)
    assert row["estimated_total_usd"] == "0.100000000"
    assert (row["stages"][0]["cached_input_tokens"], row["stages"][0]["reasoning_tokens"]) == (500, 10)


def test_snapshot_combined_split_and_explicit_receipt_precedence_use_applied_rate_versions():
    data = dataset()
    work = add_work(data, "rates")
    add_attempt(data, work, "combined", amount=None,
                usage={"input_tokens": 1_000_000, "output_tokens": 500_000, "cached_input_tokens": 100})
    add_attempt(data, work, "split", model="model-b", amount=None,
                usage={"input_tokens": 1_000_000, "input_text_tokens": 250_000,
                       "input_image_tokens": 750_000, "output_tokens": 500_000})
    add_attempt(data, work, "explicit", amount="0.125", no_usage=True, rate="explicit-v2")
    data["rate_book"] = rate_book(**{"model-a": rate(), "model-b": rate(mode="split", input_usd=None,
                                                                          text="1", image="3", output_usd="2")})
    result = summarize_work_dataset(data)
    row = result["works"][0]
    assert row["estimated_total_usd"] == "7.625000000"
    assert row["rate_versions"] == ["explicit-v2", "rates-v1"]
    assert row["known_input_tokens"] == 2_000_000
    assert row["input_tokens"] is None
    assert row["known_usage_count"] == 2
    assert result["summary"]["known_partial_estimated_usd"] == "7.625000000"
    # Derived pricing does not rewrite immutable source receipts.
    assert next(item for item in row["attempts"] if item["attempt_id"] == uid("combined"))["receipt"]["estimated_cost_usd"] is None


@pytest.mark.parametrize("model,usage,book", [
    ("unsupported", {"input_tokens": 0, "output_tokens": 0}, rate_book(**{"model-a": rate()})),
    ("model-a", {"input_tokens": 100, "output_tokens": None}, rate_book(**{"model-a": rate()})),
    ("model-a", {"input_tokens": 100, "output_tokens": 20}, None),
    ("model-a", {"input_tokens": 100, "output_tokens": 20}, rate_book(**{"model-a": rate(mode="split", input_usd=None, text="1", image="2")})),
])
def test_unknown_price_or_required_usage_stays_null_even_for_observed_zero_tokens(model, usage, book):
    data = dataset()
    work = add_work(data, "missing-rate")
    add_attempt(data, work, "missing-rate-call", model=model, amount=None, usage=usage)
    data["rate_book"] = book
    row = summarize_work_dataset(data)["works"][0]
    assert row["estimated_total_usd"] is None
    assert row["known_partial_estimated_usd"] == "0.000000000"
    assert row["missing_cost_count"] == 1


def test_empty_work_and_local_pending_do_not_claim_an_observed_local_zero_receipt():
    data = dataset()
    empty = add_work(data, "empty")
    local = add_work(data, "local-pending")
    add_attempt(data, local, "local-pending-call", status="PENDING", provider="local", model="local", stage="composition")
    rows = {row["work_id"]: row for row in summarize_work_dataset(data)["works"]}
    assert rows[empty["work_id"]]["estimated_total_usd"] == "0.000000000"
    row = rows[local["work_id"]]
    assert row["estimated_total_usd"] is None
    assert row["missing_cost_count"] == 1
    assert (row["known_usage_count"], row["missing_usage_count"]) == (0, 0)
    assert row["stages"][0]["input_tokens"] is None


def test_comparison_accepts_different_models_under_same_applied_price_policy():
    data = dataset()
    pair(data, baseline_model="model-a", candidate_model="model-b")
    result = summarize_work_dataset(data)
    comparison = result["comparison"]
    assert comparison == {
        "scope": "MATCHED_COMPLETED_WORKS", "planned_pair_count": 1, "compared_pair_count": 1,
        "excluded_pair_count": 0, "excluded_work_count": 0,
        "baseline": {"completed_work_count": 1, "total_estimated_cost_usd": "2.000000000", "estimated_cost_per_completed_ad_usd": "2.000000000"},
        "candidate": {"completed_work_count": 1, "total_estimated_cost_usd": "1.000000000", "estimated_cost_per_completed_ad_usd": "1.000000000"},
        "estimated_cost_reduction_pct": "50.000000000", "estimated_cost_difference_usd": "1.000000000",
        "excluded_pairs": [], "reason_codes": [],
    }
    assert result["summary"]["cost_per_completed_ad_usd"] == "1.500000000"


@pytest.mark.parametrize("condition,reason", [
    ("product", "PRODUCT_MISMATCH"), ("quality", "QUALITY_MISMATCH"),
    ("stages", "STAGES_MISMATCH"), ("cost", "COST_MISSING"),
    ("rate", "RATE_MISMATCH"), ("open", "NOT_COMPLETED"),
])
def test_each_comparison_condition_excludes_both_members(condition, reason):
    data = dataset()
    _, candidate = pair(data)
    receipt = next(item for item in data["receipts"] if item["attempt_id"] == uid("pair-1-candidate"))
    if condition == "product":
        candidate["product_ref"] = "product-other"
    elif condition == "quality":
        candidate["completion"]["quality_policy_version"] = "quality-other"
    elif condition == "stages":
        candidate["required_stages"].append("review")
        add_attempt(data, candidate, "review", stage="review", amount="0")
        complete(data, candidate)
    elif condition == "cost":
        receipt.update(estimated_cost_usd=None, rate_version=None)
    elif condition == "rate":
        receipt["rate_version"] = "other-rates"
    else:
        candidate.update(status="OPEN", completion=None, closed_at=None)
    comparison = summarize_work_dataset(data)["comparison"]
    assert comparison["compared_pair_count"] == 0
    assert (comparison["excluded_pair_count"], comparison["excluded_work_count"]) == (1, 2)
    assert reason in comparison["excluded_pairs"][0]["reason_codes"]
    assert "NO_COMPARABLE_PAIRS" in comparison["reason_codes"]
    assert comparison["baseline"]["total_estimated_cost_usd"] is None
    assert comparison["candidate"]["estimated_cost_per_completed_ad_usd"] is None
    assert comparison["estimated_cost_reduction_pct"] is comparison["estimated_cost_difference_usd"] is None


def test_unpaired_and_incomplete_pair_exclusions_keep_global_costs():
    data = dataset()
    pair(data, "valid")
    lone = add_work(data, "lone", pair="missing-candidate", arm="baseline")
    add_attempt(data, lone, "lone", amount="8")
    complete(data, lone)
    unpaired = add_work(data, "unpaired")
    add_attempt(data, unpaired, "unpaired", status="FAILED", amount="4")
    result = summarize_work_dataset(data)
    comparison = result["comparison"]
    assert (comparison["planned_pair_count"], comparison["compared_pair_count"], comparison["excluded_pair_count"], comparison["excluded_work_count"]) == (3, 1, 2, 2)
    assert {item["task_pair_id"] for item in comparison["excluded_pairs"]} == {None, "missing-candidate"}
    assert all("UNPAIRED_WORK" in item["reason_codes"] for item in comparison["excluded_pairs"])
    assert result["summary"]["estimated_total_usd"] == "15.000000000"
    assert result["summary"]["cost_per_completed_ad_usd"] == "5.000000000"


def test_duplicate_artifact_in_same_arm_excludes_every_affected_pair_and_deduplicates_ad_count():
    data = dataset()
    first, _ = pair(data, "first")
    second, _ = pair(data, "second")
    artifact = first["completion"]["artifact_sha256"]
    next(item for item in data["receipts"] if item["attempt_id"] == uid("second-baseline"))["artifact_sha256"] = artifact
    second["completion"]["artifact_sha256"] = artifact
    result = summarize_work_dataset(data)
    assert result["summary"]["completed_ad_count"] == 3
    assert result["summary"]["cost_per_completed_ad_usd"] == "2.000000000"
    assert result["comparison"]["compared_pair_count"] == 0
    assert len(result["comparison"]["excluded_pairs"]) == 2
    assert all("DUPLICATE_FINAL_ARTIFACT" in item["reason_codes"] for item in result["comparison"]["excluded_pairs"])


def test_half_declared_pair_keeps_pair_identity_and_excludes_the_entire_group():
    data = dataset()
    pair(data, "ambiguous")
    extra = add_work(data, "no-arm", pair="ambiguous", arm=None)
    add_attempt(data, extra, "no-arm")
    complete(data, extra)
    comparison = summarize_work_dataset(data)["comparison"]
    assert (comparison["planned_pair_count"], comparison["compared_pair_count"], comparison["excluded_work_count"]) == (1, 0, 3)
    assert comparison["excluded_pairs"][0]["task_pair_id"] == "ambiguous"
    assert "UNPAIRED_WORK" in comparison["excluded_pairs"][0]["reason_codes"]


@pytest.mark.parametrize("baseline,candidate,reduction,difference,reason", [
    ("0", "1", None, "-1.000000000", "BASELINE_ZERO_COST"),
    ("1", "2", "-100.000000000", "-1.000000000", None),
    ("1", "0", "100.000000000", "1.000000000", None),
])
def test_comparison_zero_baseline_and_cost_increases_are_explicit(baseline, candidate, reduction, difference, reason):
    data = dataset()
    pair(data, baseline=baseline, candidate=candidate)
    comparison = summarize_work_dataset(data)["comparison"]
    assert comparison["estimated_cost_reduction_pct"] == reduction
    assert comparison["estimated_cost_difference_usd"] == difference
    assert comparison["reason_codes"] == ([reason] if reason else [])


def test_large_exact_integer_and_price_values_survive_hostile_decimal_context():
    data = dataset()
    work = add_work(data, "precise")
    add_attempt(data, work, "large", amount=None, usage={"input_tokens": 2**63 - 1, "output_tokens": 0})
    data["rate_book"] = rate_book(**{"model-a": rate(input_usd="1000000000", output_usd="0")})
    expected = summarize_work_dataset(data)
    assert expected["summary"]["estimated_total_usd"] == "9223372036854775807000.000000000"
    assert expected["summary"]["input_tokens"] == 2**63 - 1
    with localcontext() as context:
        context.prec, context.rounding = 2, ROUND_UP
        context.traps[Inexact] = True
        assert summarize_work_dataset(data) == expected


def test_rounding_and_nested_result_detachment_are_deterministic():
    data = dataset()
    work = add_work(data, "rounding")
    for name in ("round-1", "round-2"):
        add_attempt(data, work, name, amount=None, usage={"input_tokens": 1_000_000, "output_tokens": 500_000})
    data["rate_book"] = rate_book(**{"model-a": rate(input_usd="0.123456789", output_usd="0.333333333")})
    before = deepcopy(data)
    result = summarize_work_dataset(data)
    assert data == before
    assert result["summary"]["estimated_total_usd"] == "0.580246912"
    assert result["works"][0]["stages"][0]["estimated_total_usd"] == "0.580246912"
    data["attempts"].reverse()
    data["receipts"].reverse()
    assert summarize_work_dataset(data) == result
    result["works"][0]["attempts"][0]["receipt"]["usage"]["input_tokens"] = 0
    assert data["receipts"][0]["usage"]["input_tokens"] == 1_000_000


def test_exact_wire_columns_and_empty_dataset_unknown_comparison():
    data = dataset()
    result = summarize_work_dataset(data)
    assert set(result) == {"schema_version", "scope", "authority", "summary", "works", "comparison"}
    assert result["summary"]["completed_ad_count"] == 0
    assert result["summary"]["cost_per_completed_ad_usd"] is None
    assert result["comparison"]["reason_codes"] == ["NO_COMPARABLE_PAIRS"]
    assert result["comparison"]["planned_pair_count"] == 0
    pair(data)
    result = summarize_work_dataset(data)
    row = result["works"][0]
    assert set(row) == set(WORK_TABLE_COLUMNS) | {"stages", "attempts", "required_stages", "quality_policy_version", "rate_versions"}
    assert tuple(row["stages"][0]) == STAGE_TABLE_COLUMNS
    assert tuple(row)[:len(WORK_TABLE_COLUMNS)] == WORK_TABLE_COLUMNS
    json.dumps(result, allow_nan=False)


def test_metrics_reject_cross_project_and_receipt_conflict_instead_of_partial_success():
    data = dataset()
    work = add_work(data, "invalid")
    add_attempt(data, work, "invalid")
    data["attempts"][0]["project_id"] = uid("other-project")
    with pytest.raises(ValueError, match="^WORK_"):
        summarize_work_dataset(data)
    data["attempts"][0]["project_id"] = PROJECT
    data["receipts"].append({**data["receipts"][0], "estimated_cost_usd": "2"})
    with pytest.raises(ValueError, match="^WORK_"):
        summarize_work_dataset(data)
