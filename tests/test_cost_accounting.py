from copy import deepcopy
from decimal import Decimal, Inexact, ROUND_UP, localcontext
import hashlib
import subprocess
import sys

import pytest

from beauty_ai.cost_accounting import (
    SCHEMA_VERSION, allocate_usd, canonical_bytes, digest, latest_records,
    normalize_dataset, usd_decimal, usd_string, validate_identifier,
)


def cost(**changes):
    record = {
        "cost_id": "cost-1", "revision": 1, "supersedes_revision": None,
        "source_ref": "evidence/source.json", "source_sha256": "a" * 64,
        "kind": "image", "category": "production", "estimated_cost_usd": "0.1",
        "rate_version": "rates-v1", "actual_cost_usd": None,
        "billing_status": "UNRECONCILED", "billing_evidence_ref": None,
        "allocations": {"batch-1": 10000}, "state": "SUCCEEDED",
    }
    return {**record, **changes}


def batch(**changes):
    return {"batch_id": "batch-1", "product_id": "product-1",
            "expected_cost_ids": ["cost-1"], **changes}


def acceptance(**changes):
    return {
        "ad_id": "ad-1", "revision": 1, "supersedes_revision": None,
        "batch_id": "batch-1", "action": "accept", "artifact_sha256": "b" * 64,
        "reviewer_ref": "reviewer-1", "quality_policy_version": "quality-v1",
        "quality_pass": True, **changes,
    }


def dataset(costs=None, batches=None, acceptances=None):
    return {"schema_version": SCHEMA_VERSION,
            "cost_items": [cost()] if costs is None else costs,
            "batches": [batch()] if batches is None else batches,
            "acceptances": [] if acceptances is None else acceptances}


@pytest.mark.parametrize("value", ["0", "0.000000001", "1.234567890", "1000000000.000000000"])
def test_money_retains_exact_nine_place_values(value):
    assert usd_decimal(value) == Decimal(value)
    assert usd_decimal(usd_string(usd_decimal(value))) == Decimal(value)


@pytest.mark.parametrize("value", [
    0, 1.2, Decimal("1"), True, None, "", "01", ".1", "1.", "+1", "-0",
    "-1", " 1", "1\n", "1e-3", "NaN", "Infinity", "0.0000000001",
    "1000000000.000000001", "1000000001", "１２", "1,000",
])
def test_money_rejects_coercions_and_out_of_contract_values(value):
    with pytest.raises(ValueError, match="^COST_AMOUNT_INVALID$"):
        usd_decimal(value)


def test_money_format_supports_derived_totals_negative_values_and_stable_rounding():
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        assert usd_string(Decimal("3000000000.123456789")) == "3000000000.123456789"
        assert usd_string(Decimal("-1.2345678905")) == "-1.234567890"
        assert usd_string(Decimal("1.2345678915")) == "1.234567892"
        assert usd_string(Decimal("-0.0000000001")) == "0.000000000"
        assert usd_decimal("999999999.123456789") == Decimal("999999999.123456789")


@pytest.mark.parametrize("value", [1, "1", Decimal("NaN"), Decimal("Infinity")])
def test_money_formatter_rejects_nonfinite_and_non_decimal_values(value):
    with pytest.raises(ValueError, match="^COST_AMOUNT_INVALID$"):
        usd_string(value)


def test_allocation_residual_goes_to_sorted_last_batch_with_exact_conservation():
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = True
        result = allocate_usd("0.000000001", {"c": 3334, "b": 3333, "a": 3333})
        assert result == {"a": "0.000000000", "b": "0.000000000", "c": "0.000000001"}
        result = allocate_usd("1000000000.000000000", {"z": 6667, "a": 3333})
        assert result == {"a": "333300000.000000000", "z": "666700000.000000000"}
    result = allocate_usd("0.123456789", {"b": 6667, "a": 3333})
    assert result == {"a": "0.041148147", "b": "0.082308642"}
    assert sum(map(Decimal, result.values())) == Decimal("0.123456789")


@pytest.mark.parametrize("allocations", [
    {}, {"a": 9999}, {"a": 10001}, {"a": -1, "b": 10001},
    {"a": True, "b": 9999}, {"a": 10000.0}, {"a": "10000"}, [],
])
def test_allocation_requires_integer_basis_points_totaling_10000(allocations):
    with pytest.raises(ValueError, match="^COST_ALLOCATION_INVALID$"):
        allocate_usd("1", allocations)


def test_normalization_is_detached_stable_and_never_creates_acceptance():
    original = dataset(costs=[cost(cost_id="z"), cost(cost_id="a")],
                       batches=[batch(expected_cost_ids=["z", "a"])])
    untouched = deepcopy(original)
    result = normalize_dataset(original)
    assert original == untouched
    assert [item["cost_id"] for item in result["cost_items"]] == ["a", "z"]
    assert result["batches"][0]["expected_cost_ids"] == ["a", "z"]
    assert result["cost_items"][0]["estimated_cost_usd"] == "0.100000000"
    assert result["acceptances"] == []
    result["cost_items"][0]["allocations"]["batch-1"] = 0
    result["batches"][0]["expected_cost_ids"].clear()
    assert original == untouched


def test_empty_dataset_is_valid():
    assert normalize_dataset(dataset([], [], [])) == dataset([], [], [])


def test_full_cost_history_deduplicates_and_selects_latest_out_of_order():
    first = cost()
    second = cost(revision=2, supersedes_revision=1, actual_cost_usd="0.2",
                  billing_status="RECONCILED", billing_evidence_ref="billing/invoice-1.json")
    data = dataset([second, first, deepcopy(first), deepcopy(second)])
    normalized = normalize_dataset(data)
    assert normalized["cost_items"] == [
        {**first, "estimated_cost_usd": "0.100000000"},
        {**second, "actual_cost_usd": "0.200000000", "estimated_cost_usd": "0.100000000"}]
    assert latest_records(normalized["cost_items"], "cost_id") == [normalized["cost_items"][1]]
    assert normalize_dataset(normalized) == normalized
    assert len(data["cost_items"]) == 4


@pytest.mark.parametrize("change", [
    {"estimated_cost_usd": "0.2"}, {"estimated_cost_usd": "0.10"},
    {"source_sha256": "b" * 64}, {"state": "UNKNOWN"},
])
def test_revision_collision_never_arbitrarily_selects_a_record(change):
    with pytest.raises(ValueError, match="^COST_REVISION_CONFLICT$"):
        normalize_dataset(dataset([cost(), cost(**change)]))


@pytest.mark.parametrize("records", [
    [cost(revision=2, supersedes_revision=1)],
    [cost(), cost(revision=3, supersedes_revision=2)],
])
def test_missing_original_or_middle_revision_is_rejected(records):
    with pytest.raises(ValueError, match="^COST_REVISION_CHAIN_INVALID$"):
        normalize_dataset(dataset(records))


@pytest.mark.parametrize("changes", [
    {"revision": 0}, {"revision": True}, {"revision": 1.0},
    {"revision": 1, "supersedes_revision": 0},
    {"revision": 2, "supersedes_revision": None},
    {"revision": 2, "supersedes_revision": True},
    {"revision": 3, "supersedes_revision": 1},
])
def test_revision_fields_are_strict(changes):
    with pytest.raises(ValueError, match="^COST_REVISION_INVALID$"):
        normalize_dataset(dataset([cost(**changes)]))


def test_invalid_superseded_observation_is_not_hidden_by_valid_latest():
    with pytest.raises(ValueError, match="^COST_RATE_VERSION_REQUIRED$"):
        normalize_dataset(dataset([cost(rate_version=None), cost(revision=2, supersedes_revision=1)]))


@pytest.mark.parametrize("state", ["SUBMITTING", "SUCCEEDED", "FAILED", "UNKNOWN", "MISSING", "RECORDED"])
def test_all_states_preserve_null_costs_without_inventing_zero(state):
    result = normalize_dataset(dataset([cost(state=state, estimated_cost_usd=None, rate_version=None)]))
    assert result["cost_items"][0]["actual_cost_usd"] is None
    assert result["cost_items"][0]["estimated_cost_usd"] is None
    assert result["cost_items"][0]["state"] == state


def test_partial_billing_preserves_evidenced_amount():
    result = normalize_dataset(dataset([cost(actual_cost_usd="0.000000001", billing_status="PARTIAL",
                                            billing_evidence_ref="./billing/partial.json")]))
    assert result["cost_items"][0]["actual_cost_usd"] == "0.000000001"
    assert result["cost_items"][0]["billing_status"] == "PARTIAL"


@pytest.mark.parametrize("change,code", [
    ({"actual_cost_usd": "0"}, "BILLING_EVIDENCE_REQUIRED"),
    ({"rate_version": None}, "RATE_VERSION_REQUIRED"),
    ({"category": "research"}, "ALLOCATION_INVALID"),
    ({"allocations": {}}, "ALLOCATION_INVALID"),
    ({"kind": "unknown"}, "SCHEMA_INVALID"),
    ({"state": "success"}, "SCHEMA_INVALID"),
    ({"category": "unknown"}, "SCHEMA_INVALID"),
    ({"billing_status": "PAID"}, "SCHEMA_INVALID"),
    ({"source_sha256": "g" * 64}, "HASH_INVALID"),
])
def test_cost_evidence_and_enums_are_strict(change, code):
    with pytest.raises(ValueError, match=f"^COST_{code}$"):
        normalize_dataset(dataset([cost(**change)]))


def test_research_requires_empty_allocations_and_stays_in_dataset():
    data = dataset([cost(category="research", allocations={})], [])
    result = normalize_dataset(data)
    assert len(result["cost_items"]) == 1
    assert result["cost_items"][0]["category"] == "research"


@pytest.mark.parametrize("batches,code", [
    ([batch(expected_cost_ids=[])], "EXPECTED_COST_MISMATCH"),
    ([batch(expected_cost_ids=["cost-1", "cost-2"])], "EXPECTED_COST_MISMATCH"),
    ([batch(expected_cost_ids=["cost-1", "cost-1"])], "EXPECTED_COST_MISMATCH"),
    ([], "BATCH_REFERENCE_INVALID"),
    ([batch(), batch()], "BATCH_CONFLICT"),
])
def test_expected_cost_sets_and_batch_identity_must_be_exact(batches, code):
    with pytest.raises(ValueError, match=f"^COST_{code}$"):
        normalize_dataset(dataset(batches=batches))


def test_latest_cost_allocations_control_expected_sets():
    first = cost()
    second = cost(revision=2, supersedes_revision=1, allocations={"batch-2": 10000})
    result = normalize_dataset(dataset([second, first], [batch(batch_id="batch-2")]))
    assert latest_records(result["cost_items"], "cost_id")[0]["allocations"] == {"batch-2": 10000}


def test_acceptance_history_preserves_revoke_and_failed_quality():
    accepted = acceptance()
    revoked = acceptance(revision=2, supersedes_revision=1, action="revoke", quality_pass=False)
    result = normalize_dataset(dataset(acceptances=[revoked, accepted, deepcopy(accepted)]))
    assert result["acceptances"] == [accepted, revoked]
    assert latest_records(result["acceptances"], "ad_id") == [revoked]
    assert normalize_dataset(result) == result


@pytest.mark.parametrize("quality", [0, 1, "true", "false", None])
def test_quality_pass_is_an_actual_boolean(quality):
    with pytest.raises(ValueError, match="^COST_QUALITY_INVALID$"):
        normalize_dataset(dataset(acceptances=[acceptance(quality_pass=quality)]))


def test_ad_identity_cannot_move_to_another_batch_in_a_revision():
    with pytest.raises(ValueError, match="^COST_ACCEPTANCE_BATCH_CONFLICT$"):
        normalize_dataset(dataset(acceptances=[acceptance(), acceptance(
            revision=2, supersedes_revision=1, batch_id="batch-2")]))


def test_acceptance_unknown_batch_and_collision_are_rejected():
    with pytest.raises(ValueError, match="^COST_BATCH_REFERENCE_INVALID$"):
        normalize_dataset(dataset(acceptances=[acceptance(batch_id="absent")]))
    with pytest.raises(ValueError, match="^COST_REVISION_CONFLICT$"):
        normalize_dataset(dataset(acceptances=[acceptance(), acceptance(quality_pass=False)]))


@pytest.mark.parametrize("record_key", ["cost_items", "batches", "acceptances"])
def test_missing_and_unrecognized_fields_are_rejected(record_key):
    data = dataset(acceptances=[acceptance()])
    data[record_key][0]["unrecognized"] = "discarding this would hide evidence"
    with pytest.raises(ValueError, match="^COST_SCHEMA_INVALID$"):
        normalize_dataset(data)
    data = dataset(acceptances=[acceptance()])
    data[record_key][0].pop(next(iter(data[record_key][0])))
    with pytest.raises(ValueError, match="^COST_SCHEMA_INVALID$"):
        normalize_dataset(data)


@pytest.mark.parametrize("value", [
    "person@example.com", "sk-proj-1234567890abcdef", "key/api_key/value",
    "password", "../evidence.json", "x/../evidence.json", "https://example.com",
    "x" * 201, "line\nbreak", "<script>",
])
def test_identifier_errors_are_safe_and_never_echo_input(value):
    with pytest.raises(ValueError, match="^COST_IDENTIFIER_INVALID$") as error:
        normalize_dataset(dataset([cost(cost_id=value)]))
    assert value not in str(error.value)
    with pytest.raises(ValueError, match="^COST_IDENTIFIER_INVALID$"):
        validate_identifier(value)


def test_public_latest_view_validates_full_history():
    assert validate_identifier("legacy:group/case") == "legacy:group/case"
    with pytest.raises(ValueError, match="^COST_REVISION_CHAIN_INVALID$"):
        latest_records([cost(revision=2, supersedes_revision=1)], "cost_id")
    with pytest.raises(ValueError, match="^COST_SCHEMA_INVALID$"):
        latest_records([], "other_id")


@pytest.mark.parametrize("value", [
    "/Users/person/invoice.json", "C:/users/invoice.json", "../invoice.json",
    "x/../invoice.json", "https://example.com/invoice", "invoice?token=value",
    "evidence/sk-proj-123456789abcdef", "evidence/access_token/value", "x" * 513,
])
def test_source_references_do_not_publish_absolute_paths_or_secrets(value):
    with pytest.raises(ValueError, match="^COST_REFERENCE_INVALID$"):
        normalize_dataset(dataset([cost(source_ref=value)]))


def test_canonical_json_and_digest_are_stable_and_utf8():
    first = {"z": [True, None, 3.5], "a": "한글"}
    second = {"a": "한글", "z": [True, None, 3.5]}
    expected = '{"a":"한글","z":[true,null,3.5]}'.encode()
    assert canonical_bytes(first) == canonical_bytes(second) == expected
    assert digest(first) == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "x"}, (1, 2), b"x", Decimal("1")])
def test_canonical_json_rejects_ambiguous_and_non_json_values(value):
    with pytest.raises(ValueError, match="^COST_CANONICAL_INVALID$"):
        canonical_bytes(value)


def test_canonical_json_rejects_cycles_without_exposing_values():
    data = []
    data.append(data)
    with pytest.raises(ValueError, match="^COST_CANONICAL_INVALID$"):
        canonical_bytes(data)


def test_import_never_loads_provider_model_or_wandb_modules():
    script = """
import sys
import beauty_ai.cost_accounting
for name in ('openai', 'torch', 'wandb', 'beauty_ai.openai_images', 'beauty_ai.provider_attempts'):
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
