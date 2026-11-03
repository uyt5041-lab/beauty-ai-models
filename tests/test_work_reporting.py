"""Optional work reports retain legacy bytes and exact, independently scoped evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from beauty_ai.cost_accounting import canonical_bytes, normalize_dataset
from beauty_ai.cost_reporting import CONTRACT_VERSION, build_report, main, render_report, write_report
from beauty_ai.generation_costs import normalize_generation_manifest
from beauty_ai.work_contracts import normalize_work_dataset
from beauty_ai.work_ledger import AdWorkLedger
from beauty_ai.work_metrics import summarize_work_dataset


def uid(name):
    return str(uuid5(NAMESPACE_URL, "work-report:" + name))


def hashed(name):
    return sha256(name.encode()).hexdigest()


def empty_legacy():
    return {"schema_version": "beautyai-cost-dataset-v1", "cost_items": [], "batches": [], "acceptances": []}


def legacy():
    return {**empty_legacy(), "cost_items": [
        {"cost_id": "cost-" + arm, "revision": 1, "supersedes_revision": None,
         "source_ref": "synthetic:" + arm, "source_sha256": hashed(arm), "kind": "image",
         "category": "research", "estimated_cost_usd": amount, "rate_version": "rates-v1",
         "actual_cost_usd": None, "billing_status": "UNRECONCILED", "billing_evidence_ref": None,
         "allocations": {}, "state": "SUCCEEDED"}
        for arm, amount in (("baseline", "2"), ("candidate", "1"))]}


def generation():
    return {"schema_version": "beautyai-generation-comparison-v1", "comparison_id": "synthetic-generation",
            "estimate_rate_version": "rates-v1", "pairs": [{"pair_id": "pair-1",
                "baseline_cost_id": "cost-baseline", "candidate_cost_id": "cost-candidate"}]}


def work_data():
    project = uid("project")
    data = {"schema_version": "beautyai-work-dataset-v1", "data_kind": "SYNTHETIC_FIXTURE",
            "works": [], "attempts": [], "receipts": [], "image_bindings": [], "rate_book": None}
    for arm, amount in (("baseline", "2"), ("candidate", "1"), ("cancelled", "0.5"), ("open", None)):
        completed = arm in {"baseline", "candidate"}
        work = {"project_id": project, "work_id": uid(arm), "product_ref": "product-1",
                "task_pair_id": "pair-1" if completed else None, "arm": arm if completed else None,
                "required_stages": ["image_generation"],
                "status": "COMPLETED" if completed else "CANCELLED" if arm == "cancelled" else "OPEN",
                "created_at": "2026-11-03T00:00:00Z", "closed_at": "2026-11-03T00:02:00Z" if arm != "open" else None,
                "completion": None}
        data["works"].append(work)
        if arm == "open":
            data["image_bindings"].append({"project_id": project, "work_id": work["work_id"],
                                           "job_id": uid("unobserved-job"), "attempt_id": None})
            continue
        identity = {"attempt_id": uid("attempt-" + arm), "project_id": project, "work_id": work["work_id"],
                    "stage": "image_generation" if completed else "revision",
                    "operation": "generate" if completed else "rewrite_copy", "provider": "openai",
                    "configured_model": "model-1", "prompt_version": "v1", "request_hash": hashed(arm),
                    "parent_attempt_id": None, "started_at": "2026-11-03T00:01:00Z"}
        data["attempts"].append(identity)
        receipt = {"schema_version": "beautyai-work-receipt-v1", "attempt_id": identity["attempt_id"],
                   "provider_request_id": "request-" + arm, "model": "model-1",
                   "usage": {"input_tokens": 100, "output_tokens": 20} if completed else None,
                   "elapsed_seconds": .25, "status": "SUCCEEDED" if completed else "FAILED",
                   "error_code": None if completed else "TEXT_REFUSAL",
                   "artifact_sha256": hashed("artifact-" + arm) if completed else None,
                   "estimated_cost_usd": amount, "rate_version": "rates-v1", "actual_cost_usd": None,
                   "billing_evidence_ref": None, "source_ref": "synthetic:" + arm, "source_sha256": hashed("source-" + arm)}
        data["receipts"].append(receipt)
        if completed:
            work["completion"] = {"artifact_sha256": receipt["artifact_sha256"], "reviewer_ref": "reviewer-1",
                                  "quality_policy_version": "quality-v1", "quality_pass": True,
                                  "expected_attempt_ids": [identity["attempt_id"]]}
    return data


@pytest.mark.parametrize("with_generation", [False, True])
@pytest.mark.parametrize("with_work", [False, True])
def test_exact_optional_shapes_and_input_identity_are_independent(with_generation, with_work):
    data, work, manifest = legacy(), work_data(), generation()
    original = deepcopy((data, work, manifest))
    report = build_report(data, generation_comparison=manifest if with_generation else None,
                           work_dataset=work if with_work else None)
    keys = {"schema_version", "contract_version", "report_id", "mode", "data_kind", "inputs",
            "scope", "overview", "comparison", "sources"}
    inputs = {"dataset_sha256", "comparison_sha256"}
    identity = {"contract_version": CONTRACT_VERSION, "dataset": normalize_dataset(data), "comparison": None}
    if with_generation:
        keys.add("generation_comparison")
        inputs.add("generation_comparison_sha256")
        identity["generation_comparison"] = normalize_generation_manifest(manifest)
    if with_work:
        keys.add("work_costs")
        inputs.add("work_dataset_sha256")
        identity["work_dataset"] = normalize_work_dataset(work)
        assert report["work_costs"] == summarize_work_dataset(work)
        assert report["inputs"]["work_dataset_sha256"] == sha256(canonical_bytes(identity["work_dataset"])).hexdigest()
    assert set(report) == keys and set(report["inputs"]) == inputs
    assert report["report_id"] == sha256(canonical_bytes(identity)).hexdigest()
    assert (data, work, manifest) == original


def test_absent_and_explicit_none_work_keep_legacy_and_generation_bytes(tmp_path):
    for index, manifest in enumerate((None, generation())):
        before = build_report(legacy(), generation_comparison=manifest)
        after = build_report(legacy(), generation_comparison=manifest, work_dataset=None)
        assert canonical_bytes(before) == canonical_bytes(after)
        assert render_report(before) == render_report(after)
        paths = write_report(legacy(), None, tmp_path / str(index), generation_comparison=manifest, work_dataset=None)
        assert Path(paths["report_path"]).read_bytes() == canonical_bytes(before)
        assert Path(paths["html_path"]).read_text() == render_report(before)


def test_work_snapshot_and_history_changes_affect_identity_even_when_totals_do_not():
    data = work_data()
    original = build_report(empty_legacy(), work_dataset=data)
    data["works"].reverse()
    data["attempts"].reverse()
    data["receipts"].reverse()
    assert build_report(empty_legacy(), work_dataset=data) == original
    changed = deepcopy(data)
    changed["receipts"][0]["source_sha256"] = hashed("changed evidence")
    report = build_report(empty_legacy(), work_dataset=changed)
    assert report["report_id"] != original["report_id"]
    assert report["inputs"]["work_dataset_sha256"] != original["inputs"]["work_dataset_sha256"]
    assert report["work_costs"]["summary"] == original["work_costs"]["summary"]


def test_pure_work_synthetic_label_is_preserved_and_mixed_actual_data_is_declared():
    work = work_data()
    assert build_report(empty_legacy(), work_dataset=work)["data_kind"] == "SYNTHETIC_FIXTURE"
    assert build_report(legacy(), work_dataset=work)["data_kind"] == "SYNTHETIC_FIXTURE"
    actual = legacy()
    actual["cost_items"][0]["source_ref"] = "declared:source"
    assert build_report(actual, work_dataset=work)["data_kind"] == "DECLARED_DATA"
    work["data_kind"] = "DECLARED_DATA"
    assert build_report(legacy(), work_dataset=work)["data_kind"] == "DECLARED_DATA"


def test_synthetic_comparison_name_does_not_hide_declared_sources_when_work_is_mixed():
    root = Path(__file__).resolve().parents[1] / "examples" / "evaluation"
    data = json.loads((root / "finops-dataset.json").read_text())
    comparison = json.loads((root / "finops-comparison.json").read_text())
    data["cost_items"][0]["source_ref"] = "declared:provider-receipt"
    assert build_report(data, comparison, work_dataset=work_data())["data_kind"] == "DECLARED_DATA"


def test_korean_dashboard_keeps_global_costs_unknowns_stage_models_and_pair_exclusions():
    report = build_report(empty_legacy(), work_dataset=work_data())
    section = report["work_costs"]
    assert section["summary"]["estimated_total_usd"] is None
    assert section["summary"]["known_partial_estimated_usd"] == "3.500000000"
    assert section["comparison"]["estimated_cost_reduction_pct"] == "50.000000000"
    html = render_report(report)
    for text in ('id="work-costs"', 'id="work-cost-table"', "광고 한 건, 완료까지의 비용", "합성 예제",
                 "완료", "진행 중", "취소", "실패 / 수정", "입력 / 출력 토큰", "토큰 관측률",
                 "최종 파일 SHA-256", "단계와 모델별 내역", "model-1", "캐시 / 추론 토큰",
                 "미확인", "$3.5", "50%", "UNPAIRED_WORK", "모델 API 비용 범위",
                 "실패·취소·진행 비용 포함", "캐시 할인은 반영하지", "작업의 실제 완료 시간과 다릅니다"):
        assert text in html
    assert "default-src 'none'" in html and "script-src 'none'" in html and "<script" not in html
    assert html.index('id="work-costs"') < html.index('id="overview"')
    for work in section["works"]:
        if work["final_artifact_sha256"] is not None:
            assert work["final_artifact_sha256"] in html


def test_both_panels_do_not_claim_work_costs_are_unmeasured():
    report = build_report(legacy(), generation_comparison=generation(), work_dataset=work_data())
    html = render_report(report)
    assert 'id="generation-comparison"' in html and 'id="work-costs"' in html
    assert "생 프롬프트 → 최종 광고 완료 비용: 미측정" not in html
    assert "금액을 중복 합산하지 않습니다" in html


def test_all_work_labels_and_reason_codes_are_html_escaped():
    report = build_report(empty_legacy(), work_dataset=work_data())
    hostile = '<img src=x onerror="alert(1)">'
    row = report["work_costs"]["works"][0]
    for key in ("product_ref", "work_id", "task_pair_id", "arm", "status", "final_artifact_sha256", "quality_policy_version"):
        row[key] = hostile
    row["rate_versions"] = [hostile]
    row["stages"][0].update(stage=hostile, provider=hostile, model=hostile)
    excluded = report["work_costs"]["comparison"]["excluded_pairs"][0]
    excluded.update(task_pair_id=hostile, work_ids=[hostile], reason_codes=[hostile])
    report["work_costs"]["comparison"]["reason_codes"] = [hostile]
    html = render_report(report)
    assert hostile not in html and "&lt;img" in html and "&quot;alert(1)&quot;" in html


def test_zero_is_visible_while_absent_usage_and_cost_remain_unknown():
    data = work_data()
    for receipt in data["receipts"]:
        receipt["estimated_cost_usd"] = "0"
    report = build_report(empty_legacy(), work_dataset=data)
    html = render_report(report)
    assert "$0.00" in html and "미확인" in html
    assert report["work_costs"]["summary"]["estimated_total_usd"] is None  # Unobserved bound job remains missing.
    assert report["work_costs"]["summary"]["known_partial_estimated_usd"] == "0.000000000"


def test_work_only_cli_uses_empty_legacy_dataset_and_preserves_exact_saved_report(tmp_path, capsys):
    source = tmp_path / "work.json"
    source.write_text(json.dumps(work_data()))
    output = tmp_path / "report"
    assert main(["--work-dataset", str(source), "--output", str(output)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["new_provider_calls"] == 0
    report = json.loads((output / "report.json").read_text())
    assert report == build_report(empty_legacy(), work_dataset=work_data())
    assert (output / "index.html").read_text() == render_report(report)


def test_cli_reads_ledger_without_initializing_or_modifying_schema(tmp_path, monkeypatch, capsys):
    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project = uid("project")
    ledger.create_work(project, uid("fresh-work"), "product-1")
    before = ledger.path.read_bytes()
    import beauty_ai.work_ledger as module

    monkeypatch.setattr(module.AdWorkLedger, "__init__", lambda *_: pytest.fail("Readonly report must not initialize a ledger"))
    assert main(["--work-ledger", str(ledger.path), "--work-project", project,
                 "--output", str(tmp_path / "report")]) == 0
    assert json.loads(capsys.readouterr().out)["new_provider_calls"] == 0
    assert ledger.path.read_bytes() == before
    report = json.loads((tmp_path / "report" / "report.json").read_text())
    assert report["work_costs"]["summary"]["open_work_count"] == 1


def test_cli_rates_are_an_explicit_snapshot_and_price_only_observed_usage(tmp_path, capsys):
    from beauty_ai.work_tracking import make_work_receipt

    ledger = AdWorkLedger(tmp_path / "works.sqlite3")
    project, work, attempt = uid("rate-project"), uid("rate-work"), uid("rate-attempt")
    ledger.create_work(project, work, "product-1")
    ledger.start_attempt(project, work, attempt, "review", "review_copy", "openai", "model-1", "v1", hashed("request"))
    ledger.record_receipt(project, work, make_work_receipt(attempt, model="model-1", status="FAILED",
        error_code="TEXT_REFUSAL", usage={"input_tokens": 2, "output_tokens": 3}))
    rates = {"schema_version": "beautyai-work-rates-v1", "rate_version": "snapshot-v1", "models": {
        "model-1": {"mode": "combined", "input_usd": "1", "output_usd": "2",
                    "input_text_usd": None, "input_image_usd": None}}}
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(rates))
    output = tmp_path / "report"
    assert main(["--work-ledger", str(ledger.path), "--work-project", project, "--work-rates", str(path),
                 "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["new_provider_calls"] == 0
    report = json.loads((output / "report.json").read_text())
    assert report["work_costs"]["summary"]["estimated_total_usd"] == "0.000008000"
    assert report["work_costs"]["works"][0]["rate_versions"] == ["snapshot-v1"]
    assert ledger.export_dataset(project)["receipts"][0]["estimated_cost_usd"] is None


@pytest.mark.parametrize("flags", [[], ["--work-ledger", "missing"], ["--work-project", "id"],
    ["--work-rates", "rates.json"], ["--work-dataset", "work.json", "--work-ledger", "db"],
    ["--work-dataset", "work.json", "--work-project", "id"],
    ["--work-dataset", "work.json", "--work-rates", "rates.json"]])
def test_cli_rejects_incompatible_or_incomplete_work_arguments(tmp_path, flags):
    with pytest.raises(SystemExit) as error:
        main([*flags, "--output", str(tmp_path / "report")])
    assert error.value.code == 2 and not (tmp_path / "report").exists()


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"usage":NaN}', '{"password":"private"}', ' ' * (1024 * 1024 + 1)])
def test_work_input_json_rejections_are_bounded_safe_and_write_nothing(tmp_path, raw, capsys):
    path = tmp_path / "work.json"
    path.write_text(raw)
    with pytest.raises(SystemExit) as error:
        main(["--work-dataset", str(path), "--output", str(tmp_path / "report")])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert message.startswith("WORK_") and "password" not in message.lower() and "{" not in message
    assert not (tmp_path / "report").exists()


def test_work_dataset_input_cannot_be_overwritten_by_output(tmp_path, capsys):
    output = tmp_path / "report"
    output.mkdir()
    source = output / "report.json"
    original = json.dumps(work_data())
    source.write_text(original)
    with pytest.raises(SystemExit):
        main(["--work-dataset", str(source), "--output", str(output)])
    assert "COST_REPORT_INPUT_OVERWRITE" in capsys.readouterr().err and source.read_text() == original


def test_wandb_range_failure_is_safe_nonzero_and_keeps_local_work_report(tmp_path, monkeypatch, capsys):
    import beauty_ai.cost_wandb as bridge

    source = tmp_path / "work.json"
    source.write_text(json.dumps(work_data()))
    monkeypatch.setattr(bridge, "sync_cost_report", lambda *_args, **_kwargs:
                        {"status": "INCOMPLETE", "error_code": "WORK_WANDB_COUNT_RANGE"})
    output = tmp_path / "report"
    assert main(["--work-dataset", str(source), "--output", str(output), "--wandb",
                 "--wandb-entity", "example", "--wandb-project", "test"]) == 2
    assert json.loads(capsys.readouterr().out)["wandb_error_code"] == "WORK_WANDB_COUNT_RANGE"
    assert (output / "report.json").is_file() and (output / "index.html").is_file()
