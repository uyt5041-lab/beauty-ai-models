"""Stage 4 frozen-plan checks. All inputs and prompt snapshots are local."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

from PIL import Image
import pytest

from beauty_ai.contracts import content_hash
from beauty_ai.experiment_runner import load_verified_plan, main, prepare_experiment
from beauty_ai.langfuse_prompts import cosmetics_prompt_definition


DESCRIPTION = "One clear cosmetic serum bottle with its original silver cap and printed label."


def sources(tmp_path, *, references=1):
    paths = []
    for index in range(2 + references):
        path = tmp_path / f"source-{index}.png"
        with Image.new("RGB", (12, 8), (30 + index * 30, 80, 160)) as image:
            image.save(path)
        paths.append(path)
    return paths[:2], paths[2:]


def prepare(tmp_path, **changes):
    products, references = sources(tmp_path)
    options = {"products": products, "references": references,
               "output": tmp_path / "outputs/preview", "product_description": DESCRIPTION,
               "presets": ["clean_studio"], "prompt_version": "cosmetics-v1", "max_calls": 1}
    options.update(changes)
    return prepare_experiment(**options)


def envelope(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rewrite(path, value, *, reseal=False):
    if reseal:
        value["sha256"] = content_hash(value["plan"])
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def invalid(path, **options):
    with pytest.raises(ValueError, match="^EXPERIMENT_") as caught:
        load_verified_plan(path, **options)
    return str(caught.value)


def test_prepare_freezes_selected_cases_inputs_requests_and_full_prompts(tmp_path):
    path = prepare(tmp_path, presets=["vivid_pop", "clean_studio"], max_calls=2)
    assert path == tmp_path / "outputs/preview/plan.json"
    saved = envelope(path)
    root, plan, digest = load_verified_plan(path, expected_sha256=saved["sha256"])
    assert root == path.parent
    assert plan == saved["plan"] and digest == saved["sha256"] == content_hash(plan)
    assert plan["schema_version"] == "beautyai-experiment-v1"
    assert str(UUID(plan["project_id"])) == plan["project_id"]
    assert plan["run_id"] and plan["created_at"]
    assert plan["max_calls"] == plan["planned_calls"] == 2
    assert plan["review_status"] == "NOT_APPROVED"
    assert plan["billing_status"] == "UNRECONCILED" and plan["actual_cost_usd"] is None
    assert [(r["role"], r["index"]) for r in plan["inputs"]] == [
        ("product", 1), ("product", 2), ("reference", 1)]
    assert [case["preset_id"] for case in plan["cases"]] == ["vivid_pop", "clean_studio"]
    assert len({case["job_id"] for case in plan["cases"]}) == 2
    for case in plan["cases"]:
        folder = root / "cases" / case["case_id"]
        assert json.loads((folder / "request.json").read_text()) == case["request"]
        assert (folder / "provider-prompt.txt").read_text() == case["provider_prompt"]
        assert case["request"]["product_images"] == [r["asset_ref"] for r in plan["inputs"][:2]]
        assert case["request"]["reference_images"] == [plan["inputs"][2]["asset_ref"]]
        assert case["prompt_source"] == "local" and case["prompt_version"] == "cosmetics-v1"
        assert hashlib.sha256(case["provider_prompt"].encode()).hexdigest() == case["provider_prompt_sha256"]
    assert (root / "index.html").is_file()


@pytest.mark.parametrize("cap", [0, 31, True, 1.0, "1"])
def test_invalid_cap_is_rejected_before_output_creation(tmp_path, cap):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        prepare(tmp_path, max_calls=cap)
    assert not (tmp_path / "outputs/preview").exists()


def test_planned_calls_over_cap_are_rejected_before_output_creation(tmp_path):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        prepare(tmp_path, presets=["clean_studio", "vivid_pop"], repeats=2, max_calls=3)
    assert not (tmp_path / "outputs/preview").exists()


def test_existing_preview_is_never_overwritten(tmp_path):
    path = prepare(tmp_path)
    original = path.read_bytes()
    sentinel = path.parent / "do-not-change.txt"
    sentinel.write_text("existing human notes")
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        prepare(tmp_path, product_description="A different cosmetic product.")
    assert path.read_bytes() == original and sentinel.read_text() == "existing human notes"


def test_prepare_and_verification_are_network_and_environment_free(tmp_path, monkeypatch):
    products, references = sources(tmp_path)
    snapshot = {"clean_studio": {**cosmetics_prompt_definition("clean_studio"), "version": 7}}

    class NoEnvironment(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("Preview must not read credentials or environment")

        def __getitem__(self, key):
            raise AssertionError("Preview must not read credentials or environment")

    def deny(*args, **kwargs):
        raise AssertionError("Preview must not use network or subprocesses")

    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", NoEnvironment())
        patch.setattr(socket, "socket", deny)
        patch.setattr(socket, "create_connection", deny)
        patch.setattr(subprocess, "run", deny)
        path = prepare_experiment(products=products, references=references,
                                  output=tmp_path / "outputs/offline", product_description=DESCRIPTION,
                                  presets=["clean_studio"], prompt_version="7", prompt_snapshots=snapshot)
        _, plan, digest = load_verified_plan(path)
    assert plan["planned_calls"] == 1 and digest == content_hash(plan)
    assert not list(path.parent.rglob("provider-attempts.sqlite*"))


def test_html_escapes_user_text_and_identifies_product_reference_and_plan(tmp_path):
    description = 'Cosmetic bottle <script>alert("preview")</script> & original label.'
    path = prepare(tmp_path, product_description=description)
    html = (path.parent / "index.html").read_text(encoding="utf-8")
    assert '<script>alert("preview")</script>' not in html
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert "제품" in html and "참고" in html
    assert "0회" in html
    assert envelope(path)["sha256"] in html


def test_saved_remote_version_and_provider_text_do_not_follow_later_snapshot_edits(tmp_path):
    snapshot = {"clean_studio": {**cosmetics_prompt_definition("clean_studio"), "version": 7}}
    path = prepare(tmp_path, prompt_version="7", prompt_snapshots=snapshot)
    before = deepcopy(envelope(path))
    snapshot["clean_studio"]["version"] = 8
    snapshot["clean_studio"]["prompt"] = "Changed after preview"
    _, plan, _ = load_verified_plan(path)
    case, = plan["cases"]
    assert envelope(path) == before
    assert case["prompt_source"] == "langfuse" and case["prompt_version"] == "7"
    assert case["request"]["managed_prompt"]["version"] == 7
    assert case["provider_prompt"] == case["request"]["managed_prompt"]["rendered_text"]
    assert "Changed after preview" not in case["provider_prompt"]


@pytest.mark.parametrize("mutation", ["prompt", "name", "version", "missing"])
def test_saved_remote_snapshot_must_match_the_frozen_plan(tmp_path, mutation):
    snapshot = {"clean_studio": {**cosmetics_prompt_definition("clean_studio"), "version": 7}}
    path = prepare(tmp_path, prompt_version="7", prompt_snapshots=snapshot)
    snapshot_path = path.parent / "prompt-snapshots.json"
    if mutation == "missing":
        snapshot_path.unlink()
    else:
        saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
        saved["clean_studio"][mutation] = {
            "prompt": "A different historical template.",
            "name": "beautyai/cosmetics/vivid-pop",
            "version": 8,
        }[mutation]
        snapshot_path.write_text(json.dumps(saved), encoding="utf-8")
    invalid(path)


def test_changed_envelope_or_expected_digest_is_rejected(tmp_path):
    path = prepare(tmp_path)
    invalid(path, expected_sha256="0" * 64)
    saved = envelope(path)
    saved["plan"]["max_calls"] = 2
    rewrite(path, saved)
    invalid(path)


@pytest.mark.parametrize("target", ["input_copy", "runtime_image", "request_file", "prompt_file"])
def test_modified_durable_input_or_case_artifacts_are_rejected(tmp_path, target):
    path = prepare(tmp_path)
    plan = envelope(path)["plan"]
    if target == "input_copy":
        changed = path.parent / plan["inputs"][0]["path"]
    elif target == "runtime_image":
        changed = next((path.parent / "runtime/assets").rglob("*.png"))
    elif target == "request_file":
        changed = path.parent / "cases/case-001/request.json"
    else:
        changed = path.parent / "cases/case-001/provider-prompt.txt"
    changed.write_bytes(b"changed-artifact")
    invalid(path)


@pytest.mark.parametrize("mutation", ["schema", "boolean_cap", "cap_too_large", "planned_count",
                                      "case_id", "job_id", "request_hash", "provider_hash",
                                      "provider_text", "product_reference_order", "input_path",
                                      "local_version", "template_hash", "remote_version"])
def test_resealed_plan_still_checks_contract_hashes_and_request_bindings(tmp_path, mutation):
    options = {"presets": ["clean_studio", "vivid_pop"], "max_calls": 2}
    if mutation == "remote_version":
        options.update(prompt_version="7", prompt_snapshots={
            preset: {**cosmetics_prompt_definition(preset), "version": 7}
            for preset in options["presets"]})
    path = prepare(tmp_path, **options)
    saved = envelope(path)
    plan, case = saved["plan"], saved["plan"]["cases"][0]
    if mutation == "schema":
        plan["schema_version"] = "other-schema"
    elif mutation == "boolean_cap":
        plan["max_calls"] = True
    elif mutation == "cap_too_large":
        plan["max_calls"] = 31
    elif mutation == "planned_count":
        plan["planned_calls"] = 1
    elif mutation == "case_id":
        plan["cases"][1]["case_id"] = case["case_id"]
    elif mutation == "job_id":
        plan["cases"][1]["job_id"] = case["job_id"]
    elif mutation == "request_hash":
        case["request"]["quality"] = "high"
    elif mutation == "provider_hash":
        case["provider_prompt_sha256"] = "0" * 64
    elif mutation == "provider_text":
        case["provider_prompt"] = "Replace the product with an unrelated object."
        case["provider_prompt_sha256"] = hashlib.sha256(case["provider_prompt"].encode()).hexdigest()
    elif mutation == "product_reference_order":
        case["request"]["product_images"].reverse()
        case["request_sha256"] = content_hash(case["request"])
    elif mutation == "input_path":
        plan["inputs"][0]["path"] = "../outside.png"
    elif mutation == "local_version":
        case["prompt_version"] = "production"
    elif mutation == "template_hash":
        case["template_sha256"] = "0" * 64
    else:
        case["prompt_version"] = "8"
    rewrite(path, saved, reseal=True)
    invalid(path)


def test_corrupt_plan_error_does_not_include_untrusted_text(tmp_path):
    path = prepare(tmp_path)
    secret = "sk-private-fixture-do-not-echo"
    path.write_text('{"plan": ' + secret)
    assert secret not in invalid(path)


def test_cli_prepare_accepts_source_alias_and_saves_a_preview_only(tmp_path, capsys):
    products, references = sources(tmp_path)
    output = tmp_path / "outputs/cli-preview"
    assert main(["prepare", "--product", str(products[0]), "--source", str(products[1]),
                 "--reference", str(references[0]), "--preset", "clean_studio",
                 "--prompt-version", "cosmetics-v1", "--max-calls", "1",
                 "--product-description", DESCRIPTION, "--output", str(output)]) == 0
    message = json.loads(capsys.readouterr().out)
    assert message["status"] == "PREPARED" and message["image_api_calls"] == 0
    _, plan, _ = load_verified_plan(output / "plan.json")
    assert plan["planned_calls"] == 1
    assert [record["role"] for record in plan["inputs"]] == ["product", "product", "reference"]
    assert plan["actual_cost_usd"] is None and plan["review_status"] == "NOT_APPROVED"


def test_cli_execute_requires_explicit_flag_before_delegating(tmp_path, monkeypatch):
    path = prepare(tmp_path)
    called = []

    def fake_execute(*args, **kwargs):
        called.append((args, kwargs))
        return {"status": "SUCCEEDED"}

    monkeypatch.setitem(sys.modules, "beauty_ai.experiment_execution", SimpleNamespace(execute_plan=fake_execute))
    args = ["execute", "--plan", str(path), "--expect-sha256", envelope(path)["sha256"],
            "--env-file", str(tmp_path / "unused.env"), "--budget-usd", "1"]
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2 and called == []
    assert main([*args, "--execute"]) == 0
    assert len(called) == 1


def test_cli_execution_error_is_nonzero_and_sanitized(tmp_path, monkeypatch, capsys):
    path = prepare(tmp_path)
    secret = "sk-private-fixture-do-not-echo"

    def failed_execute(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setitem(sys.modules, "beauty_ai.experiment_execution", SimpleNamespace(execute_plan=failed_execute))
    status = main(["execute", "--plan", str(path), "--expect-sha256", envelope(path)["sha256"],
                   "--env-file", str(tmp_path / "unused.env"), "--budget-usd", "1", "--execute"])
    captured = capsys.readouterr()
    assert status == 1
    assert secret not in captured.out + captured.err
