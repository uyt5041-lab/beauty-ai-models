"""Work report publication uses the existing local fake W&B implementation."""
from copy import deepcopy
import hashlib
import json

import pytest

from beauty_ai import cost_wandb as bridge
from beauty_ai.cost_reporting import build_report
from beauty_ai.work_contracts import digest, normalize_work_dataset
from test_cost_wandb import remote, sync  # Reuse the established SDK/API fixture.
from test_work_metrics import (PROJECT, add_attempt, add_work, complete, dataset, pair,
                               rate, rate_book, uid)


EMPTY = {"schema_version": "beautyai-cost-dataset-v1", "cost_items": [], "batches": [], "acceptances": []}


def work_dataset():
    data = dataset()
    baseline, candidate = pair(data, baseline="2", candidate="1")
    # Add a failed revision without changing the accepted final artifact.
    add_attempt(data, baseline, "failed-revision", status="FAILED", stage="revision", amount="0.25",
                usage={"input_tokens": 4, "output_tokens": 2})
    complete(data, baseline)
    local = add_work(data, "local")
    add_attempt(data, local, "local-compose", provider="local", model="local", stage="composition",
                amount="0", rate="local-api-zero-v1", usage={"input_tokens": 0, "output_tokens": 0})
    pending = add_work(data, "pending")
    add_attempt(data, pending, "pending-call", status="PENDING")
    data["image_bindings"].append({"project_id": PROJECT, "work_id": pending["work_id"],
                                   "job_id": uid("unobserved-job"), "attempt_id": None})
    return data


@pytest.fixture
def work_report_file(tmp_path):
    report = build_report(EMPTY, work_dataset=work_dataset())
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path


def table(run, key):
    artifact = next(item for item in run.artifacts if item.type == "run_table" and item.key == key)
    return artifact, artifact.get(key)


def replace(path, change):
    report = json.loads(path.read_bytes())
    change(report)
    path.write_text(json.dumps(report) + "\n")


def test_work_tables_summary_exact_artifact_and_deduplicated_sync(work_report_file, remote):
    original = work_report_file.read_bytes()
    source = json.loads(original)
    first = sync(work_report_file, remote)
    assert first["status"] == "READBACK_VERIFIED", first
    second = sync(work_report_file, remote)
    assert second["status"] == "READBACK_VERIFIED" and second["deduplicated"]
    assert len(remote[1].initializations) == 1
    run = next(iter(remote[0].remote.values()))
    artifact = next(item for item in run.artifacts if item.type == "cost-comparison")
    assert artifact.files["report.json"] == original == work_report_file.read_bytes()
    assert run.config["work_dataset_sha256"] == digest(normalize_work_dataset(work_dataset()))
    work = table(run, "work_costs")[1]
    stages = table(run, "work_stage_usage")[1]
    assert work.columns == bridge.WORK_TABLE_COLUMNS
    assert stages.columns == bridge.STAGE_TABLE_COLUMNS
    assert len(work.data) == 4 and len(stages.data) == 5
    pending = next(row for row in work.data if row[0] == uid("pending"))
    assert pending[work.columns.index("input_tokens")] is None
    assert pending[work.columns.index("known_input_tokens")] == 0
    assert pending[work.columns.index("missing_usage_count")] == 2
    assert run.summary["work_known_partial_estimated_usd"] == 3.25
    assert run.summary["work_estimated_total_usd"] is None
    assert run.summary["work_compared_pair_count"] == 1
    assert run.summary["work_excluded_pair_count"] == 2
    assert run.summary["work_comparison_estimated_cost_reduction_pct"] == float(
        source["work_costs"]["comparison"]["estimated_cost_reduction_pct"])
    assert run.summary["report_file_sha256"] == hashlib.sha256(original).hexdigest()
    assert any(detail["receipt"] and detail["receipt"]["elapsed_seconds"] == 0.5
               for row in source["work_costs"]["works"] for detail in row["attempts"])


def test_work_null_summary_codec_preserves_unknowns(work_report_file, remote):
    remote[1].drop_null_summaries = True
    assert sync(work_report_file, remote)["status"] == "READBACK_VERIFIED"
    run = next(iter(remote[0].remote.values()))
    assert "work_estimated_total_usd" not in run.summary
    assert run.summary["work_known_partial_estimated_usd"] == 3.25
    assert sync(work_report_file, remote)["deduplicated"]
    run.summary["work_estimated_total_usd"] = 0
    result = sync(work_report_file, remote)
    assert result["error_code"] == "WANDB_COST_REMOTE_SUMMARY_MISMATCH"
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("key", ["comparison", "work_costs", "work_stage_usage"])
@pytest.mark.parametrize("change", ["missing", "cell", "bool", "columns", "duplicate"])
def test_every_work_and_legacy_table_is_independently_verified(work_report_file, remote, key, change):
    assert sync(work_report_file, remote)["status"] == "READBACK_VERIFIED"
    run = next(iter(remote[0].remote.values()))
    artifact, value = table(run, key)
    if change == "missing":
        run.artifacts.remove(artifact)
    elif change == "cell":
        value.data[0][0] = "different"
    elif change == "bool":
        column = "attempt_count" if key != "comparison" else "candidate_missing_cost_count"
        value.data[0][value.columns.index(column)] = True
    elif change == "columns":
        value.columns.reverse()
    else:
        run.artifacts.append(artifact)
    receipt = sync(work_report_file, remote)
    assert receipt["status"] == "INCOMPLETE"
    assert receipt["error_code"] == "WANDB_COST_REMOTE_TABLE_MISMATCH"
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("change", ["artifact", "summary", "config", "known_summary_missing"])
def test_work_file_identity_and_summaries_are_verified(work_report_file, remote, change):
    assert sync(work_report_file, remote)["status"] == "READBACK_VERIFIED"
    run = next(iter(remote[0].remote.values()))
    if change == "artifact":
        next(item for item in run.artifacts if item.type == "cost-comparison").files["report.json"] += b" "
    elif change == "summary":
        run.summary["work_known_input_tokens"] = 999
    elif change == "config":
        run.config["work_dataset_sha256"] = "f" * 64
    else:
        del run.summary["work_known_partial_estimated_usd"]
    assert sync(work_report_file, remote)["status"] == "INCOMPLETE"
    assert len(remote[1].initializations) == 1


@pytest.mark.parametrize("change", [
    "top_extra", "row_extra", "stage_extra", "summary_extra", "comparison_extra", "receipt_extra",
    "count_bool", "count_null", "count_wrong", "money_float", "money_noncanonical", "money_negative_zero", "null_as_zero",
    "missing_hash", "missing_work", "hash_invalid", "attempt_extra", "pair_count_wrong",
])
def test_invalid_work_wire_is_rejected_before_auth(work_report_file, remote, change):
    def mutate(report):
        work = report["work_costs"]
        row = next(item for item in work["works"] if item["attempts"][0]["receipt"] is not None)
        if change == "top_extra": work["extra"] = 1
        elif change == "row_extra": row["extra"] = 1
        elif change == "stage_extra": row["stages"][0]["extra"] = 1
        elif change == "summary_extra": work["summary"]["extra"] = 1
        elif change == "comparison_extra": work["comparison"]["extra"] = 1
        elif change == "receipt_extra": row["attempts"][0]["receipt"]["extra"] = 1
        elif change == "count_bool": row["attempt_count"] = True
        elif change == "count_null": row["attempt_count"] = None
        elif change == "count_wrong": row["attempt_count"] += 1
        elif change == "money_float": row["known_partial_estimated_usd"] = 1.0
        elif change == "money_noncanonical": row["known_partial_estimated_usd"] = "1.0"
        elif change == "money_negative_zero": row["known_partial_estimated_usd"] = "-0.000000000"
        elif change == "null_as_zero": work["summary"]["estimated_total_usd"] = "0.000000000"
        elif change == "missing_hash": del report["inputs"]["work_dataset_sha256"]
        elif change == "missing_work": del report["work_costs"]
        elif change == "hash_invalid": report["inputs"]["work_dataset_sha256"] = "bad"
        elif change == "attempt_extra": row["attempts"][0]["extra"] = 1
        elif change == "pair_count_wrong": work["comparison"]["compared_pair_count"] += 1
    replace(work_report_file, mutate)
    result = sync(work_report_file, remote)
    assert result["status"] == "INCOMPLETE" and result["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls and not remote[1].initializations


@pytest.mark.parametrize("location", ["individual", "aggregate", "stage"])
def test_count_precision_boundary_is_rejected_before_network(tmp_path, remote, location):
    data = dataset()
    work = add_work(data, "large")
    value = 2**53 + 1 if location == "individual" else 2**52 + 1
    add_attempt(data, work, "large-one", usage={"input_tokens": value, "output_tokens": 0})
    if location != "individual":
        add_attempt(data, work, "large-two", usage={"input_tokens": value, "output_tokens": 0})
    report = build_report(EMPTY, work_dataset=data)
    if location == "stage":
        # Ensure preflight checks the saved stage count independently too.
        report["work_costs"]["summary"]["input_tokens"] = 0
        report["work_costs"]["summary"]["known_input_tokens"] = 0
        report["work_costs"]["works"][0]["input_tokens"] = 0
        report["work_costs"]["works"][0]["known_input_tokens"] = 0
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    result = sync(path, remote)
    assert result["error_code"] == "WORK_WANDB_COUNT_RANGE"
    assert not remote[0].calls and not remote[1].initializations


def test_exact_max_safe_integer_publishes_without_float_coercion(tmp_path, remote):
    data = dataset()
    work = add_work(data, "max-safe")
    add_attempt(data, work, "max-safe-call", usage={"input_tokens": 2**53, "output_tokens": 0})
    path = tmp_path / "report.json"
    path.write_text(json.dumps(build_report(EMPTY, work_dataset=data)))
    result = sync(path, remote)
    assert result["status"] == "READBACK_VERIFIED", result
    run = next(iter(remote[0].remote.values()))
    assert type(run.summary["work_input_tokens"]) is int
    assert run.summary["work_input_tokens"] == 2**53


def test_explicit_receipt_estimates_cannot_be_rewritten_in_derived_rows(tmp_path, remote):
    data = dataset()
    work = add_work(data, "explicit")
    add_attempt(data, work, "explicit-call", amount="1")
    report = build_report(EMPTY, work_dataset=data)
    for item in (report["work_costs"]["works"][0], report["work_costs"]["works"][0]["stages"][0],
                 report["work_costs"]["summary"]):
        item["estimated_total_usd"] = item["known_partial_estimated_usd"] = "2.000000000"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert sync(path, remote)["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls


def test_product_mismatched_pair_cannot_be_forged_as_comparable(tmp_path, remote):
    data = dataset()
    _, candidate = pair(data)
    candidate["product_ref"] = "other-product"
    report = build_report(EMPTY, work_dataset=data)
    comparison = report["work_costs"]["comparison"]
    comparison.update(compared_pair_count=1, excluded_pair_count=0, excluded_work_count=0,
        excluded_pairs=[], reason_codes=[], estimated_cost_reduction_pct="50.000000000",
        estimated_cost_difference_usd="1.000000000")
    comparison["baseline"] = dict(completed_work_count=1, total_estimated_cost_usd="2.000000000",
                                  estimated_cost_per_completed_ad_usd="2.000000000")
    comparison["candidate"] = dict(completed_work_count=1, total_estimated_cost_usd="1.000000000",
                                   estimated_cost_per_completed_ad_usd="1.000000000")
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert sync(path, remote)["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls


def test_rate_snapshot_derived_amounts_are_supported(tmp_path, remote):
    data = dataset()
    work = add_work(data, "derived")
    add_attempt(data, work, "derived-call", amount=None)
    data["rate_book"] = rate_book(**{"model-a": rate()})
    path = tmp_path / "report.json"
    path.write_text(json.dumps(build_report(EMPTY, work_dataset=data)))
    result = sync(path, remote)
    assert result["status"] == "READBACK_VERIFIED", result


def test_applied_receipt_rate_versions_cannot_be_relabelled(tmp_path, remote):
    data = dataset()
    pair(data)
    report = build_report(EMPTY, work_dataset=data)
    for work in report["work_costs"]["works"]:
        work["rate_versions"] = ["forged-rates-v2"]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert sync(path, remote)["error_code"] == "WANDB_COST_REPORT_INVALID"
    assert not remote[0].calls


@pytest.mark.parametrize("access", ["PUBLIC", "RESTRICTED", None])
def test_work_publication_keeps_private_project_requirement(work_report_file, remote, access):
    remote[0].access = access
    result = sync(work_report_file, remote)
    assert result["error_code"] == "WANDB_COST_PROJECT_PRIVACY_UNVERIFIED"
    assert remote[0].calls == ["privacy"] and not remote[1].initializations


def test_generation_and_work_both_publish_all_four_tables(tmp_path, remote):
    from test_generation_costs import fixture
    base, manifest = fixture(2)
    report = build_report(base, generation_comparison=manifest, work_dataset=work_dataset())
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    result = sync(path, remote)
    assert result["status"] == "READBACK_VERIFIED", result
    run = next(iter(remote[0].remote.values()))
    assert {item.key for item in run.artifacts if item.type == "run_table"} == {
        "comparison", "generation_cost_estimate", "work_costs", "work_stage_usage"}
    assert "generation_planned_pair_count" in run.summary
    assert "work_compared_pair_count" in run.summary


def test_work_preparation_never_touches_supplied_remote(work_report_file):
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail("Preparation contacted a remote object")
    result = bridge.sync_cost_report(work_report_file, entity="team", project="costs", sdk=Forbidden(), api=Forbidden())
    assert result["status"] == "PREPARED", result
