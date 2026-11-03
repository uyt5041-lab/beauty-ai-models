"""Strict wire, privacy, relational integrity, and precision at the work boundary."""
from copy import deepcopy
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import json
from uuid import UUID

import pytest

from beauty_ai.work_contracts import (
    DATASET_SCHEMA_VERSION, DEFAULT_REQUIRED_STAGES, MAX_JSON_BYTES, MAX_TOKEN_COUNT,
    RATE_SCHEMA_VERSION, RECEIPT_SCHEMA_VERSION, canonical_bytes, digest,
    normalize_attempt, normalize_attempt_spec, normalize_completion, normalize_create_spec,
    normalize_rate_book, normalize_receipt, normalize_usage, normalize_uuid,
    normalize_work, normalize_work_dataset, parse_work_json, usd_decimal, usd_string,
    validate_timestamp,
)

PROJECT = "00000000-0000-0000-0000-000000000001"
WORK = "00000000-0000-0000-0000-000000000002"
ATTEMPT = "00000000-0000-0000-0000-000000000003"
OTHER = "00000000-0000-0000-0000-000000000004"
JOB = "00000000-0000-0000-0000-000000000005"
TIME = "2026-11-03T01:00:00+00:00"
LATER = "2026-11-03T01:01:00+00:00"


def attempt(**changes):
    return {"attempt_id": ATTEMPT, "work_id": WORK, "project_id": PROJECT,
            "stage": "image_generation", "operation": "generate", "provider": "openai",
            "configured_model": "image-v1", "prompt_version": "prompt-v1",
            "request_hash": "a" * 64, "parent_attempt_id": None, "started_at": TIME, **changes}


def receipt(**changes):
    return {"schema_version": RECEIPT_SCHEMA_VERSION, "attempt_id": ATTEMPT,
            "provider_request_id": "req-1", "model": "image-v1",
            "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            "elapsed_seconds": 0.25, "status": "SUCCEEDED", "error_code": None,
            "artifact_sha256": "b" * 64, "estimated_cost_usd": "0.000000001",
            "rate_version": "rates-v1", "actual_cost_usd": None,
            "billing_evidence_ref": None, "source_ref": "receipt:one", "source_sha256": "c" * 64,
            **changes}


def completion(**changes):
    return {"artifact_sha256": "b" * 64, "reviewer_ref": "reviewer-1",
            "quality_policy_version": "quality-v1", "quality_pass": True,
            "expected_attempt_ids": [ATTEMPT], **changes}


def work(**changes):
    return {"work_id": WORK, "project_id": PROJECT, "product_ref": "product-1",
            "task_pair_id": None, "arm": None, "required_stages": ["image_generation"],
            "status": "OPEN", "created_at": TIME, "closed_at": None, "completion": None,
            **changes}


def dataset(**changes):
    return {"schema_version": DATASET_SCHEMA_VERSION, "data_kind": "DECLARED_DATA",
            "works": [work()], "attempts": [attempt()], "receipts": [receipt()],
            "image_bindings": [{"project_id": PROJECT, "work_id": WORK, "job_id": JOB,
                                "attempt_id": ATTEMPT}], "rate_book": None, **changes}


def rates(**changes):
    return {"schema_version": RATE_SCHEMA_VERSION, "rate_version": "rates-v1",
            "models": {"image-v1": {"mode": "combined", "input_usd": "1",
                        "input_text_usd": None, "input_image_usd": None, "output_usd": "2"}},
            **changes}


def assert_safe_failure(function, value):
    with pytest.raises(ValueError, match=r"^WORK_[A-Z_]+$") as error:
        function(value)
    assert "PRIVATE123456789" not in str(error.value)
    assert len(str(error.value)) < 60


def test_normalization_is_detached_deterministic_and_idempotent():
    source = dataset(rate_book=rates())
    original = deepcopy(source)
    normalized = normalize_work_dataset(source)
    assert source == original
    assert normalize_work_dataset(normalized) == normalized
    assert normalized["receipts"][0]["estimated_cost_usd"] == "0.000000001"
    assert normalized["receipts"][0]["usage"]["cached_input_tokens"] is None
    assert digest(normalized) == digest(json.loads(canonical_bytes(normalized)))
    normalized["receipts"][0]["usage"]["input_tokens"] = 1
    assert source == original


def test_create_defaults_and_independently_nullable_pair_fields():
    value = normalize_create_spec({"product_ref": "product-1"})
    assert value["required_stages"] == list(DEFAULT_REQUIRED_STAGES)
    assert value["task_pair_id"] is None and value["arm"] is None
    assert normalize_create_spec({"product_ref": "p", "arm": "baseline"})["task_pair_id"] is None
    assert normalize_create_spec({"product_ref": "p", "task_pair_id": "pair-1"})["arm"] is None
    assert normalize_create_spec({"product_ref": "p", "required_stages": ["composition", "review"]})[
        "required_stages"] == ["review", "composition"]


@pytest.mark.parametrize("changes", [
    {"product_ref": 1}, {"product_ref": "private/authorization"}, {"arm": "other"},
    {"required_stages": []}, {"required_stages": ["review", "review"]},
    {"required_stages": ["unknown"]}, {"extra": None},
])
def test_create_spec_rejects_unsafe_types_and_extra_keys(changes):
    assert_safe_failure(normalize_create_spec, {"product_ref": "p", **changes})


def test_uuid_and_timestamp_boundaries_are_explicit():
    assert normalize_uuid(UUID(WORK)) == WORK
    assert validate_timestamp("2026-11-03T10:00:00+09:00") == TIME
    assert validate_timestamp("2026-11-03T01:00:00Z") == TIME
    assert_safe_failure(normalize_uuid, "00000000000000000000000000000002")
    for value in ("2026-11-03", "2026-11-03T01:00:00", "2026-02-30T01:00:00Z", True):
        assert_safe_failure(validate_timestamp, value)


@pytest.mark.parametrize("value", [True, -1, 1.0, "1", MAX_TOKEN_COUNT + 1])
def test_usage_rejects_coercions_or_out_of_range_tokens(value):
    assert_safe_failure(normalize_usage, {"input_tokens": value})


def test_usage_preserves_unknown_parts_and_validates_subsets_without_double_counting():
    partial = normalize_usage({"input_tokens": MAX_TOKEN_COUNT})
    assert partial["input_tokens"] == MAX_TOKEN_COUNT and partial["output_tokens"] is None
    assert normalize_usage(None) is None
    for value in ({"input_tokens": 2, "output_tokens": 3, "total_tokens": 4},
                  {"input_tokens": 2, "cached_input_tokens": 3},
                  {"output_tokens": 2, "reasoning_tokens": 3},
                  {"input_tokens": 2, "input_text_tokens": 2, "input_image_tokens": 1},
                  {"undocumented_tokens": 1}):
        assert_safe_failure(normalize_usage, value)
    assert normalize_usage({"input_tokens": 5, "cached_input_tokens": 5,
                            "input_text_tokens": 5})["input_tokens"] == 5


@pytest.mark.parametrize("changes", [
    {"elapsed_seconds": True}, {"elapsed_seconds": -1}, {"elapsed_seconds": float("inf")},
    {"elapsed_seconds": 10**400},
    {"usage": {"input_tokens": float("nan")}}, {"source_sha256": "A" * 64},
    {"estimated_cost_usd": "0.1", "rate_version": None}, {"actual_cost_usd": "1"},
    {"estimated_cost_usd": "1e-9"}, {"estimated_cost_usd": "1000000000.000000001"},
    {"error_code": "PRIVATE raw provider text"}, {"error_code": "TEXT_REFUSAL"},
    {"model": "sk-PRIVATE123456789"}, {"provider_request_id": "https://private.example"},
    {"prompt": "private raw text"},
])
def test_receipts_reject_unsafe_types_missing_evidence_and_private_data(changes):
    assert_safe_failure(normalize_receipt, receipt(**changes))


def test_receipt_requires_all_nullable_fields_and_keeps_failed_costs():
    value = receipt(status="FAILED", error_code="TEXT_REFUSAL", artifact_sha256=None)
    assert normalize_receipt(value)["estimated_cost_usd"] == "0.000000001"
    del value["actual_cost_usd"]
    assert_safe_failure(normalize_receipt, value)


def test_money_reuses_exact_arithmetic_under_hostile_decimal_context():
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert usd_string(usd_decimal("1000000000.000000000")) == "1000000000.000000000"
        assert usd_string(Decimal("3000000000.123456789")) == "3000000000.123456789"
        assert normalize_rate_book(rates())["models"]["image-v1"]["input_usd"] == "1.000000000"


def test_rate_book_allows_missing_input_rate_and_strictly_validates_modes():
    value = rates()
    value["models"]["image-v1"]["input_usd"] = None
    assert normalize_rate_book(value)["models"]["image-v1"]["input_usd"] is None
    assert normalize_rate_book(None) is None
    for changes in ({"mode": "split"}, {"output_usd": None}, {"extra": "x"},
                    {"input_usd": True}, {"input_image_usd": "1"}):
        value = rates()
        value["models"]["image-v1"].update(changes)
        assert_safe_failure(normalize_rate_book, value)


@pytest.mark.parametrize("raw", [
    b'{"work_id":"one","work_id":"two"}', b'{"usage":NaN}', b'{"usage":Infinity}',
    b'{"password":"PRIVATE"}', b'{"model":"sk-PRIVATE123456789"}', b'\xff',
    b'{', b'[' * 50 + b']' * 50, b' ' * (MAX_JSON_BYTES + 1),
])
def test_raw_json_rejects_duplicates_nonfinite_private_unbounded_and_malformed(raw):
    assert_safe_failure(parse_work_json, raw)


def test_raw_json_parser_and_internal_dict_dataset_have_distinct_size_boundaries():
    assert parse_work_json('{"x":1}') == {"x": 1}
    records = [receipt(attempt_id=str(UUID(int=100 + index)), provider_request_id=f"req-{index}")
               for index in range(1500)]
    identities = [attempt(attempt_id=row["attempt_id"], work_id=WORK if index < 750 else OTHER)
                  for index, row in enumerate(records)]
    value = dataset(works=[work(), work(work_id=OTHER)], receipts=records,
                    attempts=identities, image_bindings=[])
    assert len(canonical_bytes(value)) > MAX_JSON_BYTES
    assert len(normalize_work_dataset(value)["receipts"]) == 1500


@pytest.mark.parametrize("mutation", [
    lambda d: d["works"].append(work(work_id=OTHER, project_id=OTHER)),
    lambda d: d["attempts"][0].update(work_id=OTHER),
    lambda d: d["attempts"][0].update(project_id=OTHER),
    lambda d: d["receipts"][0].update(attempt_id=OTHER),
    lambda d: d["image_bindings"][0].update(work_id=OTHER),
    lambda d: d["image_bindings"][0].update(attempt_id=OTHER),
    lambda d: d["image_bindings"].append(deepcopy(d["image_bindings"][0])),
    lambda d: d["works"].append(deepcopy(d["works"][0])),
    lambda d: d["attempts"].append(deepcopy(d["attempts"][0])),
    lambda d: d["receipts"].append(deepcopy(d["receipts"][0])),
])
def test_dataset_rejects_cross_project_or_dangling_and_duplicate_identity(mutation):
    value = dataset()
    mutation(value)
    assert_safe_failure(normalize_work_dataset, value)


def test_parent_must_exist_in_same_work_and_cannot_form_equal_timestamp_cycle():
    assert_safe_failure(normalize_attempt, attempt(parent_attempt_id=ATTEMPT))
    assert_safe_failure(normalize_work_dataset, dataset(attempts=[attempt(parent_attempt_id=OTHER)]))
    value = dataset(attempts=[attempt(parent_attempt_id=OTHER),
                             attempt(attempt_id=OTHER, parent_attempt_id=ATTEMPT)])
    assert_safe_failure(normalize_work_dataset, value)
    value = dataset(works=[work(), work(work_id=OTHER)],
                    attempts=[attempt(parent_attempt_id=OTHER), attempt(attempt_id=OTHER, work_id=OTHER)])
    assert_safe_failure(normalize_work_dataset, value)


def test_provider_request_id_is_unique_per_project_provider_across_works():
    value = dataset(works=[work(), work(work_id=OTHER)],
                    attempts=[attempt(), attempt(attempt_id=OTHER, work_id=OTHER)],
                    receipts=[receipt(), receipt(attempt_id=OTHER)])
    assert_safe_failure(normalize_work_dataset, value)
    value["receipts"][1]["provider_request_id"] = None
    assert len(normalize_work_dataset(value)["receipts"]) == 2


def test_pair_arm_is_unique_while_unpaired_works_remain_allowed():
    value = dataset(works=[work(task_pair_id="pair-1", arm="baseline"),
                           work(work_id=OTHER, task_pair_id="pair-1", arm="baseline")])
    assert_safe_failure(normalize_work_dataset, value)
    value["works"][1]["arm"] = "candidate"
    assert len(normalize_work_dataset(value)["works"]) == 2


def test_completion_requires_exact_attempts_terminal_jobs_stages_and_matching_final_artifact():
    value = dataset(works=[work(status="COMPLETED", closed_at=LATER, completion=completion())])
    assert normalize_work_dataset(value)["works"][0]["status"] == "COMPLETED"
    mutations = [lambda d: d["works"][0]["completion"].update(expected_attempt_ids=[]),
                 lambda d: d.update(receipts=[]),
                 lambda d: d["image_bindings"][0].update(attempt_id=None),
                 lambda d: d["works"][0].update(required_stages=["review"]),
                 lambda d: d["works"][0]["completion"].update(artifact_sha256="d" * 64),
                 lambda d: d["receipts"][0].update(status="FAILED", error_code="PROVIDER_AUTH")]
    for mutation in mutations:
        changed = deepcopy(value)
        mutation(changed)
        assert_safe_failure(normalize_work_dataset, changed)


def test_local_receipt_requires_explicit_zero_api_evidence_and_excludes_invented_actual():
    identity = attempt(stage="composition", provider="local", configured_model="local")
    local = receipt(model="local", provider_request_id=None, usage={"input_tokens": 0, "output_tokens": 0},
                    estimated_cost_usd="0", rate_version="local-api-zero-v1")
    value = dataset(attempts=[identity], receipts=[local], image_bindings=[])
    normalized = normalize_work_dataset(value)
    assert normalized["receipts"][0]["actual_cost_usd"] is None
    for changes in ({"usage": None}, {"estimated_cost_usd": None},
                    {"rate_version": "rates-v1"}, {"usage": {"input_tokens": 1, "output_tokens": 0}}):
        assert_safe_failure(normalize_work_dataset, {**value, "receipts": [{**local, **changes}]})


def test_work_state_and_quality_use_strict_true_and_aware_chronology():
    for value in (1, "true", False):
        assert_safe_failure(normalize_completion, completion(quality_pass=value))
    for changes in ({"status": "COMPLETED"}, {"closed_at": LATER}, {"completion": completion()},
                    {"status": "CANCELLED", "closed_at": "2026-11-02T01:00:00Z"}):
        assert_safe_failure(normalize_work, work(**changes))
    cancelled = work(status="CANCELLED", closed_at=LATER)
    assert normalize_work_dataset(dataset(works=[cancelled]))["receipts"][0]["status"] == "SUCCEEDED"


def test_per_work_attempt_limit_is_enforced_in_addition_to_project_receipt_limit():
    identities = [attempt(attempt_id=str(UUID(int=100 + index))) for index in range(1001)]
    assert_safe_failure(normalize_work_dataset, dataset(attempts=identities, receipts=[], image_bindings=[]))


def test_attempt_spec_has_no_runtime_or_owner_fields_and_checks_parent_identity():
    full = attempt()
    spec = {key: value for key, value in full.items() if key not in {"project_id", "work_id", "started_at"}}
    del spec["parent_attempt_id"]
    assert normalize_attempt_spec(spec)["parent_attempt_id"] is None
    assert_safe_failure(normalize_attempt_spec, full)
    assert_safe_failure(normalize_attempt_spec, {**spec, "operation": "review_copy"})
    assert normalize_attempt_spec({**spec, "operation": "review_copy", "stage": "review"})["stage"] == "review"
