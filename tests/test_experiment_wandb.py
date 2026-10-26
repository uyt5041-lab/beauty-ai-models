"""Offline W&B bridge contracts. No image provider or remote W&B traffic."""
import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from beauty_ai import experiment_wandb as bridge, experiment_runner
from beauty_ai.wandb_experiments import historical_estimate, finops_summary, HISTORICAL_RATE_BASIS


@pytest.fixture
def trial(tmp_path, monkeypatch):
    root = tmp_path / "outputs" / "experiment-trials" / "saved"
    root.mkdir(parents=True)
    plan = root / "plan.json"
    plan.write_text('{}')
    report = root / "report.json"
    report.write_text('{"status":"SUCCEEDED","actual_cost_usd":null}')
    image = root / "image.png"
    Image.new("RGB", (16, 20), "blue").save(image)
    record = dict(record_id="test-new-trial", family="api", experiment_group="saved", operation="edit",
        model=HISTORICAL_RATE_BASIS["model"], status="succeeded", error_code=None,
        prompt_text="Preserve the original matte product finish.", prompt_name="cosmetics/vivid_pop",
        prompt_version="cosmetics-v1", prompt_sha256="a" * 64, request_hash="b" * 64,
        source_paths=[str(image)], output_paths=[str(image)], output_sha256s=[hashlib.sha256(image.read_bytes()).hexdigest()],
        source_report=str(report), source_report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
        started_at="2026-10-23T00:00:00+00:00", elapsed_seconds=3.2, timing_scope="recorded",
        estimated_cost_usd=None, actual_cost_usd=None, billing_status="UNRECONCILED",
        usage={"input_text_tokens":10,"input_image_tokens":5,"output_tokens":7}, settings={},
        quality_status="NOT_APPROVED", data_kind="user_supplied_unreviewed", review_notes="Pending",
        data_provenance={"execution_schema":"beautyai-experiment-execution-v1",
            "source_sha256s":[hashlib.sha256(image.read_bytes()).hexdigest()]})
    monkeypatch.setattr(bridge, "collect_trial", lambda *a, **k: [record])
    return plan, record


def sync(plan, **kwargs):
    return bridge.sync_trial(plan, expected_sha256="d" * 64, entity="example-team", **kwargs)


def test_offline_package_preserves_unknown_cost_and_source(trial, monkeypatch):
    plan, record = trial
    before = (plan.parent / "report.json").read_bytes()
    monkeypatch.setattr(bridge, "publish_packages", lambda *a, **k: pytest.fail("Offline preparation published"))
    receipt = sync(plan)
    assert receipt["status"] == "PREPARED" and receipt["new_image_model_calls"] == 0
    assert receipt["finops"]["api_call_count"] is None
    assert receipt["finops"]["api_attempt_record_count"] == 1
    assert receipt["finops"]["estimated_api_cost_usd"] is None
    assert receipt["finops"]["release_weight_finops_pct"] == 40
    payload = json.loads(next((plan.parent / "wandb/packages").rglob("record.json")).read_text())
    assert payload["prompt_version"] == "cosmetics-v1"
    assert payload["started_at"] == record["started_at"]
    assert payload["estimate_origin"] == "NOT_CALCULATED_NO_RATE_BASIS"
    assert payload["actual_cost_usd"] is None
    assert (plan.parent / "report.json").read_bytes() == before


def test_upload_and_repeat_use_same_identity_and_do_not_mutate_report(trial, monkeypatch):
    plan, _ = trial
    before = (plan.parent / "report.json").read_bytes()
    seen = []
    def publish(packages, plans, root, **kwargs):
        seen.append([(p["run_id"], p["manifest_sha256"]) for p in packages])
        assert all(p.is_file() for p in plans)
        return {"status":"READBACK_VERIFIED", "overview_url":"https://wandb.ai/example-team/project/runs/test",
                "records":[{"status":"READBACK_VERIFIED"}]}
    monkeypatch.setattr(bridge, "publish_packages", publish)
    assert sync(plan, publish=True)["readback_verified"] is True
    assert sync(plan, publish=True)["readback_verified"] is True
    assert seen[0] == seen[1]
    assert (plan.parent / "report.json").read_bytes() == before


def test_single_sync_and_full_import_have_same_manifest(trial, monkeypatch):
    plan, record = trial
    from beauty_ai.wandb_experiments import prepare_package
    root = next(parent.parent for parent in plan.parent.parents if parent.name == "outputs")
    expected = prepare_package(record, root, root / "outputs" / "full-import")
    seen = []
    def publish(packages, *args, **kwargs):
        seen.append(packages[0]["manifest_sha256"])
        return {"status":"READBACK_VERIFIED", "overview_url":"https://wandb.ai/team/project/runs/a", "records":[]}
    monkeypatch.setattr(bridge, "publish_packages", publish)
    assert sync(plan, publish=True)["status"] == "READBACK_VERIFIED"
    assert seen == [expected["manifest_sha256"]]


@pytest.mark.parametrize("error", [RuntimeError("sk-" + "x"*40), ValueError("WANDB_REMOTE_IMAGE_MISMATCH")])
def test_upload_failure_is_separate_sanitized_and_never_regenerates(trial, monkeypatch, error):
    plan, _ = trial
    before = (plan.parent / "report.json").read_bytes()
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(bridge, "publish_packages", fail)
    from beauty_ai import experiment_execution
    monkeypatch.setattr(experiment_execution, "execute_plan", lambda *a, **k: pytest.fail("Image rerun"))
    receipt = sync(plan, publish=True)
    assert receipt["status"] == "INCOMPLETE" and not receipt["readback_verified"]
    assert "sk-" not in json.dumps(receipt)
    assert (plan.parent / "report.json").read_bytes() == before


def test_unverified_upload_is_not_success(trial, monkeypatch):
    plan, _ = trial
    monkeypatch.setattr(bridge, "publish_packages", lambda *a, **k: {"status":"UPLOADED"})
    assert sync(plan, publish=True)["error_code"] == "WANDB_READBACK_UNCONFIRMED"


def test_empty_mock_or_preview_does_not_publish(trial, monkeypatch):
    plan, _ = trial
    monkeypatch.setattr(bridge, "collect_trial", lambda *a, **k: [])
    monkeypatch.setattr(bridge, "publish_packages", lambda *a, **k: pytest.fail("Empty upload"))
    assert sync(plan, publish=True)["status"] == "SKIPPED_NO_LIVE_RECORDS"


def test_mock_auto_hook_never_uploads(trial, monkeypatch):
    plan, _ = trial
    monkeypatch.setattr(bridge, "sync_trial", lambda *a, **k: pytest.fail("Mock contacted uploader"))
    assert bridge.finish_recording(plan, expected_sha256="d"*64, entity="team", project="project", live=False)["status"] == "SKIPPED_MOCK"


def test_auto_hook_publishes_live_saved_results(trial, monkeypatch):
    plan, _ = trial
    observed = []
    def capture(path, **kwargs):
        observed.append((path, kwargs))
        return {"status":"READBACK_VERIFIED"}
    monkeypatch.setattr(bridge, "sync_trial", capture)
    bridge.finish_recording(plan, expected_sha256="d"*64, entity="team", project="project", live=True)
    assert observed[0][0] == plan and observed[0][1]["publish"] is True


def test_symlinked_staging_stops_before_upload(trial, tmp_path):
    plan, _ = trial
    outside = tmp_path / "outside"
    outside.mkdir()
    (plan.parent / "wandb").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        sync(plan, publish=True)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("entity", ["../team", "", None, "team/name"])
def test_invalid_destination_does_not_write(trial, entity):
    plan, _ = trial
    with pytest.raises(ValueError, match="WANDB_DESTINATION_INVALID"):
        bridge.sync_trial(plan, expected_sha256="d"*64, entity=entity)
    assert not (plan.parent / "wandb-sync.json").exists()


def test_new_trial_never_inherits_historical_price(trial):
    _, record = trial
    assert historical_estimate(record) == (None, "NOT_CALCULATED_NO_RATE_BASIS", None)
    historical = dict(record, data_provenance={})
    assert historical_estimate(historical)[1] == "RECONSTRUCTED_WITH_RECORDED_B12_RATES"
    assert "api_attempt_record_count" not in finops_summary([{"payload":historical}])


def test_sync_cli_never_invokes_executor(trial, monkeypatch, capsys):
    plan, _ = trial
    from beauty_ai import experiment_execution
    monkeypatch.setattr(experiment_execution, "execute_plan", lambda *a, **k: pytest.fail("Generated from sync CLI"))
    assert experiment_runner.main(["sync-wandb", "--plan", str(plan), "--expect-sha256", "d"*64,
        "--entity", "team", "--prepare-only"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PREPARED"


def test_prior_verified_receipt_is_not_lost_on_retry_error(trial, monkeypatch):
    plan, _ = trial
    monkeypatch.setattr(bridge, "publish_packages", lambda *a, **k: {"status":"READBACK_VERIFIED", "overview_url":"https://wandb.ai/team/project/runs/a", "records":[]})
    first = sync(plan, publish=True)
    def fail(*a, **k):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(bridge, "publish_packages", fail)
    next_receipt = sync(plan, publish=True)
    assert next_receipt["status"] == "INCOMPLETE"
    assert next_receipt["previous_verified_upload"]["overview_url"] == first["overview_url"]
