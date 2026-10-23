"""Offline scoring of declared human reviews, never image/model judgement.

Criteria are provisional. A computed pass does not authenticate reviewers,
verify image rights, approve an asset, or release the team's service.
"""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from .contracts import Contract, content_hash

DIMENSIONS = ("shape", "label_logo", "color_material", "reference_concept",
              "composition_copyspace")
PRESERVATION = ("shape", "label_logo", "color_material")
FATAL_FLAGS = ("product_identity_changed", "critical_part_missing",
               "required_label_logo_changed", "unrequested_product_or_ad_text")
COVERAGE_TAGS = ("transparent", "reflective", "printed_label", "tube", "pump", "jar")
DEFAULT_WEIGHTS = dict(zip(DIMENSIONS, (25, 25, 20, 15, 15)))
Rating = Annotated[int, Field(ge=1, le=5, strict=True)]
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class StrictRecord(Contract):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImageQualityPolicy(StrictRecord):
    version: Literal["image-eval-v0.1"] = "image-eval-v0.1"
    weights: dict[str, Annotated[int, Field(ge=1, le=100, strict=True)]] = Field(
        default_factory=lambda: dict(DEFAULT_WEIGHTS))
    pass_score: Annotated[int, Field(ge=0, le=100, strict=True)] = 80
    preservation_minimum: Rating = 4
    required_reviewers: Literal[2] = 2

    @model_validator(mode="after")
    def valid_weights(self):
        if set(self.weights) != set(DIMENSIONS) or sum(self.weights.values()) != 100:
            raise ValueError("Exactly five dimensions with total weight 100 are required")
        if (self.weights != DEFAULT_WEIGHTS or self.pass_score != 80
                or self.preservation_minimum != 4):
            raise ValueError("Published v0.1 weights and thresholds are immutable")
        return self


class ReviewJudgement(StrictRecord):
    scores: dict[str, Rating | None]
    fatal_flags: dict[str, bool | None]
    not_applicable: list[Literal["label_logo"]] = Field(default_factory=list, max_length=1)

    @model_validator(mode="after")
    def complete_keys(self):
        if set(self.scores) != set(DIMENSIONS) or set(self.fatal_flags) != set(FATAL_FLAGS):
            raise ValueError("All dimension and fatal-check keys must be present")
        if "label_logo" in self.not_applicable and self.scores["label_logo"] is not None:
            raise ValueError("An inapplicable label score must be null")
        return self


class HumanImageReview(ReviewJudgement):
    reviewer_id: Identifier
    reviewer_type: Literal["human"] = "human"


class ReviewConsensus(ReviewJudgement):
    endorsed_by: list[Identifier] = Field(min_length=2, max_length=2)
    resolution_note: str = Field(min_length=10, max_length=2000)

    @model_validator(mode="after")
    def different_endorsers(self):
        if len(set(self.endorsed_by)) != 2 or len(self.resolution_note.strip()) < 10:
            raise ValueError("Two distinct reviewers must endorse the resolution")
        return self


class ImageReviewRecord(StrictRecord):
    case_id: Identifier
    evaluation_cycle_id: Identifier | None = None
    product_id: Identifier
    split: Literal["dev", "holdout", "smoke"]
    condition: Literal["baseline", "reference"]
    origin: Literal["real", "synthetic"]
    rights_confirmed: bool | None = None
    source_photo_sha256: list[Digest] = Field(default_factory=list, max_length=5)
    reference_photo_sha256: list[Digest] = Field(default_factory=list, max_length=5)
    output_sha256: Digest | None = None
    output_width: Annotated[int, Field(ge=1, strict=True)] | None = None
    output_height: Annotated[int, Field(ge=1, strict=True)] | None = None
    request_sha256: Digest | None = None
    settings_sha256: Digest | None = None
    label_expected: bool | None = None
    reviews: list[HumanImageReview] = Field(default_factory=list, max_length=2)
    consensus: ReviewConsensus | None = None

    @model_validator(mode="after")
    def unique_evidence(self):
        if len({r.reviewer_id for r in self.reviews}) != len(self.reviews):
            raise ValueError("A reviewer cannot count twice")
        if len(set(self.source_photo_sha256)) != len(self.source_photo_sha256):
            raise ValueError("Source photos must be distinct")
        if len(set(self.reference_photo_sha256)) != len(self.reference_photo_sha256):
            raise ValueError("Reference photos must be distinct")
        return self


def _validate(model, value):
    # Revalidate copies/construction bypasses and isolate caller mutation.
    return model.model_validate(value.model_dump() if isinstance(value, model) else value)


def score_image_case(record: ImageReviewRecord | Mapping,
                     policy: ImageQualityPolicy | Mapping | None = None) -> dict:
    """Calculate a provisional gate from explicit human records only."""
    case = _validate(ImageReviewRecord, record)
    rules = _validate(ImageQualityPolicy, policy) if policy is not None else ImageQualityPolicy()
    result = {"case_id": case.case_id, "product_id": case.product_id,
        "status": "BLOCKED", "score_100": None, "assessed_weight": 0,
        "reason_codes": [], "policy_version": rules.version,
        "policy_sha256": content_hash(rules), "review_record_sha256": content_hash(case),
        "reviewer_evidence": "DECLARED_NOT_AUTHENTICATED",
        "asset_approval": "NOT_GRANTED", "service_release_approval": "NOT_GRANTED",
        "real_evaluation_eligible": False, "applicable_dimensions_pass": False,
        "label_logo_assessed": False, "model_invoked": False}
    reasons = result["reason_codes"]
    if case.evaluation_cycle_id is None:
        reasons.append("EVALUATION_CYCLE_UNKNOWN")
    if case.rights_confirmed is not True:
        reasons.append("RIGHTS_NOT_CONFIRMED")
    if case.output_sha256 is None:
        reasons.append("OUTPUT_NOT_RECORDED")
    if case.output_width is None or case.output_height is None:
        reasons.append("OUTPUT_DIMENSIONS_UNKNOWN")
    if case.request_sha256 is None or case.settings_sha256 is None:
        reasons.append("RUN_CONFIGURATION_NOT_RECORDED")
    if (case.condition == "baseline" and case.reference_photo_sha256
            or case.condition == "reference" and not case.reference_photo_sha256):
        reasons.append("REFERENCE_CONDITION_MISMATCH")
    if case.origin == "real" and not 2 <= len(case.source_photo_sha256) <= 5:
        reasons.append("REAL_PRODUCT_REQUIRES_2_TO_5_PHOTOS")
    if case.origin == "real" and case.split == "smoke":
        reasons.append("REAL_PRODUCT_SPLIT_NOT_ASSIGNED")
    if len(case.reviews) != rules.required_reviewers:
        reasons.append("TWO_HUMAN_REVIEWS_REQUIRED")
        return result
    left, right = case.reviews
    comparable = lambda review: {k: review.model_dump()[k]
                                 for k in ("scores", "fatal_flags", "not_applicable")}
    if case.consensus is not None:
        if set(case.consensus.endorsed_by) != {left.reviewer_id, right.reviewer_id}:
            reasons.append("CONSENSUS_ENDORSEMENTS_MISMATCH")
            return result
        judgement = case.consensus
    elif comparable(left) != comparable(right):
        reasons.append("REVIEW_DISAGREEMENT_UNRESOLVED")
        return result
    else:
        judgement = left
    excluded = set(judgement.not_applicable)
    if "label_logo" in excluded and case.label_expected is not False:
        reasons.append("LABEL_NA_NOT_ESTABLISHED")
    if case.label_expected is False and "label_logo" not in excluded:
        reasons.append("UNLABELED_PRODUCT_REQUIRES_NA")
    if case.label_expected is None:
        reasons.append("LABEL_PRESENCE_UNKNOWN")
    if any(score is None for name, score in judgement.scores.items() if name not in excluded):
        reasons.append("RATING_UNKNOWN")
    if any(flag is None for flag in judgement.fatal_flags.values()):
        reasons.append("FATAL_CHECK_UNKNOWN")
    if reasons:
        return result
    assessed = sum(weight for name, weight in rules.weights.items() if name not in excluded)
    # N/A score is explicitly diagnostic and normalized over assessed weight.
    numerator = sum(rules.weights[name] * score / 5
                    for name, score in judgement.scores.items() if name not in excluded)
    result.update(score_100=round(numerator * 100 / assessed, 4), assessed_weight=assessed,
                  label_logo_assessed="label_logo" not in excluded)
    failed = ["FATAL_" + name.upper() for name, value in judgement.fatal_flags.items() if value]
    if case.output_width * 5 != case.output_height * 4:
        failed.append("OUTPUT_RATIO_NOT_4_5")
    if result["score_100"] < rules.pass_score:
        failed.append("WEIGHTED_SCORE_BELOW_THRESHOLD")
    if any(judgement.scores[name] < rules.preservation_minimum
           for name in PRESERVATION if name not in excluded):
        failed.append("PRESERVATION_BELOW_MINIMUM")
    if failed:
        result.update(status="FAIL", reason_codes=failed)
        return result
    result["applicable_dimensions_pass"] = True
    if case.origin != "real" or case.split == "smoke":
        result.update(status="SMOKE_ONLY", reason_codes=["SYNTHETIC_NOT_REAL_EVALUATION"])
    elif excluded:
        result.update(status="DIAGNOSTIC_ONLY", real_evaluation_eligible=True,
                      reason_codes=["LABEL_NA_REQUIRES_DATASET_LABEL_COVERAGE"])
    else:
        result.update(status="PASS_PROVISIONAL", real_evaluation_eligible=True)
    return result


class ProductSlot(StrictRecord):
    slot_id: Identifier
    product_id: Identifier | None = None
    split: Literal["dev", "holdout"]
    collection_status: Literal["NOT_COLLECTED", "COLLECTED"] = "NOT_COLLECTED"
    origin: Literal["real", "synthetic"] = "real"
    rights_confirmed: bool | None = None
    rights_evidence: str | None = Field(default=None, max_length=1000)
    source_photo_sha256: list[Digest] = Field(default_factory=list, max_length=5)
    coverage: dict[str, bool | None] = Field(
        default_factory=lambda: {tag: None for tag in COVERAGE_TAGS})

    @model_validator(mode="after")
    def valid_coverage(self):
        if set(self.coverage) != set(COVERAGE_TAGS):
            raise ValueError("All coverage tags must be recorded")
        if len(set(self.source_photo_sha256)) != len(self.source_photo_sha256):
            raise ValueError("Source photos must be distinct")
        return self


class RealProductManifest(StrictRecord):
    dataset_id: Identifier
    evaluation_cycle_id: Identifier | None = None
    version: Literal["image-eval-v0.1"] = "image-eval-v0.1"
    state: Literal["DRAFT_NOT_COLLECTED", "COLLECTED"] = "DRAFT_NOT_COLLECTED"
    products: list[ProductSlot] = Field(max_length=100)
    holdout_settings_sha256: Digest | None = None
    holdout_frozen_before_review: bool | None = None

    @model_validator(mode="after")
    def unique_slots(self):
        if len({p.slot_id for p in self.products}) != len(self.products):
            raise ValueError("Slot IDs must be unique")
        product_ids = [p.product_id for p in self.products if p.product_id is not None]
        if len(set(product_ids)) != len(product_ids):
            raise ValueError("A real product group must belong to only one split")
        return self


def evaluate_dataset(manifest: RealProductManifest | Mapping,
                     review_records: list[ImageReviewRecord | Mapping],
                     policy: ImageQualityPolicy | Mapping | None = None) -> dict:
    """Check declared dataset coverage and reviews; never opens image files."""
    data = _validate(RealProductManifest, manifest)
    rules = _validate(ImageQualityPolicy, policy) if policy is not None else ImageQualityPolicy()
    records = [_validate(ImageReviewRecord, item) for item in review_records]
    reasons = []
    if data.evaluation_cycle_id is None:
        reasons.append("EVALUATION_CYCLE_UNKNOWN")
    if data.state != "COLLECTED":
        reasons.append("DATASET_NOT_COLLECTED")
    if data.holdout_settings_sha256 is None or data.holdout_frozen_before_review is not True:
        reasons.append("HOLDOUT_SETTINGS_NOT_FROZEN")
    eligible = []
    photo_owners = {}
    for product in data.products:
        if (product.product_id is None or product.collection_status != "COLLECTED"
                or product.origin != "real" or product.rights_confirmed is not True
                or not product.rights_evidence or not product.rights_evidence.strip()
                or not 2 <= len(product.source_photo_sha256) <= 5
                or any(value is None for value in product.coverage.values())):
            reasons.append("PRODUCT_EVIDENCE_INCOMPLETE")
            continue
        eligible.append(product)
        for digest in product.source_photo_sha256:
            owner = photo_owners.setdefault(digest, product.product_id)
            if owner != product.product_id:
                reasons.append("SOURCE_PHOTO_REUSED_ACROSS_PRODUCTS")
    if len(eligible) != 10 or len(data.products) != 10:
        reasons.append("TEN_REAL_PRODUCTS_REQUIRED")
    coverage = {}
    for split in ("dev", "holdout"):
        group = [p for p in eligible if p.split == split]
        coverage[split] = {tag: sum(p.coverage[tag] is True for p in group) for tag in COVERAGE_TAGS}
        if len(group) != 5:
            reasons.append(split.upper() + "_REQUIRES_FIVE_PRODUCTS")
        if any(count < 1 for count in coverage[split].values()):
            reasons.append(split.upper() + "_COVERAGE_INCOMPLETE")
    cases = [score_image_case(record, rules) for record in records]
    if len({record.case_id for record in records}) != len(records):
        reasons.append("DUPLICATE_CASE_ID")
    by_product = {p.product_id: p for p in eligible}
    seen_pairs = set()
    for record, result in zip(records, cases):
        product = by_product.get(record.product_id)
        pair = (record.product_id, record.condition)
        if pair in seen_pairs:
            reasons.append("DUPLICATE_PRODUCT_CONDITION")
        seen_pairs.add(pair)
        if (product is None or record.split != product.split or record.origin != "real"
                or set(record.source_photo_sha256) != set(product.source_photo_sha256)
                or record.label_expected is not product.coverage["printed_label"]):
            reasons.append("REVIEW_PRODUCT_EVIDENCE_MISMATCH")
        if record.evaluation_cycle_id != data.evaluation_cycle_id:
            reasons.append("EVALUATION_CYCLE_MISMATCH")
        if record.settings_sha256 != data.holdout_settings_sha256:
            reasons.append("CANDIDATE_SETTINGS_MISMATCH")
        if not result["real_evaluation_eligible"] or not result["applicable_dimensions_pass"]:
            reasons.append("CASE_NOT_PASSED")
    expected = {(p.product_id, condition) for p in eligible for condition in ("baseline", "reference")}
    if seen_pairs != expected or len(records) != 20:
        reasons.append("TWENTY_PAIRED_CASES_REQUIRED")
    reference_counts = [len(record.reference_photo_sha256) for record in records
                        if record.condition == "reference"]
    if not (1 in reference_counts and any(count >= 2 for count in reference_counts)):
        reasons.append("REFERENCE_COUNT_COVERAGE_INCOMPLETE")
    # An unlabeled product can pass its applicable dimensions, but cannot
    # stand in for the printed-label test in either product-group split.
    for split in ("dev", "holdout"):
        labeled = [p for p in eligible if p.split == split and p.coverage["printed_label"]]
        if not any(all(any(record.product_id == p.product_id and record.condition == condition
                          and result["status"] == "PASS_PROVISIONAL" and result["label_logo_assessed"]
                          for record, result in zip(records, cases))
                       for condition in ("baseline", "reference")) for p in labeled):
            reasons.append(split.upper() + "_PRINTED_LABEL_REVIEWS_REQUIRED")
    return {"dataset_id": data.dataset_id, "policy_version": rules.version,
        "policy_sha256": content_hash(rules), "manifest_sha256": content_hash(data),
        "status": "BLOCKED" if reasons else "PROVISIONAL_IMAGE_CRITERIA_MET",
        "reason_codes": sorted(set(reasons)), "eligible_real_products": len(eligible),
        "expected_real_products": 10, "expected_cases": 20, "coverage": coverage,
        "cases": cases, "evidence_verification": "DECLARED_NOT_AUTHENTICATED",
        "asset_approval": "NOT_GRANTED", "service_release_approval": "NOT_GRANTED",
        "model_invoked": False}


Money = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]


class FinOpsRecord(StrictRecord):
    """Confirmed amounts and estimates have separate fields and denominators."""
    evaluation_cycle_id: Identifier | None = None
    candidate_settings_sha256: Digest | None = None
    accounting_window_id: Identifier | None = None
    accepted_final_ads: Count | None = None
    production_total_confirmed_usd: Money | None = None
    production_total_estimated_usd: Money | None = None
    avoidable_failure_retry_confirmed_usd: Money | None = None
    research_confirmed_usd: Money | None = None
    research_estimated_usd: Money | None = None
    production_team_api_confirmed_usd: Money | None = None
    research_team_api_confirmed_usd: Money | None = None
    team_key_confirmed: bool | None = None
    team_budget_usd: Literal[30] = 30
    team_spend_confirmed_usd: Money | None = None
    team_remaining_confirmed_usd: Money | None = None
    balance_checked_at: str | None = Field(default=None, min_length=20, max_length=40)
    evaluation_as_of: str | None = Field(default=None, min_length=20, max_length=40)
    budget_controls_verified: bool | None = None
    billing_lag_hours: Literal[24] = 24
    eligible_receipts: Count | None = None
    matched_receipts: Count | None = None
    pending_within_billing_lag: Count | None = None
    cost_traceability_complete: bool | None = None

    @model_validator(mode="after")
    def coherent_amounts(self):
        for timestamp in (self.balance_checked_at, self.evaluation_as_of):
            if timestamp is not None:
                parsed = datetime.fromisoformat(timestamp)
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError("FinOps timestamps must include a timezone")
        if (self.production_total_confirmed_usd is not None
                and self.avoidable_failure_retry_confirmed_usd is not None
                and self.avoidable_failure_retry_confirmed_usd > self.production_total_confirmed_usd):
            raise ValueError("Avoidable production cost cannot exceed total production cost")
        if (self.production_total_confirmed_usd is not None and self.production_team_api_confirmed_usd is not None
                and self.production_team_api_confirmed_usd > self.production_total_confirmed_usd):
            raise ValueError("Team API production cost cannot exceed all-provider production cost")
        if (self.research_confirmed_usd is not None and self.research_team_api_confirmed_usd is not None
                and self.research_team_api_confirmed_usd > self.research_confirmed_usd):
            raise ValueError("Team API research cost cannot exceed all-provider research cost")
        if (self.production_team_api_confirmed_usd is not None and self.research_team_api_confirmed_usd is not None
                and self.team_spend_confirmed_usd is not None
                and self.production_team_api_confirmed_usd + self.research_team_api_confirmed_usd
                    > self.team_spend_confirmed_usd + 0.000001):
            raise ValueError("Attributed team API cost cannot exceed confirmed cumulative team spend")
        if (self.eligible_receipts is not None and self.matched_receipts is not None
                and self.matched_receipts > self.eligible_receipts):
            raise ValueError("Matched receipts cannot exceed eligible receipts")
        if (self.team_spend_confirmed_usd is not None and self.team_remaining_confirmed_usd is not None
                and not math.isclose(self.team_spend_confirmed_usd + self.team_remaining_confirmed_usd,
                                     self.team_budget_usd, rel_tol=0, abs_tol=0.000001)):
            raise ValueError("Confirmed team spend and balance must reconcile to the shared budget")
        return self


def evaluate_finops(record: FinOpsRecord | Mapping) -> dict:
    data = _validate(FinOpsRecord, record)
    result = {"status": "BLOCKED", "score_100": None, "reason_codes": [],
        "weights": {"unit_cost": 30, "avoidable_waste": 20, "budget_control": 25, "reconciliation": 25},
        "unit_cost_confirmed_usd": None, "unit_cost_estimated_usd": None,
        "avoidable_waste_rate": None, "team_utilization": None,
        "billing_match_rate": None, "estimate_relative_error": None,
        "budget_state": "UNKNOWN", "new_paid_calls_allowed_by_this_report": False,
        "balance_age_seconds": None,
        "record_sha256": content_hash(data), "evidence_verification": "DECLARED_NOT_AUTHENTICATED",
        "research_confirmed_usd": data.research_confirmed_usd,
        "research_estimated_usd": data.research_estimated_usd}
    if data.accepted_final_ads and data.production_total_estimated_usd is not None:
        result["unit_cost_estimated_usd"] = data.production_total_estimated_usd / data.accepted_final_ads
    required = ("evaluation_cycle_id", "candidate_settings_sha256", "accounting_window_id", "accepted_final_ads", "production_total_confirmed_usd", "production_total_estimated_usd",
        "avoidable_failure_retry_confirmed_usd", "team_key_confirmed", "team_spend_confirmed_usd",
        "team_remaining_confirmed_usd", "budget_controls_verified", "eligible_receipts", "matched_receipts",
        "pending_within_billing_lag", "cost_traceability_complete", "production_team_api_confirmed_usd",
        "research_team_api_confirmed_usd", "balance_checked_at", "evaluation_as_of")
    if any(getattr(data, name) is None for name in required):
        result["reason_codes"].append("FINOPS_EVIDENCE_UNKNOWN")
        return result
    balance_age = (datetime.fromisoformat(data.evaluation_as_of)
                   - datetime.fromisoformat(data.balance_checked_at)).total_seconds()
    result["balance_age_seconds"] = balance_age
    if not 0 <= balance_age <= 15 * 60:
        result["reason_codes"].append("BUDGET_BALANCE_STALE_OR_FUTURE")
        return result
    if data.accepted_final_ads == 0 or data.eligible_receipts == 0:
        result["reason_codes"].append("FINOPS_DENOMINATOR_NOT_ESTABLISHED")
        return result
    if data.pending_within_billing_lag:
        result["reason_codes"].append("BILLING_LAG_PENDING")
        return result
    actual = Decimal(str(data.production_total_confirmed_usd))
    estimated = Decimal(str(data.production_total_estimated_usd))
    result["unit_cost_confirmed_usd"] = float(actual / data.accepted_final_ads)
    # Explicitly confirmed zero spend is different from missing billing.
    result["avoidable_waste_rate"] = float(Decimal(str(data.avoidable_failure_retry_confirmed_usd)) / actual) if actual else 0.0
    result["estimate_relative_error"] = (float(abs(estimated - actual) / actual) if actual
                                           else (0.0 if estimated == 0 else None))
    result["team_utilization"] = data.team_spend_confirmed_usd / data.team_budget_usd
    result["billing_match_rate"] = data.matched_receipts / data.eligible_receipts
    result["budget_state"] = ("BLOCK_NEW_PAID_CALLS" if result["team_utilization"] >= 0.9
        else "WARNING" if result["team_utilization"] >= 0.8 else "WITHIN_LIMIT")
    gates = {
        "unit_cost": result["unit_cost_confirmed_usd"] <= 0.5,
        "avoidable_waste": result["avoidable_waste_rate"] <= 0.1,
        "budget_control": data.team_key_confirmed and data.budget_controls_verified
                          and result["team_utilization"] < 0.9,
        "reconciliation": result["billing_match_rate"] == 1.0 and data.cost_traceability_complete
            and result["estimate_relative_error"] is not None and result["estimate_relative_error"] <= 0.2,
    }
    result["dimension_pass"] = gates
    result["score_100"] = sum(result["weights"][name] for name, passed in gates.items() if passed)
    result["reason_codes"] = [name.upper() + "_GATE_FAILED" for name, passed in gates.items() if not passed]
    result["status"] = "PASS_PROVISIONAL" if all(gates.values()) else "FAIL"
    return result


class E2ECaseRecord(StrictRecord):
    case_id: Identifier
    success: bool | None = None
    machine_seconds: Money | None = None
    duration_censored: bool | None = None


class NoviceUserRecord(StrictRecord):
    participant_id: Identifier
    completed_without_help: bool | None = None


class ServiceEvaluationRecord(StrictRecord):
    evaluation_cycle_id: Identifier | None = None
    candidate_settings_sha256: Digest | None = None
    e2e_cases: list[E2ECaseRecord] = Field(default_factory=list, max_length=20)
    novice_users: list[NoviceUserRecord] = Field(default_factory=list, max_length=5)
    text_owner_accepted: bool | None = None
    renderer_owner_accepted: bool | None = None


def evaluate_service(record: ServiceEvaluationRecord | Mapping) -> dict:
    data = _validate(ServiceEvaluationRecord, record)
    result = {"status": "BLOCKED", "score_100": None, "success_rate": None,
              "machine_p95_seconds": None, "novice_completions": None, "reason_codes": [],
              "record_sha256": content_hash(data)}
    if data.evaluation_cycle_id is None or data.candidate_settings_sha256 is None:
        result["reason_codes"].append("SERVICE_CANDIDATE_UNKNOWN")
        return result
    if (len(data.e2e_cases) != 20 or len(data.novice_users) != 5
            or len({c.case_id for c in data.e2e_cases}) != 20
            or len({u.participant_id for u in data.novice_users}) != 5):
        result["reason_codes"].append("SERVICE_SAMPLE_INCOMPLETE_OR_DUPLICATE")
        return result
    if (any(c.success is None or c.machine_seconds is None or c.duration_censored is None for c in data.e2e_cases)
            or any(u.completed_without_help is None for u in data.novice_users)
            or data.text_owner_accepted is None or data.renderer_owner_accepted is None):
        result["reason_codes"].append("SERVICE_EVIDENCE_UNKNOWN")
        return result
    result["success_rate"] = sum(c.success for c in data.e2e_cases) / 20
    result["novice_completions"] = sum(u.completed_without_help for u in data.novice_users)
    if any(c.duration_censored for c in data.e2e_cases):
        result["reason_codes"].append("LATENCY_CENSORED")
        return result
    # All completed durations, including terminal failures, belong here.
    result["machine_p95_seconds"] = sorted(c.machine_seconds for c in data.e2e_cases)[18]
    gates = {"success": result["success_rate"] >= 0.9,
             "latency": result["machine_p95_seconds"] <= 90,
             "usability": result["novice_completions"] >= 4,
             "text_and_renderer_acceptance": data.text_owner_accepted and data.renderer_owner_accepted}
    result["dimension_pass"] = gates
    result["score_100"] = sum(25 for passed in gates.values() if passed)
    result["reason_codes"] = [name.upper() + "_GATE_FAILED" for name, passed in gates.items() if not passed]
    result["status"] = "PASS_PROVISIONAL" if all(gates.values()) else "FAIL"
    return result


def evaluate_release(manifest: RealProductManifest | Mapping,
                     image_reviews: list[ImageReviewRecord | Mapping], *,
                     finops: FinOpsRecord | Mapping,
                     service: ServiceEvaluationRecord | Mapping) -> dict:
    manifest = _validate(RealProductManifest, manifest)
    finops = _validate(FinOpsRecord, finops)
    service = _validate(ServiceEvaluationRecord, service)
    images = evaluate_dataset(manifest, image_reviews)
    costs = evaluate_finops(finops)
    experience = evaluate_service(service)
    image_scores = [case["score_100"] for case in images["cases"]]
    known_failure_codes = {"CASE_NOT_PASSED", "DEV_PRINTED_LABEL_REVIEWS_REQUIRED",
                           "HOLDOUT_PRINTED_LABEL_REVIEWS_REQUIRED"}
    image_score = (sum(image_scores) / 20
        if len(image_scores) == 20 and all(s is not None for s in image_scores)
        and set(images["reason_codes"]).issubset(known_failure_codes) else None)
    parts = {"image_quality": image_score, "finops": costs["score_100"], "service": experience["score_100"]}
    weighted = (round(parts["image_quality"] * .4 + parts["finops"] * .4 + parts["service"] * .2, 4)
                if all(value is not None for value in parts.values()) else None)
    same_cycle = (manifest.evaluation_cycle_id is not None
        and manifest.evaluation_cycle_id == finops.evaluation_cycle_id == service.evaluation_cycle_id
        and manifest.holdout_settings_sha256 is not None
        and manifest.holdout_settings_sha256 == finops.candidate_settings_sha256 == service.candidate_settings_sha256)
    if not same_cycle:
        weighted = None
    gates_pass = (same_cycle and images["status"] == "PROVISIONAL_IMAGE_CRITERIA_MET"
                  and costs["status"] == experience["status"] == "PASS_PROVISIONAL")
    return {"version": "image-eval-v0.1", "weights": {"image_quality": 40, "finops": 40, "service": 20},
            "score_100": weighted, "component_scores": parts,
            "same_evaluation_cycle_and_candidate": same_cycle,
            "status": "PROVISIONAL_GATES_MET_AWAITING_TEAM_SIGNOFF" if gates_pass else "BLOCKED",
            "asset_approval": "NOT_GRANTED", "service_release_approval": "NOT_GRANTED",
            "new_paid_calls_authorized": False, "image_evaluation": images,
            "finops_evaluation": costs, "service_evaluation": experience}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Score declared image reviews offline; no model calls")
    parser.add_argument("record", help="JSON object containing an ImageReviewRecord")
    args = parser.parse_args(argv)
    try:
        with open(args.record, encoding="utf-8") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result
        report = score_image_case(json.loads(raw, object_pairs_hook=unique_keys))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "INVALID_REVIEW_RECORD", "model_invoked": False}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
