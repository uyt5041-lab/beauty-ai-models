"""Local dashboard semantics, reproducible evidence identity and safe file IO."""
import builtins
from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from beauty_ai.cost_accounting import canonical_bytes, normalize_dataset
from beauty_ai.cost_reporting import (
    CONTRACT_VERSION, REPORT_SCHEMA_VERSION, build_report, main, render_report, write_report,
)
from beauty_ai.generation_costs import compare_generations, normalize_generation_manifest


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "evaluation"


@pytest.fixture
def example():
    return tuple(json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
                 for name in ("finops-dataset.json", "finops-comparison.json"))


def historical_data():
    """The required 26-observation shape, without copying private source data."""
    costs = []
    for index in range(26):
        amount = "0.040000000" if index < 19 else "0.059245000" if index == 19 else None
        costs.append({
            "cost_id": f"legacy-case-{index}", "revision": 1, "supersedes_revision": None,
            "source_ref": f"legacy:group/case-{index}",
            "source_sha256": sha256(str(index).encode()).hexdigest(),
            "kind": "image", "category": "research", "estimated_cost_usd": amount,
            "rate_version": "historical-b12-2026-10-23" if amount is not None else None,
            "actual_cost_usd": None, "billing_status": "UNRECONCILED", "billing_evidence_ref": None,
            "allocations": {}, "state": "RECORDED",
        })
    return {"schema_version": "beautyai-cost-dataset-v1", "cost_items": costs,
            "batches": [], "acceptances": []}


@pytest.fixture
def generation_example():
    data = historical_data()
    for index, record in enumerate(data["cost_items"]):
        record["state"] = "SUCCEEDED"
        if index < 20:
            record["estimated_cost_usd"] = "0.041213" if index < 10 else "0.0407115"
    manifest = {
        "schema_version": "beautyai-generation-comparison-v1", "comparison_id": "generation-comparison-1",
        "estimate_rate_version": "historical-b12-2026-10-23",
        "pairs": [{"pair_id": f"pair-{index}", "baseline_cost_id": f"legacy-case-{index}",
                   "candidate_cost_id": f"legacy-case-{index + 10}"} for index in range(10)],
    }
    return data, manifest


def test_example_keeps_exact_units_and_counterfactual_separate_from_cash(example):
    data, manifest = example
    report = build_report(data, manifest)
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["mode"] == "COMPARISON"
    assert report["data_kind"] == "SYNTHETIC_FIXTURE"
    assert report["scope"]["scope_authority"] == "DECLARED_SCOPE"
    assert report["scope"]["quality_authority"] == "DECLARED_QUALITY"
    assert report["scope"]["complete_account_coverage_verified"] is False
    assert report["scope"]["human_review_verified"] is False
    assert report["overview"]["accepted_ads"] == 9
    assert report["overview"]["cost_item_count"] == 3
    assert report["comparison"]["research"]["actual"]["total_cost_usd"] == "0.250000000"
    for basis in ("estimated", "actual"):
        section = report["comparison"][basis]
        assert section["baseline"]["unit_cost_usd"] == "0.500000000"
        assert section["candidate"]["unit_cost_usd"] == "0.300000000"
        assert section["unit_cost_reduction_pct"] == "40.000000000"
        assert section["counterfactual_difference_usd"] == "1.000000000"
        assert Decimal(section["baseline"]["total_cost_usd"]) - Decimal(section["candidate"]["total_cost_usd"]) == Decimal(".5")
    assert report["comparison"]["counterfactual_is_actual_cash_difference"] is False
    html = render_report(report)
    assert "40%" in html and "$0.5" in html
    assert "합성 예제 · 계산 검증용" in html
    assert "실제 비용 절감 실험 결과가 아닙니다" in html
    assert "실제 청구 감소액이 아닙니다" in html


def test_overview_26_keeps_20_estimates_and_six_missing_without_inventing_actual_or_acceptance():
    report = build_report(historical_data())
    overview = report["overview"]
    assert report["mode"] == "OVERVIEW"
    assert report["data_kind"] == "DECLARED_DATA"
    assert overview["cost_item_count"] == overview["categories"]["research"]["cost_item_count"] == 26
    assert overview["categories"]["production"]["cost_item_count"] == 0
    assert overview["estimated"]["known_partial_usd"] == "0.819245000"
    assert overview["estimated"]["known_cost_count"] == 20
    assert overview["estimated"]["unknown_cost_count"] == 6
    assert overview["estimated"]["total_cost_usd"] is None
    assert overview["actual"]["known_partial_usd"] == "0.000000000"
    assert overview["actual"]["known_cost_count"] == 0
    assert overview["actual"]["unknown_cost_count"] == 26
    assert overview["actual"]["total_cost_usd"] is None
    assert overview["accepted_ads"] is None
    assert overview["acceptance_status"] == "NOT_RECORDED"
    assert report["scope"]["estimate_rate_version"] is None
    assert report["scope"]["observed_rate_versions"] == ["historical-b12-2026-10-23"]
    assert sum(source["rate_version"] is not None for source in report["sources"]) == 20
    assert all(source["billing_evidence_ref"] is None for source in report["sources"])
    for basis in ("estimated", "actual"):
        section = report["comparison"][basis]
        assert section["unit_cost_reduction_pct"] is None
        assert section["counterfactual_difference_usd"] is None
        assert section["baseline"]["unit_cost_usd"] is None
        assert "COMPARISON_NOT_PROVIDED" in section["reason_codes"]
        assert "ACCEPTANCE_NOT_RECORDED" in section["reason_codes"]
    html = render_report(report)
    assert "$0.819245" in html and "금액 미확인 26건" in html
    assert "$0.00" not in html
    assert "산출 보류" in html and "채택 기록 미등록" in html
    assert "관측 단가 기준: historical-b12-2026-10-23" in html
    assert "비교 단가 기준 <b>미확인</b>" in html


def test_accepted_records_alone_do_not_enable_a_comparison(example):
    report = build_report(example[0])
    assert report["overview"]["accepted_ads"] == 9
    assert report["data_kind"] == "SYNTHETIC_FIXTURE"
    for basis in ("estimated", "actual"):
        assert report["comparison"][basis]["unit_cost_reduction_pct"] is None
        assert report["comparison"][basis]["counterfactual_difference_usd"] is None
        assert report["comparison"][basis]["reason_codes"] == ["COMPARISON_NOT_PROVIDED"]
    assert "등록된 비교 조건을 충족했습니다" not in render_report(report)


def test_report_identity_is_canonical_input_history_manifest_and_contract(example):
    data, manifest = example
    untouched = deepcopy((data, manifest))
    report = build_report(data, manifest)
    identity = {"contract_version": CONTRACT_VERSION, "dataset": normalize_dataset(data),
                "comparison": manifest}
    assert report["report_id"] == sha256(canonical_bytes(identity)).hexdigest()
    assert report["inputs"]["dataset_sha256"] == sha256(canonical_bytes(normalize_dataset(data))).hexdigest()
    assert report["inputs"]["comparison_sha256"] == sha256(canonical_bytes(manifest)).hexdigest()
    assert (data, manifest) == untouched
    data["cost_items"].reverse()
    data["batches"].reverse()
    data["acceptances"].reverse()
    reordered = dict(reversed(list(data.items())))
    assert build_report(reordered, dict(reversed(list(manifest.items())))) == report
    assert "created_at" not in report and "generated_at" not in report


def test_previous_revision_changes_identity_but_only_latest_amount_is_counted(example):
    data, manifest = example
    first = data["cost_items"][0]
    data["cost_items"].append({**first, "revision": 2, "supersedes_revision": 1})
    report = build_report(data, manifest)
    first["estimated_cost_usd"] = "9"
    other = build_report(data, manifest)
    assert other["report_id"] != report["report_id"]
    assert other["inputs"]["dataset_sha256"] != report["inputs"]["dataset_sha256"]
    assert other["comparison"] == report["comparison"]
    assert other["overview"]["cost_item_count"] == 3
    assert other["overview"]["cost_revision_count"] == 4


def test_partial_actual_is_reported_without_promoting_it_to_complete_cost(example):
    data, manifest = example
    data["cost_items"][0].update(actual_cost_usd="0.75", billing_status="PARTIAL")
    report = build_report(data, manifest)
    assert report["overview"]["actual"]["known_cost_count"] == 3
    assert report["overview"]["actual"]["complete_cost_count"] == 2
    assert report["overview"]["actual"]["partial_cost_count"] == 1
    assert report["overview"]["actual"]["total_cost_usd"] is None
    assert report["comparison"]["actual"]["unit_cost_reduction_pct"] is None
    assert report["comparison"]["estimated"]["unit_cost_reduction_pct"] == "40.000000000"
    assert "부분 청구 1건" in render_report(report)


def test_known_zero_is_displayed_as_zero_and_unknown_is_not(example):
    data, manifest = example
    data["cost_items"][0].update(actual_cost_usd="0", estimated_cost_usd="0")
    report = build_report(data, manifest)
    assert report["comparison"]["actual"]["unit_cost_reduction_pct"] is None
    assert "BASELINE_ZERO_COST" in report["comparison"]["actual"]["reason_codes"]
    assert "$0.00" in render_report(report)
    empty = build_report({"schema_version": "beautyai-cost-dataset-v1", "cost_items": [],
                          "batches": [], "acceptances": []})
    assert empty["overview"]["actual"]["total_cost_usd"] is None
    assert "$0.00" not in render_report(empty)


def test_negative_reduction_is_labelled_cost_increase(example):
    data, manifest = example
    data["cost_items"][1].update(actual_cost_usd="3", estimated_cost_usd="3")
    report = build_report(data, manifest)
    assert report["comparison"]["actual"]["unit_cost_reduction_pct"] == "-20.000000000"
    html = render_report(report)
    assert "비용 증가율" in html and "20%" in html
    assert "$-0.5" in html


def test_empty_selected_batch_has_a_notice_without_inventing_missing_item_count(example):
    data, manifest = example
    removed = data["cost_items"].pop(0)
    data["batches"][0]["expected_cost_ids"] = []
    report = build_report(data, manifest)
    arm = report["comparison"]["estimated"]["baseline"]
    assert arm["missing_batch_count"] == 1
    assert arm["missing_batch_ids"] == list(removed["allocations"])
    assert arm["missing_cost_count"] == 0
    assert report["comparison"]["estimated"]["unit_cost_reduction_pct"] is None
    html = render_report(report)
    assert "비용 기록이 없는 묶음 1개" in html
    assert "누락된 비용 항목 수는 알 수 없습니다" in html


def test_tiny_cost_and_coverage_are_exact_even_with_hostile_decimal_context(example):
    data, manifest = example
    data["cost_items"][2].update(estimated_cost_usd="0.000000001", actual_cost_usd="0.000000001")
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        report = build_report(data, manifest)
    assert report["overview"]["estimated"]["known_partial_usd"] == "3.500000001"
    assert "$0.000000001" in render_report(report)


def test_renderer_escapes_labels_reasons_and_ids_and_has_no_active_external_content(example):
    report = build_report(*example)
    hostile = '\"><script>alert("x")</script><img src="https://example.invalid/x">'
    report["sources"][0]["source_ref"] = hostile
    report["sources"][0]["cost_id"] = hostile
    report["sources"][0]["source_sha256"] = hostile
    report["sources"][0]["rate_version"] = hostile
    report["sources"][0]["billing_evidence_ref"] = hostile
    report["scope"]["observed_rate_versions"] = [hostile]
    report["scope"]["quality_policy_version"] = hostile
    report["report_id"] = hostile
    report["comparison"]["actual"]["reason_codes"] = [hostile]
    html = render_report(report)
    assert hostile not in html
    assert "&lt;script&gt;" in html and "&quot;" in html
    assert "<script" not in html and "<img" not in html and "<iframe" not in html
    assert "Content-Security-Policy" in html
    assert "connect-src 'none'" in html and "script-src 'none'" in html
    assert '<html lang="ko">' in html
    assert 'name="viewport"' in html and "@media(max-width:760px)" in html


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(schema_version="unexpected"),
    lambda data: data["cost_items"][0].update(actual_cost_usd=2.0),
    lambda data: data["cost_items"][0].update(source_ref="private/api_key.txt"),
    lambda data: data["cost_items"][0].update(billing_evidence_ref=None),
])
def test_report_rejects_invalid_schema_or_evidence(example, mutation):
    mutation(example[0])
    with pytest.raises(ValueError, match="COST_"):
        build_report(*example)


def test_write_report_is_byte_reproducible_and_does_not_change_inputs(tmp_path, example):
    before = deepcopy(example)
    result = write_report(*example, output=tmp_path / "report")
    json_path, html_path = Path(result["report_path"]), Path(result["html_path"])
    report_bytes, html_bytes = json_path.read_bytes(), html_path.read_bytes()
    assert json.loads(report_bytes)["report_id"] == result["report_id"]
    assert write_report(*example, output=tmp_path / "report") == result
    assert json_path.read_bytes() == report_bytes
    assert html_path.read_bytes() == html_bytes
    assert example == before


def test_local_cli_writes_overview_with_no_optional_paid_import_or_network(tmp_path, monkeypatch, capsys):
    data_file = tmp_path / "dataset.json"
    data_file.write_bytes(canonical_bytes(historical_data()))
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"wandb", "openai", "httpx", "requests", "torch", "diffusers"}
        assert not name.endswith(("cost_wandb", "openai_images", "api_image_processor", "inference_runtime"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert main(["--dataset", str(data_file), "--output", str(tmp_path / "output")]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["new_provider_calls"] == 0
    assert response["status"] == "LOCAL_REPORT_WRITTEN"
    assert json.loads(Path(response["report_path"]).read_bytes())["mode"] == "OVERVIEW"


def test_python_module_cli_generates_synthetic_comparison(tmp_path):
    command = [sys.executable, "-m", "beauty_ai.cost_reporting", "--dataset", str(EXAMPLES / "finops-dataset.json"),
               "--comparison", str(EXAMPLES / "finops-comparison.json"), "--output", str(tmp_path / "output")]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    report = json.loads(Path(response["report_path"]).read_bytes())
    assert report["comparison"]["actual"]["unit_cost_reduction_pct"] == "40.000000000"
    assert report["data_kind"] == "SYNTHETIC_FIXTURE"


def test_fresh_module_import_does_not_load_provider_model_or_network_clients():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert name.split('.')[0] not in {'wandb', 'openai', 'httpx', 'requests', 'torch', 'diffusers'}
    assert not name.endswith(('cost_wandb', 'openai_images', 'api_image_processor', 'inference_runtime'))
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import beauty_ai.cost_reporting
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_cli_enforces_input_size_limit_before_json_parsing(tmp_path, monkeypatch, capsys):
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(historical_data()))
    monkeypatch.setattr("beauty_ai.cost_reporting._MAX_JSON_BYTES", 128)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(source), "--output", str(tmp_path / "out")])
    assert error.value.code == 2
    assert capsys.readouterr().err == "COST_REPORT_INPUT_INVALID\n"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":2}', b'{"a":NaN}', b'{"a":Infinity}', b'\xff', b'{"raw":"private-content"'])
def test_cli_rejects_invalid_json_without_echoing_source_content(tmp_path, capsys, raw):
    data_file = tmp_path / "dataset.json"
    data_file.write_bytes(raw)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(data_file), "--output", str(tmp_path / "output")])
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert message.startswith("COST_") and "private-content" not in message
    assert not (tmp_path / "output" / "report.json").exists()


@pytest.mark.parametrize("input_name", ["report.json", "index.html"])
def test_cli_never_overwrites_its_input(tmp_path, capsys, input_name):
    source = tmp_path / input_name
    before = canonical_bytes(historical_data())
    source.write_bytes(before)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(source), "--output", str(tmp_path)])
    assert error.value.code == 2
    assert source.read_bytes() == before
    assert "COST_REPORT_INPUT_OVERWRITE" in capsys.readouterr().err


def test_cli_rejects_input_hardlink_collision(tmp_path, capsys):
    source = tmp_path / "dataset.json"
    before = canonical_bytes(historical_data())
    source.write_bytes(before)
    (tmp_path / "out").mkdir()
    os.link(source, tmp_path / "out" / "report.json")
    with pytest.raises(SystemExit):
        main(["--dataset", str(source), "--output", str(tmp_path / "out")])
    assert source.read_bytes() == before
    assert "COST_REPORT_INPUT_OVERWRITE" in capsys.readouterr().err


def test_cli_refuses_symlink_input_and_nonregular_input(tmp_path, capsys):
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(historical_data()))
    link = tmp_path / "link.json"
    link.symlink_to(source)
    fifo = tmp_path / "pipe.json"
    os.mkfifo(fifo)
    for path in (link, fifo, tmp_path):
        with pytest.raises(SystemExit) as error:
            main(["--dataset", str(path), "--output", str(tmp_path / "output")])
        assert error.value.code == 2
        assert capsys.readouterr().err.startswith("COST_")


def test_write_refuses_symlink_outputs_before_mutating_any_file(tmp_path, example):
    target = tmp_path / "keep.html"
    target.write_text("preserve", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    (output / "index.html").symlink_to(target)
    with pytest.raises(ValueError, match="COST_REPORT_OUTPUT_INVALID"):
        write_report(*example, output=output)
    assert target.read_text() == "preserve"
    assert not (output / "report.json").exists()


def test_wandb_is_explicit_and_uses_local_report_path(tmp_path, example, monkeypatch, capsys):
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(example[0]))
    called = []

    def sync(path, **kwargs):
        assert Path(path).is_file()
        called.append((path, kwargs))
        return {"status": "READBACK_VERIFIED"}

    monkeypatch.setitem(sys.modules, "beauty_ai.cost_wandb", types.SimpleNamespace(sync_cost_report=sync))
    assert main(["--dataset", str(source), "--output", str(tmp_path / "out"),
                 "--wandb", "--wandb-entity", "test-team", "--wandb-project", "test-project"]) == 0
    assert called[0][1] == {"entity": "test-team", "project": "test-project", "publish": True}
    assert json.loads(capsys.readouterr().out)["wandb_status"] == "READBACK_VERIFIED"


@pytest.mark.parametrize("status", ["INCOMPLETE", "PREPARED", "UPLOADED"])
def test_requested_wandb_publish_without_verified_readback_is_nonzero(tmp_path, example, monkeypatch, capsys, status):
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(example[0]))
    monkeypatch.setitem(sys.modules, "beauty_ai.cost_wandb", types.SimpleNamespace(
        sync_cost_report=lambda *args, **kwargs: {"status": status, "error_code": "WANDB_COST_READBACK_FAILED"}))
    result = main(["--dataset", str(source), "--output", str(tmp_path / "out"),
                   "--wandb", "--wandb-entity", "team", "--wandb-project", "project"])
    assert result == 2
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "LOCAL_REPORT_WRITTEN"
    assert response["wandb_status"] == status
    assert response["wandb_error_code"] == "WANDB_COST_READBACK_FAILED"
    assert Path(response["report_path"]).is_file()


@pytest.mark.parametrize("error,expected", [("WANDB_DESTINATION_INVALID", "WANDB_DESTINATION_INVALID"),
                                           ("private-credential-value", "WANDB_COST_SYNC_FAILED")])
def test_wandb_validation_failure_has_safe_nonzero_result_with_local_report(tmp_path, example, monkeypatch, capsys, error, expected):
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(example[0]))

    def fail(*args, **kwargs):
        raise ValueError(error)

    monkeypatch.setitem(sys.modules, "beauty_ai.cost_wandb", types.SimpleNamespace(sync_cost_report=fail))
    result = main(["--dataset", str(source), "--output", str(tmp_path / "out"),
                   "--wandb", "--wandb-entity", "team", "--wandb-project", "project"])
    assert result == 2
    captured = capsys.readouterr()
    response = json.loads(captured.out)
    assert response["wandb_status"] == "INCOMPLETE"
    assert response["wandb_error_code"] == expected
    assert "private-credential-value" not in captured.out + captured.err
    assert Path(response["report_path"]).is_file()


@pytest.mark.parametrize("extra", [["--wandb"], ["--wandb-entity", "team"], ["--wandb-project", "project"]])
def test_wandb_flags_require_explicit_complete_configuration(tmp_path, extra):
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(tmp_path / "absent.json"), "--output", str(tmp_path / "out"), *extra])
    assert error.value.code == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("with_comparison,report_id,json_hash,html_hash", [
    (True, "6eaf27f30f0a930245d13d301ef4fce095e9de8eccca81b99e12674902362b08",
     "f293a90b19af8b22e0832eaf4fe0d9bde70aa513cdc549ca1ba45f771947d00a",
     "adc18dc7d3a2cffd33398e5c4b34460d82606e38a5483b4e1d302edc8f20ddc9"),
    (False, "7b8d981dcee425d73a08e7c93ccf07ff986104e2a1e417d63ac4978da47ea596",
     "3a36ba012897c23252a1dee15628ffbb56a4aa29fdc570a1e9ac8628c061bc8d",
     "a99b15c187b2b39220a900391af52a24361a774f38db68b6581aa40e16660e06"),
])
def test_absent_generation_keeps_frozen_legacy_identity_json_and_html_bytes(
        example, tmp_path, with_comparison, report_id, json_hash, html_hash):
    data, manifest = example
    comparison = manifest if with_comparison else None
    report = build_report(data, comparison)
    assert build_report(data, comparison, generation_comparison=None) == report
    assert report["report_id"] == report_id
    assert sha256(canonical_bytes(report)).hexdigest() == json_hash
    assert sha256(render_report(report).encode()).hexdigest() == html_hash
    assert set(report["inputs"]) == {"dataset_sha256", "comparison_sha256"}
    assert "generation_comparison" not in report
    # Existing callers may still provide all three historical arguments positionally.
    paths = write_report(data, comparison, tmp_path)
    assert sha256(Path(paths["report_path"]).read_bytes()).hexdigest() == json_hash
    assert sha256(Path(paths["html_path"]).read_bytes()).hexdigest() == html_hash


def test_generation_reports_twenty_compared_six_excluded_without_changing_accepted_metric(generation_example):
    data, manifest = generation_example
    untouched = deepcopy(generation_example)
    previous = build_report(data)
    report = build_report(data, generation_comparison=manifest)
    section = report["generation_comparison"]
    assert section == compare_generations(data, manifest)
    assert section["compared_record_count"] == 20
    assert section["excluded_record_count"] == 6
    assert section["compared_pair_count"] == 10
    assert section["coverage_pct"] == "76.923076923"
    assert section["estimated_cost_reduction_pct"] == "1.216849052"
    assert section["estimated_cost_difference_usd"] == "0.005015000"
    assert section["baseline"]["total_estimated_cost_usd"] == "0.412130000"
    assert section["candidate"]["total_estimated_cost_usd"] == "0.407115000"
    assert section["baseline"]["estimated_cost_per_generation_usd"] == "0.041213000"
    assert section["candidate"]["estimated_cost_per_generation_usd"] == "0.040711500"
    assert report["comparison"] == previous["comparison"]
    assert report["overview"] == previous["overview"]
    assert report["mode"] == "OVERVIEW"
    assert report["comparison"]["estimated"]["unit_cost_reduction_pct"] is None
    assert generation_example == untouched
    html = render_report(report)
    assert "1.216849052%" in html and "$0.005015" in html
    assert "$0.041213" in html and "$0.0407115" in html
    assert "20건" in html and "제외 6건" in html and "비교 10쌍 / 계획 10쌍" in html
    assert "76.923076923%" in html
    assert "UNPAIRED_RECORD" in html and "COST_MISSING" in html
    assert "legacy-case-20" in html
    assert "관측 비교 · 품질 미검증" in html
    assert "OBSERVATIONAL_NOT_QUALITY_CONTROLLED" in html
    assert "생 프롬프트 → 최종 광고 완료 비용: 미측정" in html
    assert "생성당 추정 비교 · 채택 광고 비교 미등록" in html
    assert html.index('<section id="generation-comparison">') < html.index('<section id="overview">')


def test_generation_is_independent_when_accepted_comparison_is_also_present(example):
    data, accepted_manifest = example
    for record in data["cost_items"]:
        record["state"] = "SUCCEEDED"
    manifest = {"schema_version": "beautyai-generation-comparison-v1", "comparison_id": "synthetic-generation",
                "estimate_rate_version": "synthetic-rate-v1", "pairs": [
                    {"pair_id": "pair-1", "baseline_cost_id": "synthetic-cost-baseline",
                     "candidate_cost_id": "synthetic-cost-candidate"}]}
    previous = build_report(data, accepted_manifest)
    report = build_report(data, accepted_manifest, generation_comparison=manifest)
    assert report["mode"] == "COMPARISON"
    assert report["comparison"] == previous["comparison"]
    assert report["comparison"]["estimated"]["unit_cost_reduction_pct"] == "40.000000000"
    assert report["generation_comparison"]["estimated_cost_reduction_pct"] == "25.000000000"
    html = render_report(report)
    assert "40%" in html and "25%" in html
    assert "채택 광고 한 장당 원가" in html and "생성당 추정비용 비교" in html


def test_generation_manifest_identity_is_canonical_and_matching_or_rate_changes_change_id(generation_example):
    data, manifest = generation_example
    report = build_report(data, generation_comparison=manifest)
    normalized_manifest = normalize_generation_manifest(manifest)
    identity = {"contract_version": CONTRACT_VERSION, "dataset": normalize_dataset(data),
                "comparison": None, "generation_comparison": normalized_manifest}
    assert report["report_id"] == sha256(canonical_bytes(identity)).hexdigest()
    assert report["inputs"]["generation_comparison_sha256"] == sha256(canonical_bytes(normalized_manifest)).hexdigest()
    manifest["pairs"].reverse()
    data["cost_items"].reverse()
    assert build_report(data, generation_comparison=dict(reversed(list(manifest.items())))) == report
    changed = deepcopy(manifest)
    changed["pairs"][0]["candidate_cost_id"], changed["pairs"][1]["candidate_cost_id"] = (
        changed["pairs"][1]["candidate_cost_id"], changed["pairs"][0]["candidate_cost_id"])
    rematched = build_report(data, generation_comparison=changed)
    assert rematched["report_id"] != report["report_id"]
    assert rematched["inputs"]["generation_comparison_sha256"] != report["inputs"]["generation_comparison_sha256"]
    assert rematched["generation_comparison"] == report["generation_comparison"]
    changed = {**manifest, "estimate_rate_version": "other-rate"}
    assert build_report(data, generation_comparison=changed)["report_id"] != report["report_id"]


@pytest.mark.parametrize("baseline,candidate,expected,label", [
    (None, "1", None, "산출 보류"),
    ("0", "1", None, "산출 보류"),
    ("1", "2", "-100.000000000", "생성당 추정비용 증가율"),
    ("1", "0", "100.000000000", "생성당 추정비용 감소율"),
])
def test_generation_unknown_zero_and_increase_keep_their_meaning(generation_example, baseline, candidate, expected, label):
    data, manifest = generation_example
    for index, record in enumerate(data["cost_items"][:20]):
        record["estimated_cost_usd"] = baseline if index < 10 else candidate
    report = build_report(data, generation_comparison=manifest)
    section = report["generation_comparison"]
    assert section["estimated_cost_reduction_pct"] == expected
    html = render_report(report)
    assert label in html
    if baseline is None:
        assert section["compared_record_count"] == 0
        assert section["baseline"]["total_estimated_cost_usd"] is None
        assert section["candidate"]["estimated_cost_per_generation_usd"] is None
        assert "COST_MISSING" in html and "PAIRED_RECORD_EXCLUDED" in html and "NO_COMPARABLE_PAIRS" in html
    elif baseline == "0":
        assert section["baseline"]["total_estimated_cost_usd"] == "0.000000000"
        assert "$0.00" in html and "BASELINE_ZERO_COST" in html
    elif expected.startswith("-"):
        assert "100%" in html and "$-10.00" in html


@pytest.mark.parametrize("mutation", [
    lambda manifest: manifest.update(schema_version="wrong"),
    lambda manifest: manifest.update(pairs=[]),
    lambda manifest: manifest.update(comparison_id="<script>private-token</script>"),
    lambda manifest: manifest["pairs"].append(deepcopy(manifest["pairs"][0])),
    lambda manifest: manifest["pairs"][0].update(candidate_cost_id="missing-cost"),
    lambda manifest: manifest.update(extra="private-token"),
])
def test_generation_rejects_malformed_manifest_before_writing(generation_example, tmp_path, mutation):
    data, manifest = generation_example
    mutation(manifest)
    with pytest.raises(ValueError, match="COST_GENERATION_"):
        write_report(data, output=tmp_path / "out", generation_comparison=manifest)
    assert not (tmp_path / "out").exists()


def test_generation_renderer_escapes_all_added_labels_and_retains_csp(generation_example):
    report = build_report(generation_example[0], generation_comparison=generation_example[1])
    hostile = '\"><script>alert("x")</script><img src="https://example.invalid/x">'
    section = report["generation_comparison"]
    for key in ("comparison_id", "estimate_rate_version", "assurance"):
        section[key] = hostile
    section["reason_codes"] = [hostile]
    section["excluded_records"] = [{"cost_id": hostile, "reason_codes": [hostile]}]
    report["inputs"]["generation_comparison_sha256"] = hostile
    html = render_report(report)
    assert hostile not in html and "&lt;script&gt;" in html
    assert "<script" not in html and "<img" not in html
    assert "script-src 'none'" in html and "connect-src 'none'" in html


def test_generation_cli_and_repeated_write_are_deterministic_and_provider_free(generation_example, tmp_path, capsys, monkeypatch):
    data, manifest = generation_example
    source, manifest_path = tmp_path / "dataset.json", tmp_path / "generation.json"
    source.write_bytes(canonical_bytes(data))
    manifest_path.write_bytes(canonical_bytes(manifest))
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"wandb", "openai", "httpx", "requests", "torch", "diffusers"}
        assert not name.endswith(("cost_wandb", "openai_images", "api_image_processor", "inference_runtime"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    args = ["--dataset", str(source), "--generation-comparison", str(manifest_path), "--output", str(tmp_path / "out")]
    assert main(args) == 0
    response = json.loads(capsys.readouterr().out)
    report_path, html_path = Path(response["report_path"]), Path(response["html_path"])
    before = report_path.read_bytes(), html_path.read_bytes()
    assert response["new_provider_calls"] == 0
    assert json.loads(before[0])["generation_comparison"]["compared_record_count"] == 20
    assert main(args) == 0
    capsys.readouterr()
    assert (report_path.read_bytes(), html_path.read_bytes()) == before
    assert source.read_bytes() == canonical_bytes(data)
    assert manifest_path.read_bytes() == canonical_bytes(manifest)


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":2}', b'{"a":NaN}', b'[]',
                                  b'{"comparison_id":"<script>private-token</script>"}', b'\xff'])
def test_generation_cli_invalid_input_has_safe_error_and_no_report(generation_example, tmp_path, capsys, raw):
    source, manifest = tmp_path / "dataset.json", tmp_path / "generation.json"
    source.write_bytes(canonical_bytes(generation_example[0]))
    manifest.write_bytes(raw)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(source), "--generation-comparison", str(manifest), "--output", str(tmp_path / "out")])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("COST_")
    assert "private-token" not in captured.err and "<script>" not in captured.err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("target_name,hardlink", [("report.json", False), ("index.html", False), ("report.json", True)])
def test_generation_cli_never_overwrites_manifest_input(generation_example, tmp_path, capsys, target_name, hardlink):
    data, manifest = generation_example
    source = tmp_path / "dataset.json"
    source.write_bytes(canonical_bytes(data))
    output = tmp_path / "out"
    output.mkdir()
    manifest_path = tmp_path / "generation.json" if hardlink else output / target_name
    before = canonical_bytes(manifest)
    manifest_path.write_bytes(before)
    if hardlink:
        os.link(manifest_path, output / target_name)
    with pytest.raises(SystemExit) as error:
        main(["--dataset", str(source), "--generation-comparison", str(manifest_path), "--output", str(output)])
    assert error.value.code == 2
    assert manifest_path.read_bytes() == before
    assert "COST_REPORT_INPUT_OVERWRITE" in capsys.readouterr().err


def test_generation_cli_reuses_safe_regular_file_and_size_checks(generation_example, tmp_path, capsys, monkeypatch):
    data, manifest = generation_example
    source, manifest_path = tmp_path / "dataset.json", tmp_path / "generation.json"
    source.write_bytes(canonical_bytes(data))
    manifest_path.write_bytes(canonical_bytes(manifest))
    link = tmp_path / "link.json"
    link.symlink_to(manifest_path)
    fifo = tmp_path / "pipe.json"
    os.mkfifo(fifo)
    for unsafe in (link, fifo, tmp_path):
        with pytest.raises(SystemExit) as error:
            main(["--dataset", str(source), "--generation-comparison", str(unsafe), "--output", str(tmp_path / "out")])
        assert error.value.code == 2
        assert capsys.readouterr().err.startswith("COST_")
    # Let the smaller dataset through while rejecting a large optional input.
    monkeypatch.setattr("beauty_ai.cost_reporting._MAX_JSON_BYTES", len(source.read_bytes()) + 1)
    manifest_path.write_bytes(b" " * (len(source.read_bytes()) + 2))
    with pytest.raises(SystemExit):
        main(["--dataset", str(source), "--generation-comparison", str(manifest_path), "--output", str(tmp_path / "out")])
    assert capsys.readouterr().err == "COST_REPORT_INPUT_INVALID\n"
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("fail", [False, True])
def test_generation_cli_preserves_local_report_when_requested_wandb_is_incomplete(
        generation_example, tmp_path, capsys, monkeypatch, fail):
    data, manifest = generation_example
    source, manifest_path = tmp_path / "dataset.json", tmp_path / "generation.json"
    source.write_bytes(canonical_bytes(data))
    manifest_path.write_bytes(canonical_bytes(manifest))

    def sync(path, **kwargs):
        uploaded = json.loads(Path(path).read_bytes())
        assert uploaded["generation_comparison"]["compared_record_count"] == 20
        assert uploaded["inputs"]["generation_comparison_sha256"]
        assert kwargs == {"entity": "team", "project": "project", "publish": True}
        if fail:
            raise ValueError("private-sdk-credential-text")
        return {"status": "UPLOADED", "error_code": "WANDB_COST_READBACK_INCOMPLETE"}

    monkeypatch.setitem(sys.modules, "beauty_ai.cost_wandb", types.SimpleNamespace(sync_cost_report=sync))
    result = main(["--dataset", str(source), "--generation-comparison", str(manifest_path),
                   "--output", str(tmp_path / "out"), "--wandb", "--wandb-entity", "team",
                   "--wandb-project", "project"])
    assert result == 2
    captured = capsys.readouterr()
    response = json.loads(captured.out)
    assert response["status"] == "LOCAL_REPORT_WRITTEN"
    assert response["wandb_status"] == ("INCOMPLETE" if fail else "UPLOADED")
    assert response["wandb_error_code"] == ("WANDB_COST_SYNC_FAILED" if fail else "WANDB_COST_READBACK_INCOMPLETE")
    assert Path(response["report_path"]).is_file() and Path(response["html_path"]).is_file()
    assert "private-sdk-credential-text" not in captured.out + captured.err
