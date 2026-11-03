"""Offline comparisons: accepted outputs, complete costs, and matched products."""
from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
import json

import pytest

from beauty_ai.cost_savings import compare_costs


def hash_for(value):
    return sha256(value.encode()).hexdigest()


def cost(cost_id, batch_id, amount="2", **changes):
    return {
        "cost_id": cost_id, "revision": 1, "supersedes_revision": None,
        "source_ref": f"fixture:{cost_id}", "source_sha256": hash_for(cost_id),
        "kind": "image", "category": "production", "estimated_cost_usd": amount,
        "rate_version": "rate-1", "actual_cost_usd": amount,
        "billing_status": "RECONCILED", "billing_evidence_ref": f"billing:{cost_id}",
        "allocations": {batch_id: 10000}, "state": "SUCCEEDED", **changes,
    }


def acceptance(batch_id, index, **changes):
    ad_id = f"{batch_id}-ad-{index}"
    return {
        "ad_id": ad_id, "revision": 1, "supersedes_revision": None,
        "batch_id": batch_id, "action": "accept", "artifact_sha256": hash_for(ad_id),
        "reviewer_ref": "reviewer:fixture", "quality_policy_version": "quality-1",
        "quality_pass": True, **changes,
    }


def fixture():
    dataset = {"schema_version": "beautyai-cost-dataset-v1", "cost_items": [],
               "batches": [], "acceptances": []}
    manifest = {
        "schema_version": "beautyai-cost-comparison-v1", "comparison_id": "comparison-1",
        "accounting_window_id": "window-1", "cost_scope": ["image"],
        "quality_policy_version": "quality-1", "estimate_rate_version": "rate-1", "pairs": [],
    }
    add_pair(dataset, manifest, "product-a", 4, 5, "2", "1.5")
    return dataset, manifest


def add_pair(dataset, manifest, product, baseline_count, candidate_count, baseline_cost, candidate_cost):
    pair = {"product_id": product}
    for arm, count, amount in (("baseline", baseline_count, baseline_cost),
                               ("candidate", candidate_count, candidate_cost)):
        batch_id = f"{product}-{arm}"
        cost_id = f"cost-{batch_id}"
        pair[f"{arm}_batch_id"] = batch_id
        dataset["batches"].append({"batch_id": batch_id, "product_id": product,
                                   "expected_cost_ids": [cost_id]})
        dataset["cost_items"].append(cost(cost_id, batch_id, amount))
        dataset["acceptances"].extend(acceptance(batch_id, index) for index in range(count))
    manifest["pairs"].append(pair)


def test_required_example_distinguishes_counterfactual_from_actual_cash_difference():
    dataset, manifest = fixture()
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        section = report[basis]
        assert section["baseline"]["total_cost_usd"] == "2.000000000"
        assert section["candidate"]["total_cost_usd"] == "1.500000000"
        assert section["baseline"]["unit_cost_usd"] == "0.500000000"
        assert section["candidate"]["unit_cost_usd"] == "0.300000000"
        assert section["unit_cost_reduction_pct"] == "40.000000000"
        assert section["counterfactual_difference_usd"] == "1.000000000"
        assert section["reason_codes"] == []
        assert Decimal(section["baseline"]["total_cost_usd"]) - Decimal(section["candidate"]["total_cost_usd"]) == Decimal(".5")
    assert report["counterfactual_is_actual_cash_difference"] is False
    assert report["scope_authority"] == "DECLARED_SCOPE"
    assert report["quality_authority"] == "DECLARED_QUALITY"
    assert report["per_product_counts"][0]["baseline_accepted_ads"] == 4
    assert report["per_product_counts"][0]["candidate_accepted_ads"] == 5
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("state", ["FAILED", "UNKNOWN", "SUBMITTING", "MISSING", "RECORDED"])
def test_known_production_cost_is_not_dropped_for_attempt_state(state):
    dataset, manifest = fixture()
    dataset["cost_items"][0]["state"] = state
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["total_cost_usd"] == "2.000000000"
    assert report["estimated"]["unit_cost_reduction_pct"] == "40.000000000"


def test_missing_observation_preserves_known_partial_and_never_infers_zero():
    dataset, manifest = fixture()
    batch = dataset["batches"][0]
    batch["expected_cost_ids"].append("missing-attempt")
    dataset["cost_items"].append(cost(
        "missing-attempt", batch["batch_id"], None, rate_version=None,
        actual_cost_usd=None, billing_status="UNRECONCILED", billing_evidence_ref=None, state="MISSING",
    ))
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        arm = report[basis]["baseline"]
        assert arm["total_cost_usd"] is None
        assert arm["unit_cost_usd"] is None
        assert arm["known_partial_usd"] == "2.000000000"
        assert arm["known_cost_count"] == 1
        assert arm["cost_item_count"] == 2
        assert arm["missing_cost_count"] == 1
        assert arm["missing_cost_ids"] == ["missing-attempt"]
        assert report[basis]["unit_cost_reduction_pct"] is None
        assert report[basis]["counterfactual_difference_usd"] is None
        assert report[basis]["candidate"]["total_cost_usd"] == "1.500000000"


def test_partial_billing_blocks_actual_only_and_retains_known_amount():
    dataset, manifest = fixture()
    dataset["cost_items"][0].update(actual_cost_usd="0.75", billing_status="PARTIAL")
    report = compare_costs(dataset, manifest)
    assert report["estimated"]["unit_cost_reduction_pct"] == "40.000000000"
    assert report["actual"]["baseline"]["known_partial_usd"] == "0.750000000"
    assert report["actual"]["baseline"]["known_cost_count"] == 1
    assert report["actual"]["baseline"]["missing_cost_count"] == 1
    assert report["actual"]["baseline"]["total_cost_usd"] is None
    assert report["actual"]["reason_codes"] == ["ACTUAL_COST_UNRECONCILED"]


def test_estimate_rate_mismatch_blocks_estimate_only():
    dataset, manifest = fixture()
    dataset["cost_items"][0]["rate_version"] = "rate-2"
    report = compare_costs(dataset, manifest)
    assert report["estimated"]["baseline"]["known_partial_usd"] == "2.000000000"
    assert report["estimated"]["baseline"]["missing_cost_count"] == 1
    assert report["estimated"]["unit_cost_reduction_pct"] is None
    assert report["estimated"]["reason_codes"] == ["ESTIMATE_RATE_MISMATCH"]
    assert report["actual"]["unit_cost_reduction_pct"] == "40.000000000"


def test_bases_are_never_mixed_to_complete_a_comparison():
    dataset, manifest = fixture()
    dataset["cost_items"][0].update(estimated_cost_usd=None, rate_version=None)
    dataset["cost_items"][1].update(actual_cost_usd=None, billing_evidence_ref=None,
                                     billing_status="UNRECONCILED")
    report = compare_costs(dataset, manifest)
    assert report["estimated"]["unit_cost_reduction_pct"] is None
    assert report["actual"]["unit_cost_reduction_pct"] is None
    assert report["estimated"]["baseline"]["known_cost_count"] == 0
    assert report["actual"]["candidate"]["known_cost_count"] == 0


def test_actual_amount_without_billing_proof_is_rejected():
    dataset, manifest = fixture()
    dataset["cost_items"][0]["billing_evidence_ref"] = None
    with pytest.raises(ValueError, match="EVIDENCE"):
        compare_costs(dataset, manifest)


@pytest.mark.parametrize("change", [{"action": "revoke"}, {"quality_pass": False}])
def test_revoked_and_quality_failed_outputs_are_not_accepted(change):
    dataset, manifest = fixture()
    original = dataset["acceptances"][0]
    dataset["acceptances"].append({**original, "revision": 2, "supersedes_revision": 1, **change})
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["accepted_ads"] == 3
    assert report["actual"]["baseline"]["total_cost_usd"] == "2.000000000"
    assert report["actual"]["unit_cost_reduction_pct"] == "55.000000000"


def test_identical_artifact_counts_once_per_arm_but_can_appear_in_both_arms():
    dataset, manifest = fixture()
    first = dataset["acceptances"][0]
    dataset["acceptances"].append({**first, "ad_id": "same-output-copy"})
    dataset["acceptances"][4]["artifact_sha256"] = first["artifact_sha256"]
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["accepted_ads"] == 4
    assert report["actual"]["candidate"]["accepted_ads"] == 5
    assert report["actual"]["unit_cost_reduction_pct"] == "40.000000000"


def test_quality_policy_mismatch_blocks_both_bases_without_counting_unqualified_ad():
    dataset, manifest = fixture()
    dataset["acceptances"][0]["quality_policy_version"] = "quality-other"
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        assert report[basis]["baseline"]["accepted_ads"] == 3
        assert report[basis]["baseline"]["total_cost_usd"] is None
        assert report[basis]["baseline"]["known_partial_usd"] == "2.000000000"
        assert report[basis]["reason_codes"] == ["QUALITY_POLICY_MISMATCH"]


def test_scope_mismatch_blocks_instead_of_silently_dropping_cost():
    dataset, manifest = fixture()
    dataset["cost_items"][0]["kind"] = "text"
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        assert report[basis]["baseline"]["total_cost_usd"] is None
        assert report[basis]["baseline"]["known_partial_usd"] == "2.000000000"
        assert report[basis]["baseline"]["cost_item_count"] == 1
        assert report[basis]["reason_codes"] == ["COST_SCOPE_MISMATCH"]


def test_latest_cost_revision_replaces_previous_and_exact_duplicate_is_deduplicated():
    dataset, manifest = fixture()
    first = dataset["cost_items"][0]
    revised = {**first, "revision": 2, "supersedes_revision": 1,
               "estimated_cost_usd": "4", "actual_cost_usd": "4"}
    dataset["cost_items"].extend([revised, deepcopy(revised), deepcopy(first)])
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["cost_item_count"] == 1
    assert report["actual"]["baseline"]["total_cost_usd"] == "4.000000000"
    assert report["actual"]["unit_cost_reduction_pct"] == "70.000000000"


def test_research_costs_are_separate_and_do_not_block_production():
    dataset, manifest = fixture()
    dataset["cost_items"].extend([
        cost("research-known", "unused", "500", category="research", allocations={}, kind="compute"),
        cost("research-unknown", "unused", None, category="research", allocations={},
             actual_cost_usd=None, rate_version=None, billing_evidence_ref=None,
             billing_status="UNRECONCILED", state="UNKNOWN"),
    ])
    report = compare_costs(dataset, manifest)
    assert report["actual"]["unit_cost_reduction_pct"] == "40.000000000"
    assert report["research"]["cost_item_count"] == 2
    for basis in ("estimated", "actual"):
        assert report["research"][basis]["known_partial_usd"] == "500.000000000"
        assert report["research"][basis]["total_cost_usd"] is None
        assert report["research"][basis]["known_cost_count"] == 1
        assert report["research"][basis]["missing_cost_count"] == 1


def test_pooled_cost_decline_does_not_hide_mismatched_product_mix():
    dataset, manifest = fixture()
    dataset["cost_items"].clear()
    dataset["batches"].clear()
    dataset["acceptances"].clear()
    manifest["pairs"].clear()
    add_pair(dataset, manifest, "product-a", 1, 9, "1", "9")
    add_pair(dataset, manifest, "product-b", 9, 1, "90", "10")
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        assert report[basis]["baseline"]["unit_cost_usd"] == "9.100000000"
        assert report[basis]["candidate"]["unit_cost_usd"] == "1.900000000"
        assert report[basis]["unit_cost_reduction_pct"] is None
        assert report[basis]["counterfactual_difference_usd"] is None
        assert report[basis]["reason_codes"] == ["PRODUCT_MIX_MISMATCH"]


def test_same_product_proportions_allow_different_sample_sizes():
    dataset, manifest = fixture()
    add_pair(dataset, manifest, "product-b", 8, 10, "4", "3")
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["accepted_ads"] == 12
    assert report["actual"]["candidate"]["accepted_ads"] == 15
    assert report["actual"]["unit_cost_reduction_pct"] == "40.000000000"


def test_same_artifact_with_conflicting_product_assignment_is_not_comparable():
    dataset, manifest = fixture()
    add_pair(dataset, manifest, "product-b", 4, 5, "2", "1.5")
    dataset["acceptances"][9]["artifact_sha256"] = dataset["acceptances"][0]["artifact_sha256"]
    report = compare_costs(dataset, manifest)
    assert "ARTIFACT_PRODUCT_MISMATCH" in report["actual"]["reason_codes"]
    assert report["actual"]["unit_cost_reduction_pct"] is None
    assert report["actual"]["baseline"]["accepted_ads"] == 7
    assert sum(pair["baseline_accepted_ads"] for pair in report["per_product_counts"]) == 7


def test_each_pair_needs_acceptance_even_when_arm_total_is_positive():
    dataset, manifest = fixture()
    add_pair(dataset, manifest, "product-b", 0, 1, "1", "1")
    report = compare_costs(dataset, manifest)
    assert "NO_ACCEPTED_ADS" in report["actual"]["reason_codes"]
    assert report["actual"]["baseline"]["accepted_ads"] == 4
    assert report["actual"]["baseline"]["total_cost_usd"] is None
    assert report["actual"]["unit_cost_reduction_pct"] is None


def test_no_acceptances_is_unknown_unit_cost_not_free_ads():
    dataset, manifest = fixture()
    dataset["acceptances"].clear()
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        assert report[basis]["baseline"]["accepted_ads"] == 0
        assert report[basis]["baseline"]["total_cost_usd"] is None
        assert report[basis]["baseline"]["unit_cost_usd"] is None
        assert report[basis]["reason_codes"] == ["NO_ACCEPTED_ADS"]


@pytest.mark.parametrize("baseline_amount,candidate_amount,reduction", [
    ("0", "1", None), ("2", "0", "100.000000000"), ("2", "5", "-100.000000000"),
])
def test_zero_baseline_zero_candidate_and_negative_reductions(baseline_amount, candidate_amount, reduction):
    dataset, manifest = fixture()
    for item, amount in zip(dataset["cost_items"], (baseline_amount, candidate_amount)):
        item.update(estimated_cost_usd=amount, actual_cost_usd=amount)
    report = compare_costs(dataset, manifest)
    assert report["actual"]["unit_cost_reduction_pct"] == reduction
    if reduction is None:
        assert report["actual"]["reason_codes"] == ["BASELINE_ZERO_COST"]
        assert report["actual"]["baseline"]["total_cost_usd"] is None
        assert report["actual"]["baseline"]["unit_cost_usd"] is None
        assert report["actual"]["baseline"]["known_partial_usd"] == "0.000000000"


def test_exact_allocation_remainder_and_cross_arm_shared_cost():
    dataset, manifest = fixture()
    baseline, candidate = [batch["batch_id"] for batch in dataset["batches"]]
    dataset["cost_items"] = [cost("shared", baseline, "1.000000001",
                                  allocations={baseline: 3333, candidate: 6667})]
    for batch in dataset["batches"]:
        batch["expected_cost_ids"] = ["shared"]
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["total_cost_usd"] == "0.333300000"
    assert report["actual"]["candidate"]["total_cost_usd"] == "0.666700001"
    assert report["actual"]["baseline"]["cost_item_count"] == 1
    assert report["actual"]["candidate"]["cost_item_count"] == 1


def test_aggregate_can_exceed_per_item_maximum():
    dataset, manifest = fixture()
    baseline = dataset["batches"][0]
    dataset["cost_items"][0].update(estimated_cost_usd="1000000000", actual_cost_usd="1000000000")
    dataset["cost_items"].append(cost("large-extra", baseline["batch_id"], "1000000000"))
    baseline["expected_cost_ids"].append("large-extra")
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["total_cost_usd"] == "2000000000.000000000"
    assert report["actual"]["unit_cost_reduction_pct"] == "99.999999940"


def test_input_order_and_caller_decimal_context_do_not_change_output_or_mutate_inputs():
    dataset, manifest = fixture()
    add_pair(dataset, manifest, "product-b", 8, 10, "4", "3")
    before = deepcopy((dataset, manifest))
    expected = compare_costs(dataset, manifest)
    assert (dataset, manifest) == before
    for collection in (dataset["batches"], dataset["cost_items"], dataset["acceptances"], manifest["pairs"]):
        collection.reverse()
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        assert compare_costs(dataset, manifest) == expected


def test_comparison_needs_no_files_network_or_credentials(monkeypatch):
    import builtins
    import os
    from pathlib import Path
    import socket

    dataset, manifest = fixture()

    def forbidden(*args, **kwargs):
        raise AssertionError("pure cost comparison attempted external access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert compare_costs(dataset, manifest)["actual"]["unit_cost_reduction_pct"] == "40.000000000"


def test_rounding_unit_cost_does_not_change_unrounded_reduction_formula():
    dataset, manifest = fixture()
    dataset["acceptances"] = [acceptance(dataset["batches"][0]["batch_id"], i) for i in range(3)]
    dataset["acceptances"] += [acceptance(dataset["batches"][1]["batch_id"], i) for i in range(7)]
    dataset["cost_items"][0].update(estimated_cost_usd="1", actual_cost_usd="1")
    dataset["cost_items"][1].update(estimated_cost_usd="1", actual_cost_usd="1")
    report = compare_costs(dataset, manifest)
    assert report["actual"]["baseline"]["unit_cost_usd"] == "0.333333333"
    assert report["actual"]["unit_cost_reduction_pct"] == "57.142857143"
    assert report["actual"]["counterfactual_difference_usd"] == "1.333333333"


@pytest.mark.parametrize("change,error", [
    ({"schema_version": "wrong"}, "SCHEMA_INVALID"),
    ({"unexpected": True}, "SCHEMA_INVALID"),
    ({"comparison_id": ""}, "IDENTIFIER_INVALID"),
    ({"comparison_id": "x" * 201}, "IDENTIFIER_INVALID"),
    ({"comparison_id": "secret-token"}, "IDENTIFIER_INVALID"),
    ({"accounting_window_id": "../window"}, "IDENTIFIER_INVALID"),
    ({"cost_scope": []}, "SCOPE_INVALID"),
    ({"cost_scope": ["image", "image"]}, "SCOPE_INVALID"),
    ({"cost_scope": ["image", "other"]}, "SCOPE_INVALID"),
    ({"pairs": []}, "PAIRS_INVALID"),
])
def test_manifest_strict_validation(change, error):
    dataset, manifest = fixture()
    manifest.update(change)
    with pytest.raises(ValueError, match=error):
        compare_costs(dataset, manifest)


@pytest.mark.parametrize("mutation,error", [
    ("duplicate-product", "PRODUCT_DUPLICATE"),
    ("shared-batch", "BATCH_DUPLICATE"),
    ("unknown-batch", "BATCH_UNKNOWN"),
    ("wrong-product", "PRODUCT_MISMATCH"),
    ("pair-extra", "PAIR_INVALID"),
])
def test_manifest_pair_identity_validation(mutation, error):
    dataset, manifest = fixture()
    pair = manifest["pairs"][0]
    if mutation == "duplicate-product":
        manifest["pairs"].append(deepcopy(pair))
    elif mutation == "shared-batch":
        pair["candidate_batch_id"] = pair["baseline_batch_id"]
    elif mutation == "unknown-batch":
        pair["candidate_batch_id"] = "missing-batch"
    elif mutation == "wrong-product":
        pair["product_id"] = "wrong-product"
    else:
        pair["extra"] = "value"
    with pytest.raises(ValueError, match=error):
        compare_costs(dataset, manifest)


def test_expected_cost_coverage_must_be_structurally_complete():
    dataset, manifest = fixture()
    dataset["batches"][0]["expected_cost_ids"].append("unrepresented-cost")
    with pytest.raises(ValueError, match="EXPECTED_COST_MISMATCH"):
        compare_costs(dataset, manifest)


def test_declared_empty_cost_set_does_not_infer_free_production():
    dataset, manifest = fixture()
    dataset["cost_items"].pop()
    dataset["batches"][1]["expected_cost_ids"] = []
    report = compare_costs(dataset, manifest)
    assert report["actual"]["candidate"]["total_cost_usd"] is None
    assert report["actual"]["candidate"]["known_cost_count"] == 0
    assert report["actual"]["reason_codes"] == ["NO_COST_ITEMS"]


def test_one_empty_product_batch_does_not_become_free_in_a_populated_arm():
    dataset, manifest = fixture()
    add_pair(dataset, manifest, "product-b", 4, 5, "2", "1.5")
    empty = "product-b-candidate"
    dataset["cost_items"] = [item for item in dataset["cost_items"] if empty not in item["allocations"]]
    next(batch for batch in dataset["batches"] if batch["batch_id"] == empty)["expected_cost_ids"] = []
    report = compare_costs(dataset, manifest)
    for basis in ("estimated", "actual"):
        section = report[basis]
        assert section["candidate"]["known_partial_usd"] == "1.500000000"
        assert section["candidate"]["total_cost_usd"] is None
        assert section["candidate"]["unit_cost_usd"] is None
        assert section["candidate"]["missing_batch_ids"] == [empty]
        assert section["candidate"]["missing_batch_count"] == 1
        assert section["unit_cost_reduction_pct"] is None
        assert section["counterfactual_difference_usd"] is None
        assert "NO_COST_ITEMS" in section["reason_codes"]
