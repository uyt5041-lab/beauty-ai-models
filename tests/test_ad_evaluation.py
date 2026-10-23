"""Explicit fictional review records test arithmetic, never real image quality."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from beauty_ai.ad_evaluation import (
    COVERAGE_TAGS, DIMENSIONS, FATAL_FLAGS, ImageQualityPolicy, ImageReviewRecord,
    RealProductManifest, evaluate_dataset, evaluate_finops, evaluate_release,
    evaluate_service, score_image_case,
)

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = "a" * 64


def review(reviewer="reviewer-a", rating=4):
    return {"reviewer_id": reviewer, "reviewer_type": "human",
            "scores": dict.fromkeys(DIMENSIONS, rating),
            "fatal_flags": dict.fromkeys(FATAL_FLAGS, False), "not_applicable": []}


def case(**changes):
    value = {"case_id": "case-1", "evaluation_cycle_id": "test-cycle-1",
        "product_id": "product-1", "split": "dev", "condition": "baseline", "origin": "real",
        "rights_confirmed": True, "source_photo_sha256": ["1" * 64, "2" * 64],
        "reference_photo_sha256": [], "output_sha256": "3" * 64,
        "output_width": 1152, "output_height": 1440,
        "request_sha256": "4" * 64, "settings_sha256": SETTINGS, "label_expected": True,
        "reviews": [review(), review("reviewer-b")], "consensus": None}
    value.update(changes)
    return value


def set_both(value, dimension, score):
    for r in value["reviews"]:
        r["scores"][dimension] = score


def dataset():
    products, cases = [], []
    for i in range(10):
        split = "dev" if i < 5 else "holdout"
        tags = dict.fromkeys(COVERAGE_TAGS, False)
        tags["printed_label"] = True
        for tag in ({0: ("transparent", "pump"), 1: ("reflective", "jar"),
                     2: ("tube",), 3: ("pump",), 4: ("jar",)}[i % 5]):
            tags[tag] = True
        product = {"slot_id": f"slot-{i}", "product_id": f"product-{i}", "split": split,
            "collection_status": "COLLECTED", "origin": "real", "rights_confirmed": True,
            "rights_evidence": "Fictional unit-test declaration; no actual photo exists",
            "source_photo_sha256": [f"{i * 10 + 1:064x}", f"{i * 10 + 2:064x}"], "coverage": tags}
        products.append(product)
        for condition in ("baseline", "reference"):
            cases.append(case(case_id=f"case-{i}-{condition}", product_id=product["product_id"],
                split=split, condition=condition, source_photo_sha256=product["source_photo_sha256"],
                reference_photo_sha256=[] if condition == "baseline" else
                    [f"{1000 + i:064x}"] + ([f"{2000 + i:064x}"] if i == 9 else []),
                output_sha256=f"{10000 + len(cases):064x}", request_sha256=f"{20000 + len(cases):064x}"))
    manifest = {"dataset_id": "fictional-test-records", "evaluation_cycle_id": "test-cycle-1",
        "version": "image-eval-v0.1", "state": "COLLECTED", "products": products,
        "holdout_settings_sha256": SETTINGS, "holdout_frozen_before_review": True}
    return manifest, cases


def finops(**changes):
    value = {"evaluation_cycle_id": "test-cycle-1", "candidate_settings_sha256": SETTINGS,
        "accounting_window_id": "test-window-1", "accepted_final_ads": 20,
        "production_total_confirmed_usd": 10.0, "production_total_estimated_usd": 10.0,
        "avoidable_failure_retry_confirmed_usd": 1.0,
        "production_team_api_confirmed_usd": 8.0, "research_team_api_confirmed_usd": 1.0,
        "research_confirmed_usd": 1.0, "research_estimated_usd": 1.0,
        "team_key_confirmed": True, "team_budget_usd": 30,
        "team_spend_confirmed_usd": 12.0, "team_remaining_confirmed_usd": 18.0,
        "balance_checked_at": "2026-10-23T12:00:00+09:00",
        "evaluation_as_of": "2026-10-23T12:01:00+09:00",
        "budget_controls_verified": True, "billing_lag_hours": 24,
        "eligible_receipts": 20, "matched_receipts": 20, "pending_within_billing_lag": 0,
        "cost_traceability_complete": True}
    value.update(changes)
    return value


def service(**changes):
    value = {"evaluation_cycle_id": "test-cycle-1", "candidate_settings_sha256": SETTINGS,
        "e2e_cases": [{"case_id": f"e2e-{i}", "success": i < 18,
                       "machine_seconds": 90.0, "duration_censored": False} for i in range(20)],
        "novice_users": [{"participant_id": f"user-{i}", "completed_without_help": i < 4}
                         for i in range(5)],
        "text_owner_accepted": True, "renderer_owner_accepted": True}
    value.update(changes)
    return value


def test_all_fours_exactly_eighty_and_no_automatic_approval():
    result = score_image_case(case())
    assert result["score_100"] == 80
    assert result["status"] == "PASS_PROVISIONAL"
    assert result["assessed_weight"] == 100
    assert result["asset_approval"] == result["service_release_approval"] == "NOT_GRANTED"
    assert result["reviewer_evidence"] == "DECLARED_NOT_AUTHENTICATED"
    assert result["model_invoked"] is False


def test_weighted_score_below_eighty_fails_without_rounding_up():
    value = case()
    set_both(value, "reference_concept", 3)
    result = score_image_case(value)
    assert result["score_100"] == 77
    assert result["status"] == "FAIL"


@pytest.mark.parametrize("dimension", ["shape", "label_logo", "color_material"])
def test_high_average_cannot_offset_preservation_failure(dimension):
    value = case(reviews=[review(rating=5), review("reviewer-b", 5)])
    set_both(value, dimension, 3)
    result = score_image_case(value)
    assert result["score_100"] >= 90
    assert result["status"] == "FAIL"
    assert "PRESERVATION_BELOW_MINIMUM" in result["reason_codes"]


@pytest.mark.parametrize("flag", FATAL_FLAGS)
def test_any_agreed_fatal_error_overrides_one_hundred(flag):
    value = case(reviews=[review(rating=5), review("reviewer-b", 5)])
    for r in value["reviews"]:
        r["fatal_flags"][flag] = True
    result = score_image_case(value)
    assert result["score_100"] == 100 and result["status"] == "FAIL"


@pytest.mark.parametrize("field", ["rights_confirmed", "output_sha256", "output_width", "output_height",
    "request_sha256", "settings_sha256", "label_expected", "evaluation_cycle_id"])
def test_unknown_evidence_blocks_score_instead_of_zero(field):
    result = score_image_case(case(**{field: None}))
    assert result["score_100"] is None and result["status"] == "BLOCKED"


def test_null_rating_and_fatal_checks_are_not_passes():
    for group, name in (("scores", "shape"), ("fatal_flags", FATAL_FLAGS[0])):
        value = case()
        for r in value["reviews"]:
            r[group][name] = None
        assert score_image_case(value)["status"] == "BLOCKED"


def test_wrong_ratio_is_hard_failure_even_at_score_one_hundred():
    result = score_image_case(case(output_height=1152,
        reviews=[review(rating=5), review("reviewer-b", 5)]))
    assert result["score_100"] == 100 and result["status"] == "FAIL"
    assert "OUTPUT_RATIO_NOT_4_5" in result["reason_codes"]


@pytest.mark.parametrize("photos,eligible", [(1, False), (2, True), (5, True)])
def test_input_photo_boundaries(photos, eligible):
    result = score_image_case(case(source_photo_sha256=[f"{i:064x}" for i in range(photos)]))
    assert (result["status"] == "PASS_PROVISIONAL") is eligible


@pytest.mark.parametrize("rating", [True, 4.0, "4", 0, 6])
def test_ratings_are_strict_one_to_five_integers(rating):
    value = case()
    value["reviews"][0]["scores"]["shape"] = rating
    with pytest.raises(ValidationError):
        score_image_case(value)


def test_two_distinct_reviewers_and_unresolved_disagreement():
    value = case()
    assert score_image_case({**value, "reviews": value["reviews"][:1]})["status"] == "BLOCKED"
    with pytest.raises(ValidationError):
        score_image_case({**value, "reviews": [value["reviews"][0]] * 2})
    value["reviews"][1]["scores"]["shape"] = 5
    assert score_image_case(value)["score_100"] is None
    assert score_image_case(value)["reason_codes"] == ["REVIEW_DISAGREEMENT_UNRESOLVED"]


def test_consensus_requires_both_reviewer_endorsements_and_reason():
    value = case()
    value["reviews"][1]["scores"]["shape"] = 5
    value["consensus"] = {k: value["reviews"][0][k] for k in ("scores", "fatal_flags", "not_applicable")}
    value["consensus"].update(endorsed_by=["reviewer-a", "reviewer-c"],
        resolution_note="Both compared the same reference again and agreed on four.")
    assert score_image_case(value)["status"] == "BLOCKED"
    value["consensus"]["endorsed_by"] = ["reviewer-a", "reviewer-b"]
    assert score_image_case(value)["score_100"] == 80
    value["consensus"]["resolution_note"] = " " * 20
    with pytest.raises(ValidationError):
        score_image_case(value)


def test_unlabeled_na_is_diagnostic_not_five_dimension_pass():
    assert score_image_case(case(label_expected=False))["status"] == "BLOCKED"
    value = case(label_expected=False)
    for r in value["reviews"]:
        r["scores"]["label_logo"] = None
        r["not_applicable"] = ["label_logo"]
    result = score_image_case(value)
    assert result["status"] == "DIAGNOSTIC_ONLY" and result["score_100"] == 80
    assert result["assessed_weight"] == 75 and result["label_logo_assessed"] is False
    assert score_image_case({**value, "label_expected": True})["status"] == "BLOCKED"
    assert score_image_case({**value, "label_expected": None})["status"] == "BLOCKED"


def test_synthetic_product_never_counts_as_real_holdout():
    result = score_image_case(case(origin="synthetic", split="holdout"))
    assert result["status"] == "SMOKE_ONLY" and result["real_evaluation_eligible"] is False


@pytest.mark.parametrize("change", [{"pass_score": 0}, {"preservation_minimum": 1},
    {"weights": {"shape": 100}}, {"weights": {"shape": 24, "label_logo": 26,
     "color_material": 20, "reference_concept": 15, "composition_copyspace": 15}}])
def test_published_v01_policy_cannot_be_weakened(change):
    with pytest.raises(ValidationError):
        ImageQualityPolicy(**change)


def test_complete_declared_dataset_passes_provisional_gate_only():
    manifest, records = dataset()
    result = evaluate_dataset(manifest, records)
    assert result["status"] == "PROVISIONAL_IMAGE_CRITERIA_MET"
    assert result["eligible_real_products"] == 10 and len(result["cases"]) == 20
    assert result["service_release_approval"] == "NOT_GRANTED"


def test_empty_real_template_and_review_example_are_blocked():
    manifest = json.loads((ROOT / "examples/evaluation/real-product-manifest.template.json").read_text())
    plan = json.loads((ROOT / "examples/evaluation/image-eval-v0.1.json").read_text())
    assert len(manifest["products"]) == 10
    assert all(p["product_id"] is None and p["collection_status"] == "NOT_COLLECTED" for p in manifest["products"])
    result = evaluate_dataset(manifest, [])
    assert result["status"] == "BLOCKED" and result["eligible_real_products"] == 0
    assert score_image_case(plan["review_record_example"]["record"])["score_100"] is None
    assert ImageQualityPolicy.model_validate(plan["image_quality"]).model_dump() == ImageQualityPolicy().model_dump()


def test_dataset_rejects_same_product_across_splits_and_photo_leakage():
    manifest, records = dataset()
    manifest["products"][5]["product_id"] = manifest["products"][0]["product_id"]
    with pytest.raises(ValidationError):
        evaluate_dataset(manifest, records)
    manifest, records = dataset()
    manifest["products"][5]["source_photo_sha256"][0] = manifest["products"][0]["source_photo_sha256"][0]
    assert "SOURCE_PHOTO_REUSED_ACROSS_PRODUCTS" in evaluate_dataset(manifest, records)["reason_codes"]


@pytest.mark.parametrize("change,reason", [
    ("missing_case", "TWENTY_PAIRED_CASES_REQUIRED"), ("duplicate", "DUPLICATE_PRODUCT_CONDITION"),
    ("synthetic", "REVIEW_PRODUCT_EVIDENCE_MISMATCH"), ("settings", "CANDIDATE_SETTINGS_MISMATCH"),
    ("cycle", "EVALUATION_CYCLE_MISMATCH"), ("reference", "CASE_NOT_PASSED")])
def test_dataset_requires_paired_bound_same_candidate_evidence(change, reason):
    manifest, records = dataset()
    if change == "missing_case": records.pop()
    elif change == "duplicate": records[-1] = deepcopy(records[0])
    elif change == "synthetic": records[0]["origin"] = "synthetic"
    elif change == "settings": records[0]["settings_sha256"] = "b" * 64
    elif change == "cycle": records[0]["evaluation_cycle_id"] = "other-cycle"
    elif change == "reference": records[1]["reference_photo_sha256"] = []
    assert reason in evaluate_dataset(manifest, records)["reason_codes"]


def test_unlabeled_product_allowed_but_all_na_cannot_supply_label_coverage():
    manifest, records = dataset()
    for p in manifest["products"][:1]: p["coverage"]["printed_label"] = False
    for record in records[:2]:
        record["label_expected"] = False
        for r in record["reviews"]:
            r["scores"]["label_logo"] = None
            r["not_applicable"] = ["label_logo"]
    assert evaluate_dataset(manifest, records)["status"] == "PROVISIONAL_IMAGE_CRITERIA_MET"
    for p in manifest["products"]: p["coverage"]["printed_label"] = False
    for record in records:
        record["label_expected"] = False
        for r in record["reviews"]:
            r["scores"]["label_logo"] = None
            r["not_applicable"] = ["label_logo"]
    result = evaluate_dataset(manifest, records)
    assert result["status"] == "BLOCKED"
    assert "HOLDOUT_PRINTED_LABEL_REVIEWS_REQUIRED" in result["reason_codes"]


def test_reference_coverage_includes_single_and_multiple_images():
    manifest, records = dataset()
    for record in records:
        record["reference_photo_sha256"] = record["reference_photo_sha256"][:1]
    assert "REFERENCE_COUNT_COVERAGE_INCOMPLETE" in evaluate_dataset(manifest, records)["reason_codes"]


def test_finops_exact_thresholds_and_explicit_estimate_actual_separation():
    result = evaluate_finops(finops(production_total_estimated_usd=12.0))
    assert result["status"] == "PASS_PROVISIONAL" and result["score_100"] == 100
    assert result["unit_cost_confirmed_usd"] == .5 and result["unit_cost_estimated_usd"] == .6
    assert result["avoidable_waste_rate"] == .1 and result["estimate_relative_error"] == .2
    assert result["new_paid_calls_allowed_by_this_report"] is False


def test_finops_decimal_money_preserves_exact_ten_and_twenty_percent_boundaries():
    value = finops(accepted_final_ads=2, production_total_confirmed_usd=.7,
        production_total_estimated_usd=.84, production_team_api_confirmed_usd=.6,
        avoidable_failure_retry_confirmed_usd=.07)
    result = evaluate_finops(value)
    assert result["avoidable_waste_rate"] == .1 and result["estimate_relative_error"] == .2
    assert result["status"] == "PASS_PROVISIONAL"
    assert evaluate_finops({**value, "avoidable_failure_retry_confirmed_usd": .070001})["status"] == "FAIL"
    assert evaluate_finops({**value, "production_total_estimated_usd": .840001})["status"] == "FAIL"


@pytest.mark.parametrize("as_of,expected", [("2026-10-23T12:15:00+09:00", "PASS_PROVISIONAL"),
    ("2026-10-23T12:15:01+09:00", "BLOCKED"), ("2026-10-23T11:59:59+09:00", "BLOCKED")])
def test_finops_balance_must_be_no_more_than_fifteen_minutes_old_at_evaluation(as_of, expected):
    result = evaluate_finops(finops(evaluation_as_of=as_of))
    assert result["status"] == expected
    if expected == "BLOCKED": assert result["score_100"] is None


@pytest.mark.parametrize("field", ["balance_checked_at", "evaluation_as_of"])
def test_finops_balance_timestamp_is_required_and_timezone_aware(field):
    assert evaluate_finops(finops(**{field: None}))["status"] == "BLOCKED"
    with pytest.raises(ValidationError):
        evaluate_finops(finops(**{field: "2026-10-23T12:00:00.000000"}))


@pytest.mark.parametrize("spend,state,status", [(23.99, "WITHIN_LIMIT", "PASS_PROVISIONAL"),
    (24., "WARNING", "PASS_PROVISIONAL"), (26.99, "WARNING", "PASS_PROVISIONAL"),
    (27., "BLOCK_NEW_PAID_CALLS", "FAIL"), (30., "BLOCK_NEW_PAID_CALLS", "FAIL")])
def test_finops_budget_warning_and_block_boundaries(spend, state, status):
    result = evaluate_finops(finops(team_spend_confirmed_usd=spend, team_remaining_confirmed_usd=30-spend))
    assert result["budget_state"] == state and result["status"] == status


@pytest.mark.parametrize("field", ["team_key_confirmed", "team_remaining_confirmed_usd",
    "production_total_confirmed_usd", "matched_receipts", "budget_controls_verified",
    "production_team_api_confirmed_usd", "accounting_window_id"])
def test_finops_unknown_is_null_not_free_or_pass(field):
    result = evaluate_finops(finops(**{field: None}))
    assert result["status"] == "BLOCKED" and result["score_100"] is None
    assert result["unit_cost_confirmed_usd"] is None


@pytest.mark.parametrize("change", [{"production_total_confirmed_usd": 10.01},
    {"avoidable_failure_retry_confirmed_usd": 1.01}, {"matched_receipts": 19},
    {"cost_traceability_complete": False}, {"production_total_estimated_usd": 12.01},
    {"team_key_confirmed": False}, {"budget_controls_verified": False}])
def test_finops_each_known_failed_gate_blocks(change):
    result = evaluate_finops(finops(**change))
    assert result["status"] == "FAIL" and result["score_100"] < 100


def test_finops_lag_and_zero_denominators_remain_blocked():
    for change in ({"pending_within_billing_lag": 1}, {"accepted_final_ads": 0},
                   {"eligible_receipts": 0, "matched_receipts": 0}):
        result = evaluate_finops(finops(**change))
        assert result["status"] == "BLOCKED" and result["score_100"] is None


def test_confirmed_zero_cost_is_not_inferred_zero_cost():
    zero = finops(production_total_confirmed_usd=0., production_total_estimated_usd=0.,
        production_team_api_confirmed_usd=0., avoidable_failure_retry_confirmed_usd=0.)
    assert evaluate_finops(zero)["status"] == "PASS_PROVISIONAL"
    assert evaluate_finops({**zero, "production_total_estimated_usd": 1.})["status"] == "FAIL"
    assert evaluate_finops({**zero, "production_total_confirmed_usd": None})["score_100"] is None


@pytest.mark.parametrize("change", [{"production_total_confirmed_usd": float("nan")},
    {"production_total_confirmed_usd": float("inf")}, {"production_total_confirmed_usd": -1.},
    {"avoidable_failure_retry_confirmed_usd": 11.}, {"matched_receipts": 21},
    {"team_remaining_confirmed_usd": 29.}, {"production_team_api_confirmed_usd": 11.},
    {"team_spend_confirmed_usd": 0., "team_remaining_confirmed_usd": 30.}])
def test_finops_rejects_invalid_or_incoherent_numbers(change):
    with pytest.raises(ValidationError): evaluate_finops(finops(**change))


def test_e2e_success_latency_and_usability_boundaries():
    value = service()
    result = evaluate_service(value)
    assert result["success_rate"] == .9 and result["machine_p95_seconds"] == 90
    assert result["novice_completions"] == 4 and result["status"] == "PASS_PROVISIONAL"
    value["e2e_cases"][17]["success"] = False
    assert evaluate_service(value)["status"] == "FAIL"
    value = service()
    value["novice_users"][3]["completed_without_help"] = False
    assert evaluate_service(value)["status"] == "FAIL"


def test_p95_includes_terminal_failures_and_never_hides_censoring():
    value = service()
    value["e2e_cases"][-1]["machine_seconds"] = 1000.
    assert evaluate_service(value)["machine_p95_seconds"] == 90
    value["e2e_cases"][-2]["machine_seconds"] = 91.
    assert evaluate_service(value)["machine_p95_seconds"] == 91
    assert evaluate_service(value)["status"] == "FAIL"
    value["e2e_cases"][-2]["duration_censored"] = True
    result = evaluate_service(value)
    assert result["status"] == "BLOCKED" and result["machine_p95_seconds"] is None


def test_e2e_unknowns_missing_samples_and_owner_signoffs():
    value = service()
    value["e2e_cases"][0]["success"] = None
    assert evaluate_service(value)["score_100"] is None
    value = service()
    value["e2e_cases"].pop()
    assert evaluate_service(value)["status"] == "BLOCKED"
    assert evaluate_service(service(text_owner_accepted=None))["status"] == "BLOCKED"
    assert evaluate_service(service(renderer_owner_accepted=False))["status"] == "FAIL"


def test_overall_finops_has_forty_percent_and_unknown_never_becomes_zero():
    manifest, records = dataset()
    result = evaluate_release(manifest, records, finops=finops(), service=service())
    assert result["weights"] == {"image_quality": 40, "finops": 40, "service": 20}
    assert result["score_100"] == 92
    assert result["status"] == "PROVISIONAL_GATES_MET_AWAITING_TEAM_SIGNOFF"
    assert result["service_release_approval"] == "NOT_GRANTED"
    blocked = evaluate_release(manifest, records, finops=finops(team_key_confirmed=None), service=service())
    assert blocked["score_100"] is None and blocked["status"] == "BLOCKED"


def test_cheap_cost_never_offsets_fatal_quality_or_different_candidate():
    manifest, records = dataset()
    for r in records[0]["reviews"]: r["fatal_flags"]["product_identity_changed"] = True
    result = evaluate_release(manifest, records, finops=finops(), service=service())
    assert result["score_100"] == 92 and result["status"] == "BLOCKED"
    manifest, records = dataset()
    for changed in (service(evaluation_cycle_id="another-cycle"),
                    service(candidate_settings_sha256="b" * 64)):
        result = evaluate_release(manifest, records, finops=finops(), service=changed)
        assert result["score_100"] is None and result["status"] == "BLOCKED"
        assert result["same_evaluation_cycle_and_candidate"] is False


def test_cli_scores_without_network_or_model_calls(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(json.dumps(case()))
    completed = subprocess.run([sys.executable, "-m", "beauty_ai.ad_evaluation", str(path)],
        cwd=ROOT, capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout)
    assert result["score_100"] == 80 and result["model_invoked"] is False


def test_cli_rejects_duplicate_keys_without_echoing_record(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"case_id":"secret-first","case_id":"secret-second"}')
    completed = subprocess.run([sys.executable, "-m", "beauty_ai.ad_evaluation", str(path)],
        cwd=ROOT, capture_output=True, text=True)
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["status"] == "INVALID_REVIEW_RECORD"
    assert "secret" not in completed.stdout + completed.stderr
