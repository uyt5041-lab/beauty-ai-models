import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from beauty_ai.wandb_experiments import (
    HISTORICAL_RATE_BASIS, TABLE_COLUMNS, canonical, checked_value, ensure_private_project,
    finops_summary, historical_estimate, prepare_package, sha256, summary_for, table_row,
    verify_package, validate_staged_package, verify_comparison_table,
)


def fixture_record(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    image = root / "result.png"
    Image.new("RGB", (8, 10), "blue").save(image)
    report = root / "report.json"
    report.write_text('{"example":true}')
    record = dict(record_id="test-record", family="api", experiment_group="sample", operation="edit",
                  model=HISTORICAL_RATE_BASIS["model"], status="succeeded", prompt_text="Preserve original finish.",
                  prompt_name="cosmetics", prompt_version=2, prompt_sha256="f" * 64,
                  request_hash="a" * 64, source_paths=[str(image)], output_paths=[str(image)],
                  output_sha256s=[sha256(image.read_bytes())], source_report=str(report),
                  source_report_sha256=sha256(report.read_bytes()), started_at="2026-10-22T12:00:00Z",
                  elapsed_seconds=12.4, timing_scope="internal_job_and_download", estimated_cost_usd=0.03,
                  actual_cost_usd=None, billing_status="UNRECONCILED", usage={"input_text_tokens": 10},
                  settings={"quality": "medium"}, langfuse_url=None, quality_status="PENDING",
                  review_notes="not human reviewed", data_kind="synthetic_fixture")
    return root, record


def test_package_is_allowlisted_and_preserves_unknown_billing(tmp_path):
    root, record = fixture_record(tmp_path)
    record["unrelated_env"] = "never upload"
    package = prepare_package(record, root, tmp_path / "stage")
    payload = json.loads((package["stage"] / "record.json").read_text())
    assert "unrelated_env" not in payload
    assert payload["actual_cost_usd"] is None
    assert payload["actual_cost_known"] is False
    assert payload["started_at"] == record["started_at"]
    assert payload["evaluation_status"] == "NOT_SCORED"
    assert str(root) not in canonical(payload).decode()
    assert len(list((package["stage"] / "images").iterdir())) == 1


def test_package_identity_stable_and_content_changes_detected(tmp_path):
    root, record = fixture_record(tmp_path)
    a = prepare_package(record, root, tmp_path / "a")
    b = prepare_package(record, root, tmp_path / "b")
    assert a["run_id"] == b["run_id"] and a["manifest_sha256"] == b["manifest_sha256"]
    record["prompt_text"] = "A different historical snapshot"
    c = prepare_package(record, root, tmp_path / "c")
    assert c["run_id"] == a["run_id"] and c["manifest_sha256"] != a["manifest_sha256"]


@pytest.mark.parametrize("kind", ["image", "report"])
def test_tampered_evidence_rejected(tmp_path, kind):
    root, record = fixture_record(tmp_path)
    record["output_sha256s" if kind == "image" else "source_report_sha256"] = ["0" * 64] if kind == "image" else "0" * 64
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        prepare_package(record, root, tmp_path / "stage")


def test_path_escape_and_symlink_rejected(tmp_path):
    root, record = fixture_record(tmp_path)
    outside = tmp_path / "outside.png"
    Image.new("RGB", (8, 10)).save(outside)
    (root / "escape.png").symlink_to(outside)
    record["source_paths"] = [str(root / "escape.png")]
    with pytest.raises(ValueError, match="OUTSIDE_ROOT"):
        prepare_package(record, root, tmp_path / "stage")


@pytest.mark.parametrize("value", [{"api_key": "placeholder"}, "sk-" + "x" * 40, float("nan")])
def test_secret_and_nonfinite_values_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        checked_value(value, tmp_path)


def test_saved_estimate_wins_and_reconstruction_has_provenance():
    usage = {"input_text_tokens": 92, "input_image_tokens": 0, "output_tokens": 191}
    record = dict(family="api", model=HISTORICAL_RATE_BASIS["model"], usage=usage)
    value, origin, basis = historical_estimate(record)
    assert value == 0.00619 and origin == "RECONSTRUCTED_WITH_RECORDED_B12_RATES"
    assert basis["snapshot_date"] == "2026-10-23"
    record["estimated_cost_usd"] = 0.123
    assert historical_estimate(record) == (0.123, "RECORDED_IN_SOURCE", None)


@pytest.mark.parametrize("change", [{"model": "unknown"}, {"family": "local_model"}, {"usage": {}}, {"usage": {"input_text_tokens": -1, "input_image_tokens": 1, "output_tokens": 1}}])
def test_unknown_rate_or_usage_stays_unknown(change):
    r = dict(family="api", model=HISTORICAL_RATE_BASIS["model"], usage={"input_text_tokens": 1, "input_image_tokens": 1, "output_tokens": 1})
    r.update(change)
    assert historical_estimate(r) == (None, "UNKNOWN", None)


def test_finops_does_not_subtract_partial_experiments_from_shared_budget(tmp_path):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    f = finops_summary([p])
    assert f["estimated_api_cost_usd"] == 0.03
    for k in ("recorded_actual_api_cost_usd", "team_budget_remaining_usd", "team_budget_utilization_pct", "actual_cost_per_accepted_final_ad_usd", "finops_score", "production_waste_ratio_pct"):
        assert f[k] is None
    assert f["billing_reconciliation_coverage_pct"] == 0
    assert f["release_weight_finops_pct"] == 40
    assert f["accepted_final_ad_count"] == 0


def test_table_preserves_unknown_cost_and_exact_prompt(tmp_path):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    sdk = SimpleNamespace(Image=lambda path: {"image": Path(path).name})
    row = dict(zip(TABLE_COLUMNS, table_row(p, sdk, "https://example.invalid/run"), strict=True))
    assert row["actual_cost_usd"] is None
    assert row["prompt_version"] == "2" and row["prompt_text"] == record["prompt_text"]
    assert row["original_started_at"] == record["started_at"]


def test_failure_without_output_is_a_valid_record(tmp_path):
    root, record = fixture_record(tmp_path)
    record.update(family="local_model", status="failed", source_paths=[], output_paths=[], output_sha256s=[],
                  estimated_cost_usd=None, actual_cost_usd=None, prompt_text=None)
    p = prepare_package(record, root, tmp_path / "stage")
    assert p["payload"]["images"] == []
    assert summary_for(p)["estimated_cost_known"] is False
    assert "actual_cost_usd" not in summary_for(p)


def test_existing_public_project_is_not_silently_changed():
    calls = []
    def execute(query, variables):
        calls.append(query)
        return {"project": {"name": "existing", "access": "PUBLIC"}}
    api = SimpleNamespace(_service_api=SimpleNamespace(execute_graphql=execute))
    with pytest.raises(ValueError, match="NOT_PRIVATE"):
        ensure_private_project(api, "team", "existing")
    assert len(calls) == 1 and calls[0].startswith("query")


def test_remote_readback_checks_bytes_not_only_status(tmp_path):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    artifact = SimpleNamespace(type="image-experiment", metadata={"manifest_sha256": p["manifest_sha256"]},
                               download=lambda root: str(p["stage"]), qualified_name="team/proj/test:v0")
    remote = SimpleNamespace(summary=summary_for(p), logged_artifacts=lambda: [artifact], url="https://example.invalid/run")
    api = SimpleNamespace(run=lambda path: remote)
    assert verify_package(api, p, "team/proj/run", tmp_path)["status"] == "READBACK_VERIFIED"
    image = next((p["stage"] / "images").iterdir())
    image.write_bytes(b"changed remote content")
    with pytest.raises(ValueError, match="REMOTE_IMAGE_MISMATCH"):
        verify_package(api, p, "team/proj/run", tmp_path)


def test_staged_allowlist_excludes_added_files(tmp_path):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    (p["stage"] / ".env").write_text("do not publish")
    names = validate_staged_package(p)
    assert ".env" not in names and "record.json" in names and "prompt.txt" in names
    assert len(names) == 3


@pytest.mark.parametrize("kind", ["record", "prompt", "image", "symlink"])
def test_staged_changes_are_blocked_before_upload(tmp_path, kind):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    path = p["stage"] / ({"record": "record.json", "prompt": "prompt.txt"}.get(kind) or p["payload"]["images"][0]["path"])
    if kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="MISMATCH|OUTSIDE_ROOT"):
        validate_staged_package(p)


def test_original_input_hash_is_rechecked(tmp_path):
    root, record = fixture_record(tmp_path)
    record["data_provenance"] = {"source_sha256s": ["0" * 64]}
    with pytest.raises(ValueError, match="INPUT_HASH_MISMATCH"):
        prepare_package(record, root, tmp_path / "stage")


@pytest.mark.parametrize("change", [None, "cost", "prompt", "null", "image"])
def test_remote_table_values_and_pixels_are_verified(tmp_path, change):
    root, record = fixture_record(tmp_path)
    p = prepare_package(record, root, tmp_path / "stage")
    sdk = SimpleNamespace(Image=lambda path: SimpleNamespace(_path=path))
    urls = {p["record_id"]: "https://example.invalid/run"}
    row = table_row(p, sdk, urls[p["record_id"]])
    if change in {"cost", "prompt", "null"}:
        col = {"cost": "estimated_cost_usd", "prompt": "prompt_text", "null": "actual_cost_usd"}[change]
        row[TABLE_COLUMNS.index(col)] = "changed"
    elif change == "image":
        different = tmp_path / "different.png"
        Image.new("RGB", (8, 10), "red").save(different)
        row[TABLE_COLUMNS.index("result")] = SimpleNamespace(_path=str(different))
    table = SimpleNamespace(columns=TABLE_COLUMNS, data=[row])
    if change:
        with pytest.raises(ValueError, match="MISMATCH"):
            verify_comparison_table(table, [p], urls)
    else:
        verify_comparison_table(table, [p], urls)
