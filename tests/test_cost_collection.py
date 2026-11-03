"""Synthetic saved evidence only: no credentials, provider imports or API calls."""
from copy import deepcopy
from decimal import localcontext
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from beauty_ai.cost_accounting import canonical_bytes
from beauty_ai.cost_collection import (HISTORICAL_RATE_VERSION, MAX_JSON_BYTES,
    collect_attempt_snapshot, collect_dataset, collect_generation_results, main)
from beauty_ai.wandb_experiments import HISTORICAL_RATE_BASIS


MODEL = HISTORICAL_RATE_BASIS["model"]
USAGE = {"input_tokens": 3955, "output_tokens": 418, "total_tokens": 4373,
         "input_image_tokens": 2904, "input_text_tokens": 1051}


def save(root, case, report):
    path = root / case / "generation-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def preset(attempt="attempt-1", *, usage=None):
    return {"status": "SUCCEEDED", "model": MODEL, "actual_billed_cost_usd": None,
            "image_sha256": "a" * 64, "receipt": {
                "attempt_id": attempt, "state": "succeeded", "model": MODEL,
                "usage": deepcopy(USAGE if usage is None else usage), "actual_cost_usd": None,
                "billing_status": "UNRECONCILED"}}


def create_db(path, rows, *, modern=True, wal=False):
    db = sqlite3.connect(path)
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("""CREATE TABLE provider_attempts (
        job_id TEXT PRIMARY KEY, project_id TEXT, request_hash TEXT, attempt_id TEXT, state TEXT,
        reservation_units INTEGER, provider_request_id TEXT, usage_json TEXT, result_json TEXT,
        error_code TEXT, created_at TEXT, updated_at TEXT)""")
    if modern:
        for name, kind in (("raw_usage_json", "TEXT"), ("metadata_json", "TEXT"),
                           ("elapsed_seconds", "REAL"), ("account_reservation_id", "TEXT")):
            db.execute(f"ALTER TABLE provider_attempts ADD COLUMN {name} {kind}")
    for row in rows:
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        db.execute(f"INSERT INTO provider_attempts ({columns}) VALUES ({placeholders})", tuple(row.values()))
    db.commit()
    return db


def row(attempt="attempt-1", state="succeeded", **kwargs):
    return {"job_id": "job-" + attempt, "project_id": "project-1", "request_hash": "b" * 64,
            "attempt_id": attempt, "state": state, "reservation_units": 1000000,
            "provider_request_id": "request-1", "usage_json": json.dumps(USAGE),
            "result_json": None, "error_code": None, "created_at": "2026-11-03T01:00:00Z",
            "updated_at": "2026-11-03T01:01:00Z", **kwargs}


def test_three_historical_formats_preserve_unknown_and_source_bytes(tmp_path):
    a = save(tmp_path / "preset", "P01-1", preset())
    save(tmp_path / "revision", "P01-1", preset("attempt-2"))
    save(tmp_path / "freeform", "T1", {"case_id": "T1", "status": "SUCCEEDED",
        "model_requested": "gpt-image-2", "usage_tokens": None, "actual_cost_usd": None,
        "output_path": "/private/local/user/image.png"})
    save(tmp_path / "freeform" / "nested", "ignored", preset("ignored"))
    dataset = collect_dataset(generation_roots=[("preset", tmp_path / "preset"),
        ("revision", tmp_path / "revision"), ("freeform", tmp_path / "freeform")])
    assert set(dataset) == {"schema_version", "cost_items", "batches", "acceptances"}
    items = dataset["cost_items"]
    assert len(items) == 3
    first = next(i for i in items if i["cost_id"] == "attempt:attempt-1")
    assert first["source_sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert first["estimated_cost_usd"] == "0.041027000"
    assert first["rate_version"] == HISTORICAL_RATE_VERSION
    legacy = next(i for i in items if i["cost_id"].startswith("legacy:"))
    assert legacy["cost_id"] == "legacy:freeform/T1"
    assert legacy["estimated_cost_usd"] is legacy["actual_cost_usd"] is legacy["rate_version"] is None
    assert all(i["category"] == "research" and i["allocations"] == {} for i in items)
    assert dataset["batches"] == dataset["acceptances"] == []
    assert "/private/" not in json.dumps(dataset)


def test_requested_model_is_not_confirmed_rate_evidence(tmp_path):
    save(tmp_path, "T1", {"model_requested": MODEL, "usage_tokens": USAGE, "status": "SUCCEEDED"})
    assert collect_generation_results(tmp_path, "request-only")[0]["estimated_cost_usd"] is None


def test_recorded_estimate_without_snapshot_basis_is_not_adopted(tmp_path):
    report = preset()
    report["estimated_cost_usd"] = "0.0001"
    save(tmp_path, "P1", report)
    item = collect_generation_results(tmp_path, "source")[0]
    assert item["estimated_cost_usd"] is item["rate_version"] is None


@pytest.mark.parametrize("status,expected", [("succeeded", "SUCCEEDED"), ("failed", "FAILED"),
    ("submitting", "SUBMITTING"), ("unknown", "UNKNOWN"), ("future-state", "UNKNOWN")])
def test_state_never_excludes_attempt_or_invents_zero(tmp_path, status, expected):
    report = preset()
    report["receipt"]["state"] = status
    report["receipt"]["usage"] = None
    save(tmp_path, "P1", report)
    item = collect_generation_results(tmp_path, "source")[0]
    assert item["state"] == expected and item["actual_cost_usd"] is None
    assert item["estimated_cost_usd"] is None


def test_actual_requires_explicit_billing_proof_and_preserves_partial(tmp_path):
    report = preset()
    report["receipt"].update(actual_cost_usd="0.012345678", billing_status="RECONCILED")
    save(tmp_path, "P1", report)
    unproven = collect_generation_results(tmp_path, "source")[0]
    assert unproven["actual_cost_usd"] is None and unproven["billing_status"] == "UNRECONCILED"
    report["receipt"].update(billing_evidence_ref="invoice:line-1", billing_status="PARTIAL")
    save(tmp_path, "P1", report)
    proven = collect_generation_results(tmp_path, "source")[0]
    assert proven["actual_cost_usd"] == "0.012345678" and proven["billing_status"] == "PARTIAL"
    report["receipt"]["actual_cost_usd"] = 0.012345678
    save(tmp_path, "P1", report)
    with pytest.raises(ValueError):
        collect_generation_results(tmp_path, "source")


def test_production_cannot_be_assigned_implicitly(tmp_path):
    with pytest.raises(ValueError, match="EXPLICIT_ALLOCATION_REQUIRED"):
        collect_generation_results(tmp_path, "source", category="production")


def test_old_schema_readonly_has_all_states_without_migration(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = create_db(path, [row(str(i), state=s) for i, s in enumerate(
        ["submitting", "unknown", "failed", "succeeded"])], modern=False)
    db.close()
    before = path.read_bytes()
    items = collect_attempt_snapshot(path, "old")
    assert {item["state"] for item in items} == {"SUBMITTING", "UNKNOWN", "FAILED", "SUCCEEDED"}
    assert all(item["estimated_cost_usd"] is None for item in items)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as check:
        assert "metadata_json" not in {r[1] for r in check.execute("PRAGMA table_info(provider_attempts)")}
        assert {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"provider_attempts"}


def test_wal_committed_snapshot_is_read_without_writes_or_uncommitted_rows(tmp_path):
    path = tmp_path / "live.sqlite3"
    db = create_db(path, [row(state="submitting", metadata_json=json.dumps({"model": MODEL}))], wal=True)
    try:
        wal = Path(str(path) + "-wal")
        before = (path.read_bytes(), wal.read_bytes())
        db.execute("UPDATE provider_attempts SET state='succeeded'")
        first = collect_attempt_snapshot(path, "live")
        assert first[0]["state"] == "SUBMITTING"
        assert (path.read_bytes(), wal.read_bytes()) == before
        db.rollback()
        again = collect_attempt_snapshot(path, "another-alias")
        assert again[0]["source_sha256"] == first[0]["source_sha256"]
        assert first[0]["estimated_cost_usd"] == "0.041027000"
    finally:
        db.close()


def test_b2_provider_receipt_and_db_same_attempt_deduplicate(tmp_path):
    saved = row(metadata_json=json.dumps({"model": MODEL}), raw_usage_json=json.dumps({"output_tokens": 418}),
                elapsed_seconds=1.5, account_reservation_id="reservation-1")
    path = tmp_path / "attempts.sqlite3"
    db = create_db(path, [saved])
    db.close()
    receipt = {key: value for key, value in saved.items() if not key.endswith("_json") and key != "reservation_units"}
    for key in ("metadata", "usage", "raw_usage", "result"):
        receipt[key] = json.loads(saved[key + "_json"]) if saved[key + "_json"] is not None else None
    receipt["reservation_usd"] = "1.000000"
    save(tmp_path / "reports", "case-1", {"schema_version": "beautyai-freeform-execution-v1",
        "status": "SUCCEEDED", "job_id": saved["job_id"], "request_sha256": saved["request_hash"],
        "provider_receipt": receipt})
    kwargs = {"generation_roots": [("report", tmp_path / "reports")], "attempt_dbs": [("db", path)]}
    dataset = collect_dataset(**kwargs)
    assert len(dataset["cost_items"]) == 1
    assert dataset["cost_items"][0]["source_ref"].startswith("attempts:db/")
    assert dataset == collect_dataset(generation_roots=kwargs["generation_roots"] * 2,
                                      attempt_dbs=kwargs["attempt_dbs"] * 2)
    with localcontext() as context:
        context.prec = 2
        assert dataset == collect_dataset(**kwargs)


def test_duplicate_equal_cost_but_changed_tokens_or_request_is_conflict(tmp_path):
    a, b = tmp_path / "one", tmp_path / "two"
    save(a, "P1", preset())
    changed = preset()
    # Equal price: +8 text tokens and -5 image tokens; observations still differ.
    changed["receipt"]["usage"].update(input_text_tokens=1059, input_image_tokens=2899)
    save(b, "P2", changed)
    with pytest.raises(ValueError, match="ATTEMPT_CONFLICT"):
        collect_dataset(generation_roots=[("one", a), ("two", b)])


def test_changed_sqlite_snapshots_reject_instead_of_choosing_latest(tmp_path):
    paths = [tmp_path / "one.sqlite3", tmp_path / "two.sqlite3"]
    for path, status in zip(paths, ["submitting", "succeeded"]):
        create_db(path, [row(state=status)]).close()
    with pytest.raises(ValueError, match="ATTEMPT_CONFLICT"):
        collect_dataset(attempt_dbs=[("one", paths[0]), ("two", paths[1])])


def test_no_attempt_id_does_not_infer_duplicate_call_across_groups(tmp_path):
    report = {"model": MODEL, "usage": USAGE, "status": "SUCCEEDED"}
    save(tmp_path, "P1", report)
    items = collect_dataset(generation_roots=[("one", tmp_path), ("two", tmp_path)])["cost_items"]
    assert len(items) == 2 and all(i["cost_id"].startswith("legacy:") for i in items)


@pytest.mark.parametrize("payload", ['{"status":"SUCCEEDED","status":"FAILED"}',
    '{"usage":NaN}', '{"api_key":"should-not-appear"}',
    '{"receipt":{"usage":{"input_tokens":true}}}',
    '{"receipt":{"usage":{"input_tokens":-1}}}', '{"receipt":"bad"}'])
def test_invalid_or_secret_json_is_rejected_with_safe_error(tmp_path, payload):
    path = save(tmp_path, "case", {})
    path.write_text(payload)
    with pytest.raises(ValueError) as exc:
        collect_generation_results(tmp_path, "source")
    assert str(exc.value).startswith("COST_COLLECTION_") and "should-not-appear" not in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


def test_bounded_json_and_containment(tmp_path):
    root = tmp_path / "root"
    path = save(root, "case", {})
    path.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="JSON_LIMIT"):
        collect_generation_results(root, "source")
    nested = 0
    for _ in range(20):
        nested = {"child": nested}
    path.write_text(json.dumps(nested))
    with pytest.raises(ValueError, match="JSON_LIMIT"):
        collect_generation_results(root, "source")
    path.unlink()
    external = tmp_path / "external.json"
    external.write_text("{}")
    path.symlink_to(external)
    with pytest.raises(ValueError, match="SOURCE_OUTSIDE_ROOT"):
        collect_generation_results(root, "source")


def test_database_bad_json_and_absent_path_do_not_create_or_expose(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        collect_attempt_snapshot(missing, "source")
    assert not missing.exists()
    dbpath = tmp_path / "malformed.sqlite3"
    create_db(dbpath, [row(usage_json='{"input_tokens":NaN}')]).close()
    with pytest.raises(ValueError, match="JSON_INVALID"):
        collect_attempt_snapshot(dbpath, "source")


def test_cli_writes_empty_editable_examples_and_safe_inventory(tmp_path, capsys):
    root = tmp_path / "input"
    save(root, "P1", preset())
    output = tmp_path / "output" / "dataset.json"
    assert main(["--generation-root", f"source={root}", "--output", str(output)]) == 0
    dataset = json.loads(output.read_text())
    assert dataset["acceptances"] == dataset["batches"] == []
    assert len(dataset["cost_items"]) == 1
    examples = json.loads(output.with_name("dataset.comparison-example.json").read_text())
    assert examples["status"] == "TEMPLATE_NOT_EVALUATED"
    assert examples["comparison"]["pairs"] == []
    assert str(tmp_path) not in output.with_name("dataset.sources.json").read_text()
    assert "new_provider_calls" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["--generation-root", f"source={root}", "--output", str(root / "dataset.json")])
    assert exc.value.code == 2


def test_import_and_collection_do_not_load_provider_modules():
    script = """import sys
from beauty_ai import cost_collection
assert not any(name in sys.modules for name in ('httpx', 'openai', 'wandb',
    'beauty_ai.openai_images', 'beauty_ai.provider_attempts', 'beauty_ai.api_image_processor'))
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_same_attempt_with_conflicting_project_provenance_is_rejected(tmp_path):
    paths = [tmp_path / "one.sqlite3", tmp_path / "two.sqlite3"]
    for path, project in zip(paths, ("project-one", "project-two")):
        create_db(path, [row(project_id=project)]).close()
    with pytest.raises(ValueError, match="ATTEMPT_CONFLICT"):
        collect_dataset(attempt_dbs=[("one", paths[0]), ("two", paths[1])])
