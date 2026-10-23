"""Offline historical evidence tests. No provider, model or telemetry calls."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import UUID, uuid4

from PIL import Image
import pytest

from beauty_ai.experiment_records import collect_experiments


MODEL = "gpt-image-2.5-sunburst-2026-09-08"
START = "2026-10-23T01:00:00+00:00"
END = "2026-10-23T01:00:02+00:00"
PROJECT = str(UUID(int=1))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def asset(run, color="red"):
    asset_id = str(uuid4())
    path = run / "runtime/assets/files" / UUID(PROJECT).hex / (UUID(asset_id).hex + ".png")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (2, 2), color).save(path)
    return {"asset_id": asset_id, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "width": 2, "height": 2, "mime_type": "image/png"}, path


def save_job(run, request, result, *, job_id=None, status="succeeded", error=None):
    job_id = job_id or str(uuid4())
    path = run / "runtime/jobs.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT,project_id TEXT,request_hash TEXT,"
                   "request_json TEXT,status TEXT,result_json TEXT,error_code TEXT,created_at TEXT,updated_at TEXT)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                   (job_id, PROJECT, digest(request), json.dumps(request), status,
                    json.dumps(result) if result is not None else None, error, START, END))
    return job_id


def api_run(root, *, name="run-20261023T010000Z-aaaaaaaa", operation="edit", managed=True,
            ledger=True, cost=0.01, job_id=None):
    run = root / "outputs/cosmetics-comparison" / name
    source, source_path = asset(run, "blue")
    output, output_path = asset(run)
    request = {"kind": "api_image", "operation": operation, "model": MODEL,
               "product_images": [source] if operation != "generate" else [], "reference_images": [],
               "width": 2, "height": 2, "quality": "low"}
    if managed:
        text = "Historical cosmetic prompt v1."
        request["managed_prompt"] = {"name": "beautyai/cosmetics/test", "version": 1,
                                     "rendered_text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        (run / "provider-prompt.txt").write_text(text)
    result = {"kind": "api_image", "operation": operation, "image": output, "model": MODEL}
    job_id = job_id or str(uuid4())
    if ledger:
        save_job(run, request, result, job_id=job_id)
    write_json(run / "request.json", request)
    shutil.copyfile(source_path, run / "source.png")
    shutil.copyfile(output_path, run / "image.png")
    receipt = {"job_id": job_id, "request_hash": digest(request), "result": result,
               "usage": {"input_tokens": 10, "output_tokens": 5}, "created_at": START, "updated_at": END,
               "actual_cost_usd": None, "billing_status": "UNRECONCILED"}
    report = {"mode": "LIVE_USER_AUTHORIZED", "http_post_attempts": 1, "status": "SUCCEEDED",
              "job_id": job_id, "request_hash": digest(request), "provider_receipt": receipt,
              "elapsed_seconds": 2.5, "started_at": START, "human_review": "PENDING",
              "estimated_cost_usd": cost, "image_path": str(run / "image.png")}
    if operation != "generate":
        report["source_sha256"] = source["sha256"]
    write_json(run / "report.json", report)
    return run, request, report


def local_run(root, *, name="b7-model-smoke", status="succeeded", job_id=None):
    run = root / "outputs" / name
    source, source_path = asset(run, "blue")
    output, _ = asset(run)
    mask, _ = asset(run, "white")
    request = {"kind": "render_background", "pipeline_profile_id": "sdxl-inpaint",
               "pipeline_revision": "historical-revision", "source": source,
               "image_prompt": {"positive": "Historical flowers prompt, not current cosmetics template.",
                                "negative": "old negative", "prompt_version": "host-authored-bright-flowers-1"},
               "seed": 42, "width": 512, "height": 512}
    result = {"kind": "render_background", "scene_underlay": output, "product_rgba": source,
              "composed_preview": mask} if status == "succeeded" else None
    job_id = save_job(run, request, result, job_id=job_id, status=status,
                      error="MODEL_UNAVAILABLE" if status == "failed" else None)
    shutil.copyfile(source_path, run / "synthetic-product.png")
    report = {"model_invoked": True, "device": "mps", "input": "synthetic_drawn_fixture",
              "quality_approval": "NOT_ASSESSED", "jobs": [{"job_id": job_id,
              "estimated_cost_usd": 1, "actual_cost_usd": 1,
              "ledger_timing": {"ledger_elapsed_ms": 2000}}]}
    write_json(run / "receipt.json", report)
    return run, request, report


def update_report(run, transform):
    path = run / "report.json"
    report = json.loads(path.read_text())
    transform(report)
    write_json(path, report)


def test_managed_history_is_not_replaced_by_current_or_remote_templates(tmp_path):
    run, request, _ = api_run(tmp_path)
    write_json(run / "remote-prompt.json", {"version": 999, "prompt": "Later changed prompt"})
    write_json(tmp_path / "beauty_ai/prompts/current.json", {"prompt": "Current template"})
    record, = collect_experiments(tmp_path)
    assert record["prompt_text"] == request["managed_prompt"]["rendered_text"]
    assert record["prompt_version"] == 1
    assert record["prompt_name"] == "beautyai/cosmetics/test"
    assert record["estimated_cost_usd"] == 0.01 and record["actual_cost_usd"] is None
    assert record["billing_status"] == "UNRECONCILED"
    assert record["cost_category"] == "research_experiment"
    assert record["quality_status"] == "PENDING" and record["data_kind"] == "unknown"
    assert record["elapsed_seconds"] == 2.5
    assert record["timing_scope"] == "client_submit_poll_elapsed_seconds"
    assert record["source_report_sha256"] == hashlib.sha256((run / "report.json").read_bytes()).hexdigest()
    assert all(Path(p).is_absolute() for p in record["source_paths"] + record["output_paths"])
    assert collect_experiments(tmp_path)[0]["record_id"] == record["record_id"]
    json.dumps(record, allow_nan=False)


def test_cutout_missing_prompt_and_cost_remain_null(tmp_path):
    api_run(tmp_path, operation="cutout", managed=False, cost=None)
    record, = collect_experiments(tmp_path)
    assert all(record[k] is None for k in ("prompt_text", "prompt_name", "prompt_version", "prompt_sha256",
                                          "actual_cost_usd", "estimated_cost_usd"))
    assert record["data_provenance"]["prompt_scope"] == "not_recorded"


def test_exact_saved_request_and_receipt_support_run_without_runtime_ledger(tmp_path):
    run, _, _ = api_run(tmp_path, ledger=False)
    shutil.rmtree(run / "runtime")
    record, = collect_experiments(tmp_path)
    assert record["output_paths"] == [str(run / "image.png")]
    assert record["source_paths"] == [str(run / "source.png")]
    assert record["status"] == "succeeded"


def test_local_failed_attempt_is_recorded_without_output_or_invented_cost(tmp_path):
    local_run(tmp_path, name="b7-model-smoke-attempt1", status="failed")
    record, = collect_experiments(tmp_path)
    assert record["status"] == "failed" and record["error_code"] == "MODEL_UNAVAILABLE"
    assert record["output_paths"] == record["output_sha256s"] == []
    assert record["estimated_cost_usd"] is None and record["actual_cost_usd"] is None
    assert record["billing_status"] == "NOT_APPLICABLE_LOCAL_BILLING"
    assert record["elapsed_seconds"] == 2 and record["started_at"] == START
    assert record["timing_scope"] == "ledger_created_to_updated_wall_clock_seconds"
    assert record["data_kind"] == "synthetic_fixture"
    assert record["prompt_version"] == "host-authored-bright-flowers-1"
    assert "flowers" in record["prompt_text"]
    assert record["settings"]["negative_prompt"] == "old negative"


def test_local_code_snapshot_hash_is_not_treated_as_input_image_hash(tmp_path):
    run, _, report = local_run(tmp_path, name="b9-model-benchmark")
    report["source_sha256"] = {"beauty_ai/sdxl_processor.py": "a" * 64}
    write_json(run / "receipt.json", report)
    record, = collect_experiments(tmp_path)
    assert record["data_provenance"]["code_snapshot_sha256s"] == report["source_sha256"]
    assert len(record["output_paths"]) == 3


@pytest.mark.parametrize("missing", ["positive", "prompt_version"])
def test_partial_historical_prompt_keeps_absent_field_unknown(tmp_path, missing):
    run, request, _ = local_run(tmp_path)
    del request["image_prompt"][missing]
    with sqlite3.connect(run / "runtime/jobs.sqlite3") as db:
        db.execute("UPDATE jobs SET request_json=?,request_hash=?", (json.dumps(request), digest(request)))
    record, = collect_experiments(tmp_path)
    if missing == "positive":
        assert record["prompt_text"] is None and record["prompt_sha256"] is None
        assert record["prompt_version"] == "host-authored-bright-flowers-1"
    else:
        assert record["prompt_version"] is None and record["prompt_text"] is not None


def test_copy_of_same_job_deduplicates_but_real_repeat_with_same_png_remains(tmp_path):
    run, _, _ = local_run(tmp_path)
    shutil.copytree(run, tmp_path / "outputs/b7-model-smoke-attempt1")
    assert len(collect_experiments(tmp_path)) == 1
    repeat, _, _ = local_run(tmp_path, name="b7-model-smoke-attempt2")
    records = collect_experiments(tmp_path)
    assert len(records) == 2 and records[0]["record_id"] != records[1]["record_id"]
    assert records[0]["output_sha256s"] == records[1]["output_sha256s"]


def test_conflicting_reused_job_id_is_rejected(tmp_path):
    _, _, report = local_run(tmp_path)
    local_run(tmp_path, name="b7-model-smoke-attempt2", job_id=report["jobs"][0]["job_id"], status="failed")
    with pytest.raises(ValueError, match="^EXPERIMENT_CONFLICTING_DUPLICATE$"):
        collect_experiments(tmp_path)


def test_new_valid_run_is_discovered_but_mock_backups_and_loose_pngs_are_not(tmp_path):
    run, _, _ = api_run(tmp_path, name="run-20270101T020304Z-1234abcd")
    shutil.copytree(run, tmp_path / "outputs/relay-backup-20270101")
    shutil.copytree(run, tmp_path / "outputs/cosmetics-prompts/run-preview")
    shutil.copyfile(run / "image.png", tmp_path / "outputs/copied.png")
    mock, _, _ = api_run(tmp_path, name="run-20270101T020305Z-1234abcd")
    update_report(mock, lambda r: r.update(mode="MOCK", http_post_attempts=0))
    records = collect_experiments(tmp_path)
    assert len(records) == 1 and records[0]["experiment_group"].endswith(run.name)


@pytest.mark.parametrize("path_kind", ["outside", "parent", "symlink", "relative"])
def test_report_paths_cannot_escape_and_errors_do_not_echo_input(tmp_path, path_kind):
    run, _, _ = api_run(tmp_path)
    if path_kind == "outside":
        path = str(tmp_path.parent / "sensitive-secret.png")
    elif path_kind == "parent":
        path = str(run / ".." / run.name / "image.png")
    elif path_kind == "symlink":
        (run / "link.png").symlink_to(run / "image.png")
        path = str(run / "link.png")
    else:
        path = "sensitive-secret.png"
    update_report(run, lambda r: r.update(image_path=path))
    with pytest.raises(ValueError, match="^EXPERIMENT_UNSAFE_PATH$") as caught:
        collect_experiments(tmp_path)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("what", ["asset", "request", "prompt", "receipt"])
def test_changed_evidence_is_rejected(tmp_path, what):
    run, request, _ = api_run(tmp_path)
    if what == "asset":
        (run / "image.png").write_bytes(b"tampered")
        expected = "EXPERIMENT_ASSET_HASH_MISMATCH"
    elif what == "request":
        with sqlite3.connect(run / "runtime/jobs.sqlite3") as db:
            db.execute("UPDATE jobs SET request_json=?", ('{"kind":"secret-tampering"}',))
        expected = "EXPERIMENT_REQUEST_HASH_MISMATCH"
    elif what == "prompt":
        (run / "provider-prompt.txt").write_text("changed after the actual call")
        expected = "EXPERIMENT_PROMPT_HASH_MISMATCH"
    else:
        update_report(run, lambda r: r["provider_receipt"].update(job_id=str(uuid4())))
        expected = "EXPERIMENT_RECEIPT_MISMATCH"
    with pytest.raises(ValueError, match="^" + expected + "$"):
        collect_experiments(tmp_path)


def test_missing_input_is_safe_error(tmp_path):
    run, request, _ = api_run(tmp_path)
    source = request["product_images"][0]
    (run / "runtime/assets/files" / UUID(PROJECT).hex / (UUID(source["asset_id"]).hex + ".png")).unlink()
    with pytest.raises(ValueError, match="^EXPERIMENT_MISSING_FILE$"):
        collect_experiments(tmp_path)


def test_uncheckpointed_ledger_is_not_silently_read_as_complete_history(tmp_path):
    run, _, _ = api_run(tmp_path)
    wal = run / "runtime/jobs.sqlite3-wal"
    wal.write_bytes(b"uncommitted historical bytes")
    with pytest.raises(ValueError, match="^EXPERIMENT_UNCHECKPOINTED_LEDGER$"):
        collect_experiments(tmp_path)
    assert wal.read_bytes() == b"uncommitted historical bytes"


@pytest.mark.parametrize("payload,code", [('{"mode":1,"mode":2}', "EXPERIMENT_DUPLICATE_JSON_KEY"),
                                         ('{"bad":NaN}', "EXPERIMENT_INVALID_EVIDENCE"),
                                         ('[]', "EXPERIMENT_INVALID_EVIDENCE"),
                                         ('secret malformed', "EXPERIMENT_INVALID_EVIDENCE")])
def test_malformed_or_ambiguous_json_is_rejected_safely(tmp_path, payload, code):
    run, _, _ = api_run(tmp_path)
    (run / "report.json").write_text(payload)
    with pytest.raises(ValueError, match="^" + code + "$"):
        collect_experiments(tmp_path)


def test_observability_url_rejects_credentials_or_unapproved_hosts(tmp_path):
    run, _, _ = api_run(tmp_path)
    update_report(run, lambda r: r.update(langfuse={"trace_url": "https://secret@example.test/private"}))
    with pytest.raises(ValueError, match="^EXPERIMENT_UNSAFE_URL$"):
        collect_experiments(tmp_path)


def test_collector_has_no_network_or_model_side_effects(tmp_path, monkeypatch):
    import socket
    run, _, _ = api_run(tmp_path)
    def deny(*args, **kwargs):
        raise AssertionError("Network is forbidden")
    monkeypatch.setattr(socket, "socket", deny)
    before = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in tmp_path.rglob("*") if p.is_file()}
    assert len(collect_experiments(tmp_path)) == 1
    after = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
