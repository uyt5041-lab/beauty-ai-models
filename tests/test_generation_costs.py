"""Offline matched-generation estimates, exclusions, and strict identities."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
from hashlib import sha256
import json

import pytest

from beauty_ai import generation_costs
from beauty_ai.cost_accounting import digest
from beauty_ai.generation_costs import compare_generations, normalize_generation_manifest


def cost(cost_id, amount="1", **changes):
    return {
        "cost_id": cost_id, "revision": 1, "supersedes_revision": None,
        "source_ref": f"fixture:{cost_id}",
        "source_sha256": sha256(cost_id.encode()).hexdigest(),
        "kind": "image", "category": "research", "estimated_cost_usd": amount,
        "rate_version": "rate-1", "actual_cost_usd": None,
        "billing_status": "UNRECONCILED", "billing_evidence_ref": None,
        "allocations": {}, "state": "SUCCEEDED", **changes,
    }


def fixture(count=1, *, baseline="1", candidate="0.5", unpaired=0):
    dataset = {"schema_version": "beautyai-cost-dataset-v1", "cost_items": [],
               "batches": [], "acceptances": []}
    manifest = {
        "schema_version": "beautyai-generation-comparison-v1",
        "comparison_id": "comparison-1", "estimate_rate_version": "rate-1", "pairs": [],
    }
    for index in range(count):
        baseline_id, candidate_id = f"baseline-{index}", f"candidate-{index}"
        dataset["cost_items"].extend((cost(baseline_id, baseline), cost(candidate_id, candidate)))
        manifest["pairs"].append({"pair_id": f"pair-{index:04d}",
                                  "baseline_cost_id": baseline_id, "candidate_cost_id": candidate_id})
    dataset["cost_items"].extend(cost(f"unpaired-{i}") for i in range(unpaired))
    return dataset, manifest


def exclusions(report):
    return {record["cost_id"]: record["reason_codes"] for record in report["excluded_records"]}


def test_twenty_comparable_and_six_unpaired_match_required_saved_example():
    dataset, manifest = fixture(10, baseline="0.041213", candidate="0.0407115", unpaired=6)
    report = compare_generations(dataset, manifest)
    assert report == {
        "schema_version": "beautyai-generation-estimate-v1", "comparison_id": "comparison-1",
        "estimate_rate_version": "rate-1", "input_record_count": 26,
        "planned_pair_count": 10, "compared_pair_count": 10, "compared_record_count": 20,
        "excluded_pair_count": 0, "excluded_record_count": 6, "coverage_pct": "76.923076923",
        "baseline": {"generation_count": 10, "total_estimated_cost_usd": "0.412130000",
                     "estimated_cost_per_generation_usd": "0.041213000"},
        "candidate": {"generation_count": 10, "total_estimated_cost_usd": "0.407115000",
                      "estimated_cost_per_generation_usd": "0.040711500"},
        "estimated_cost_reduction_pct": "1.216849052", "estimated_cost_difference_usd": "0.005015000",
        "per_generation_difference_usd": "0.000501500",
        "excluded_records": [{"cost_id": f"unpaired-{i}", "reason_codes": ["UNPAIRED_RECORD"]}
                             for i in range(6)],
        "reason_codes": ["UNPAIRED_RECORD"], "assurance": "OBSERVATIONAL_NOT_QUALITY_CONTROLLED",
    }
    json.dumps(report, allow_nan=False)


def test_missing_cost_excludes_both_pair_members_but_keeps_other_pairs():
    dataset, manifest = fixture(2)
    dataset["cost_items"][0].update(estimated_cost_usd=None, rate_version=None)
    report = compare_generations(dataset, manifest)
    assert exclusions(report) == {"baseline-0": ["COST_MISSING"],
                                  "candidate-0": ["PAIRED_RECORD_EXCLUDED"]}
    assert report["compared_pair_count"] == 1
    assert report["excluded_pair_count"] == 1
    assert report["compared_record_count"] + report["excluded_record_count"] == 4
    assert report["coverage_pct"] == "50.000000000"
    assert report["estimated_cost_reduction_pct"] == "50.000000000"
    assert report["reason_codes"] == ["COST_MISSING", "PAIRED_RECORD_EXCLUDED"]


def test_both_invalid_members_keep_only_their_own_reasons():
    dataset, manifest = fixture()
    dataset["cost_items"][0].update(estimated_cost_usd=None, rate_version="other", state="FAILED")
    dataset["cost_items"][1].update(kind="text", rate_version="other")
    report = compare_generations(dataset, manifest)
    assert exclusions(report) == {"baseline-0": ["COST_MISSING", "NOT_SUCCEEDED"],
                                  "candidate-0": ["NOT_IMAGE", "RATE_MISMATCH"]}
    assert report["reason_codes"] == ["COST_MISSING", "NOT_IMAGE", "NOT_SUCCEEDED",
                                      "NO_COMPARABLE_PAIRS", "RATE_MISMATCH"]
    for arm in ("baseline", "candidate"):
        assert report[arm] == {"generation_count": 0, "total_estimated_cost_usd": None,
                               "estimated_cost_per_generation_usd": None}
    for key in ("estimated_cost_reduction_pct", "estimated_cost_difference_usd", "per_generation_difference_usd"):
        assert report[key] is None


@pytest.mark.parametrize("changes,reason", [
    ({"kind": "text"}, "NOT_IMAGE"),
    ({"kind": "review"}, "NOT_IMAGE"),
    ({"kind": "compute"}, "NOT_IMAGE"),
    ({"state": "FAILED"}, "NOT_SUCCEEDED"),
    ({"state": "SUBMITTING"}, "NOT_SUCCEEDED"),
    ({"state": "MISSING"}, "NOT_SUCCEEDED"),
    ({"state": "UNKNOWN"}, "NOT_SUCCEEDED"),
    ({"state": "RECORDED"}, "NOT_SUCCEEDED"),
    ({"rate_version": "rate-other"}, "RATE_MISMATCH"),
])
def test_each_inclusion_condition_excludes_the_whole_pair(changes, reason):
    dataset, manifest = fixture()
    dataset["cost_items"][1].update(changes)
    report = compare_generations(dataset, manifest)
    assert exclusions(report) == {"baseline-0": ["PAIRED_RECORD_EXCLUDED"], "candidate-0": [reason]}
    assert report["compared_record_count"] == 0
    assert report["excluded_record_count"] == 2
    assert report["coverage_pct"] == "0.000000000"


def test_unpaired_records_include_all_own_reasons_without_gating_valid_pairs():
    dataset, manifest = fixture(unpaired=1)
    dataset["cost_items"][-1].update(kind="review", state="UNKNOWN", rate_version="other")
    report = compare_generations(dataset, manifest)
    assert exclusions(report) == {"unpaired-0": ["NOT_IMAGE", "NOT_SUCCEEDED", "RATE_MISMATCH", "UNPAIRED_RECORD"]}
    assert report["estimated_cost_reduction_pct"] == "50.000000000"


def test_latest_correction_is_used_and_history_is_not_an_extra_generation():
    dataset, manifest = fixture()
    revised = {**dataset["cost_items"][0], "revision": 2, "supersedes_revision": 1,
               "estimated_cost_usd": "2"}
    dataset["cost_items"].extend((revised, deepcopy(revised)))
    report = compare_generations(dataset, manifest)
    assert report["input_record_count"] == report["compared_record_count"] == 2
    assert report["baseline"]["total_estimated_cost_usd"] == "2.000000000"
    assert report["estimated_cost_reduction_pct"] == "75.000000000"


def test_latest_correction_can_invalidate_previously_complete_pair():
    dataset, manifest = fixture()
    dataset["cost_items"].append({**dataset["cost_items"][0], "revision": 2, "supersedes_revision": 1,
                                  "estimated_cost_usd": None, "rate_version": None})
    report = compare_generations(dataset, manifest)
    assert report["input_record_count"] == 2
    assert report["compared_pair_count"] == 0
    assert exclusions(report)["baseline-0"] == ["COST_MISSING"]


@pytest.mark.parametrize("baseline,candidate,reduction,difference,reasons", [
    ("0", "0", None, "0.000000000", ["BASELINE_ZERO_COST"]),
    ("0", "1", None, "-1.000000000", ["BASELINE_ZERO_COST"]),
    ("1", "0", "100.000000000", "1.000000000", []),
    ("1", "2", "-100.000000000", "-1.000000000", []),
])
def test_zero_cost_and_negative_reduction_are_explicit(baseline, candidate, reduction, difference, reasons):
    dataset, manifest = fixture(baseline=baseline, candidate=candidate)
    report = compare_generations(dataset, manifest)
    assert report["compared_pair_count"] == 1
    assert report["estimated_cost_reduction_pct"] == reduction
    assert report["estimated_cost_difference_usd"] == difference
    assert report["per_generation_difference_usd"] == difference
    assert report["reason_codes"] == reasons


def test_negative_input_cost_is_rejected_by_shared_accounting_validation():
    dataset, manifest = fixture(baseline="-1")
    with pytest.raises(ValueError, match="COST_AMOUNT_INVALID"):
        compare_generations(dataset, manifest)


def test_unrounded_averages_determine_reduction_and_difference():
    dataset, manifest = fixture(3, baseline="0", candidate="0")
    dataset["cost_items"][0]["estimated_cost_usd"] = "1"
    dataset["cost_items"][1]["estimated_cost_usd"] = "0.000000001"
    report = compare_generations(dataset, manifest)
    assert report["baseline"]["estimated_cost_per_generation_usd"] == "0.333333333"
    assert report["candidate"]["estimated_cost_per_generation_usd"] == "0.000000000"
    assert report["estimated_cost_reduction_pct"] == "99.999999900"
    assert report["per_generation_difference_usd"] == "0.333333333"


def test_large_totals_can_exceed_per_record_amount_limit():
    dataset, manifest = fixture(2, baseline="1000000000", candidate="1")
    report = compare_generations(dataset, manifest)
    assert report["baseline"]["total_estimated_cost_usd"] == "2000000000.000000000"
    assert report["estimated_cost_reduction_pct"] == "99.999999900"


def test_order_context_and_input_immutability():
    dataset, manifest = fixture(3, baseline="1", candidate="0.123456789", unpaired=2)
    before = deepcopy((dataset, manifest))
    expected = compare_generations(dataset, manifest)
    assert (dataset, manifest) == before
    dataset["cost_items"].reverse()
    manifest["pairs"].reverse()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert compare_generations(dataset, manifest) == expected
    normalized = normalize_generation_manifest(manifest)
    assert [pair["pair_id"] for pair in normalized["pairs"]] == sorted(pair["pair_id"] for pair in manifest["pairs"])
    assert digest(normalized) == digest(normalize_generation_manifest(before[1]))
    normalized["pairs"][0]["pair_id"] = "detached"
    assert manifest["pairs"][-1]["pair_id"] == "pair-0000"


@pytest.mark.parametrize("change,error", [
    ({"schema_version": "wrong"}, "SCHEMA_INVALID"),
    ({"schema_version": 1}, "SCHEMA_INVALID"),
    ({"unexpected": True}, "SCHEMA_INVALID"),
    ({"comparison_id": ""}, "IDENTIFIER_INVALID"),
    ({"comparison_id": "x" * 201}, "IDENTIFIER_INVALID"),
    ({"comparison_id": "../comparison"}, "IDENTIFIER_INVALID"),
    ({"comparison_id": "api_key-fixture"}, "IDENTIFIER_INVALID"),
    ({"estimate_rate_version": "https://example.invalid"}, "IDENTIFIER_INVALID"),
    ({"pairs": []}, "PAIRS_INVALID"),
    ({"pairs": {}}, "PAIRS_INVALID"),
])
def test_strict_manifest_schema_and_shared_safe_identifiers(change, error):
    dataset, manifest = fixture()
    manifest.update(change)
    with pytest.raises(ValueError, match=f"^COST_GENERATION_{error}$"):
        compare_generations(dataset, manifest)


@pytest.mark.parametrize("manifest", [None, [], "manifest", 1])
def test_non_object_manifest_is_rejected(manifest):
    with pytest.raises(ValueError, match="^COST_GENERATION_SCHEMA_INVALID$"):
        normalize_generation_manifest(manifest)


@pytest.mark.parametrize("mutation,error", [
    ("duplicate-pair", "PAIR_DUPLICATE"),
    ("same-pair-cost", "COST_DUPLICATE"),
    ("same-arm-cost", "COST_DUPLICATE"),
    ("cross-arm-cost", "COST_DUPLICATE"),
    ("unknown-cost", "COST_UNKNOWN"),
    ("pair-extra", "PAIR_INVALID"),
    ("pair-missing", "PAIR_INVALID"),
    ("pair-not-object", "PAIR_INVALID"),
    ("unsafe-pair-id", "IDENTIFIER_INVALID"),
    ("unsafe-cost-id", "IDENTIFIER_INVALID"),
])
def test_pair_identity_and_disjoint_arm_validation(mutation, error):
    dataset, manifest = fixture(2)
    first, second = manifest["pairs"]
    if mutation == "duplicate-pair":
        second["pair_id"] = first["pair_id"]
    elif mutation == "same-pair-cost":
        first["candidate_cost_id"] = first["baseline_cost_id"]
    elif mutation == "same-arm-cost":
        second["baseline_cost_id"] = first["baseline_cost_id"]
    elif mutation == "cross-arm-cost":
        second["baseline_cost_id"] = first["candidate_cost_id"]
    elif mutation == "unknown-cost":
        second["candidate_cost_id"] = "missing-cost"
    elif mutation == "pair-extra":
        first["extra"] = True
    elif mutation == "pair-missing":
        del first["pair_id"]
    elif mutation == "pair-not-object":
        manifest["pairs"][0] = []
    elif mutation == "unsafe-pair-id":
        first["pair_id"] = "../pair"
    else:
        first["baseline_cost_id"] = "credential-value"
    with pytest.raises(ValueError, match=f"^COST_GENERATION_{error}$"):
        compare_generations(dataset, manifest)


def test_no_actual_cost_fallback_or_category_reclassification():
    dataset, manifest = fixture()
    dataset["cost_items"][0].update(estimated_cost_usd=None, rate_version=None,
                                   actual_cost_usd="7", billing_evidence_ref="fixture:bill")
    report = compare_generations(dataset, manifest)
    assert report["compared_pair_count"] == 0
    assert exclusions(report)["baseline-0"] == ["COST_MISSING"]
    assert dataset["cost_items"][0]["category"] == "research"


def test_empty_dataset_rejects_unknown_pair_ids_instead_of_inventing_costs():
    dataset, manifest = fixture()
    dataset["cost_items"].clear()
    with pytest.raises(ValueError, match="^COST_GENERATION_COST_UNKNOWN$"):
        compare_generations(dataset, manifest)


def test_calculation_record_accesses_grow_linearly_with_records_and_pairs(monkeypatch):
    dataset, manifest = fixture(300, unpaired=200)
    accesses = 0

    class CountedRecord(dict):
        def __getitem__(self, key):
            nonlocal accesses
            accesses += 1
            return super().__getitem__(key)

    records = [CountedRecord(record) for record in dataset["cost_items"]]
    monkeypatch.setattr(generation_costs, "normalize_dataset", lambda value: value)
    monkeypatch.setattr(generation_costs, "latest_records", lambda items, identity: records)
    report = compare_generations(dataset, manifest)
    assert report["compared_pair_count"] == 300
    assert accesses <= 10 * len(records) + 2 * len(manifest["pairs"])


def test_comparison_has_no_files_network_or_credentials(monkeypatch):
    import builtins
    import os
    from pathlib import Path
    import socket

    dataset, manifest = fixture()

    def forbidden(*args, **kwargs):
        raise AssertionError("generation comparison attempted external access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert compare_generations(dataset, manifest)["estimated_cost_reduction_pct"] == "50.000000000"
