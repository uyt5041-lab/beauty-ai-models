"""Stage 2 contract checks; fixtures are local, no image-provider calls."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
from uuid import UUID, uuid4

from PIL import Image
import pytest

from beauty_ai.contracts import ExternalImageRequest, content_hash
from beauty_ai.cosmetics_prompts import render_cosmetics_provider_prompt
from beauty_ai.experiment_inputs import prepare_inputs
from beauty_ai.experiment_plan import build_cases
from beauty_ai.langfuse_prompts import cosmetics_prompt_definition


DESCRIPTION = "One clear glass serum bottle with a silver cap and its original printed label."
VARIABLES = ("product_description", "palette_section", "creative_direction_section",
             "composition", "reference_instructions")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def records(products=2, references=0):
    result = []
    for role, count in (("product", products), ("reference", references)):
        for index in range(1, count + 1):
            label = f"{role}-{index:02d}"
            normalized = sha(label)
            result.append({"role": role, "index": index, "name": label + ".jpg",
                           "source_sha256": sha("raw-" + label), "normalized_sha256": normalized,
                           "path": "inputs/" + label + ".png",
                           "asset_ref": {"asset_id": str(uuid4()), "sha256": normalized,
                                         "width": 600, "height": 800, "mime_type": "image/png"}})
    return result


def snapshots(presets=("clean_studio",), version=1):
    return {preset: {**cosmetics_prompt_definition(preset), "version": version} for preset in presets}


def local(items=None, **changes):
    options = {"product_description": DESCRIPTION, "presets": ["clean_studio"],
               "prompt_version": "cosmetics-v1"}
    options.update(changes)
    return build_cases(records() if items is None else items, **options)


def remote(items=None, **changes):
    options = {"product_description": DESCRIPTION, "presets": ["clean_studio"],
               "prompt_version": "1", "prompt_snapshots": snapshots()}
    options.update(changes)
    return build_cases(records() if items is None else items, **options)


def test_local_case_freezes_request_full_provider_prompt_and_template_separately():
    inputs = records(2, 2)
    case, = local(inputs)
    request = ExternalImageRequest.model_validate(case["request"])
    assert case["case_id"] == "case-001" and case["repeat_index"] == 1
    assert str(UUID(case["job_id"])) == case["job_id"]
    assert case["prompt_source"] == "local" and case["prompt_version"] == "cosmetics-v1"
    assert "managed_prompt" not in case["request"]
    assert case["request"]["operation"] == "edit"
    assert case["request"]["product_images"] == [r["asset_ref"] for r in inputs[:2]]
    assert case["request"]["reference_images"] == [r["asset_ref"] for r in inputs[2:]]
    assert case["request_sha256"] == content_hash(request) == content_hash(case["request"])
    assert case["provider_prompt"] == render_cosmetics_provider_prompt(request)
    assert case["provider_prompt_sha256"] == sha(case["provider_prompt"])
    assert case["template_sha256"] == sha(cosmetics_prompt_definition("clean_studio")["prompt"])
    assert "Image 1 is the primary product and viewpoint" in case["provider_prompt"]
    assert "Images 3 through 4" in case["provider_prompt"]
    assert "background composition and lighting" in case["provider_prompt"]


def test_stage_one_records_are_accepted_without_reordering(tmp_path):
    paths = []
    for index, color in enumerate(("red", "blue", "green")):
        path = tmp_path / f"source-{index}.png"
        with Image.new("RGB", (6, 4), color) as image:
            image.save(path)
        paths.append(path)
    inputs = prepare_inputs(paths[:2], paths[2:], tmp_path / "outputs/prepare", uuid4())
    case, = local(inputs)
    assert case["request"]["product_images"] == [r["asset_ref"] for r in inputs[:2]]
    assert case["request"]["reference_images"] == [inputs[2]["asset_ref"]]


def test_selected_presets_repeats_and_job_ids_are_explicit():
    cases = local(presets=["vivid_pop", "clean_studio"], repeats=2)
    assert [(c["preset_id"], c["repeat_index"]) for c in cases] == [
        ("vivid_pop", 1), ("vivid_pop", 2), ("clean_studio", 1), ("clean_studio", 2)]
    assert [c["case_id"] for c in cases] == ["case-001", "case-002", "case-003", "case-004"]
    assert len({c["job_id"] for c in cases}) == 4
    assert cases[0]["request_sha256"] == cases[1]["request_sha256"]
    assert cases[0]["request_sha256"] != cases[2]["request_sha256"]


def test_local_prompt_does_not_inherit_previous_lavender_fixture_instructions():
    cases = local(presets=["clean_studio", "premium_minimal", "vivid_pop"])
    assert len(cases) == 3
    for case in cases:
        assert DESCRIPTION in case["provider_prompt"]
        assert "lavender" not in case["provider_prompt"].lower()
        assert "coral pump" not in case["provider_prompt"].lower()


def test_exact_remote_snapshot_is_frozen_without_mutating_inputs():
    inputs, snapshot = records(3, 1), snapshots(version=7)
    before = deepcopy((inputs, snapshot))
    case, = remote(inputs, prompt_version="7", prompt_snapshots=snapshot)
    managed = case["request"]["managed_prompt"]
    assert managed["name"] == "beautyai/cosmetics/clean-studio" and managed["version"] == 7
    assert managed["rendered_text"] == case["provider_prompt"]
    assert managed["sha256"] == case["provider_prompt_sha256"] == sha(case["provider_prompt"])
    assert case["template_sha256"] == sha(snapshot["clean_studio"]["prompt"])
    assert case["prompt_source"] == "langfuse" and case["prompt_version"] == "7"
    assert not re.search(r"\{\{[a-z_]+\}\}", case["provider_prompt"])
    assert "Images 4 through 4" in case["provider_prompt"]
    assert (inputs, snapshot) == before
    snapshot["clean_studio"]["prompt"] = "later edited remote template"
    assert managed["rendered_text"] == case["provider_prompt"]


def test_hashes_change_for_semantic_inputs_but_not_random_execution_ids():
    inputs = records()
    original, = local(inputs)
    repeat, = local(inputs)
    description, = local(inputs, product_description="One opaque white cosmetic tube with a black cap.")
    quality, = local(inputs, quality="high")
    layout, = local(inputs, layout_id="product_left")
    assert original["job_id"] != repeat["job_id"]
    assert original["request_sha256"] == repeat["request_sha256"]
    assert len({c["request_sha256"] for c in (original, description, quality, layout)}) == 4
    assert original["template_sha256"] == description["template_sha256"]
    assert original["provider_prompt_sha256"] != description["provider_prompt_sha256"]
    assert original["provider_prompt_sha256"] == quality["provider_prompt_sha256"]


def test_remote_version_and_template_changes_affect_frozen_hashes():
    inputs = records()
    first, = remote(inputs)
    second, = remote(inputs, prompt_version="2", prompt_snapshots=snapshots(version=2))
    revised = snapshots()
    revised["clean_studio"]["prompt"] += "\nUse broad diffused lighting."
    third, = remote(inputs, prompt_snapshots=revised)
    assert len({case["request_sha256"] for case in (first, second, third)}) == 3
    assert first["template_sha256"] == second["template_sha256"] != third["template_sha256"]
    assert first["provider_prompt_sha256"] == second["provider_prompt_sha256"] != third["provider_prompt_sha256"]


@pytest.mark.parametrize("presets", [[], ["unknown"], ["clean_studio", "clean_studio"],
                                     ["clean_studio", "premium_minimal", "vivid_pop", "extra"], "clean_studio"])
def test_invalid_preset_selection_is_rejected(presets):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        local(presets=presets)


@pytest.mark.parametrize("repeats", [0, 11, True, 1.0, "1"])
def test_repeats_are_strictly_bounded_integers(repeats):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        local(repeats=repeats)


def test_ten_repeats_and_five_products_five_references_are_allowed():
    assert len(local(records(5, 5), repeats=10)) == 10


@pytest.mark.parametrize("changes", [{"quality": "ultra"}, {"layout_id": "unconfigured"},
                                     {"model": "unknown-model"}, {"product_description": " "},
                                     {"product_description": "a" * 601}, {"product_description": None}])
def test_existing_request_contract_failures_use_safe_codes(changes):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        local(**changes)


@pytest.mark.parametrize("version", ["0", "-1", "1.0", "01", "١", "production", "latest", 1, True])
def test_remote_version_requires_positive_canonical_ascii_integer_string(version):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        remote(prompt_version=version)


def test_local_version_and_snapshot_mode_cannot_be_mixed():
    for options in ({"prompt_version": "1"}, {"prompt_version": "cosmetics-v2"},
                    {"prompt_version": "cosmetics-v1", "prompt_snapshots": snapshots()}):
        with pytest.raises(ValueError, match="^EXPERIMENT_"):
            local(**options)


@pytest.mark.parametrize("mutation", ["version", "bool_version", "name", "type", "extra_preset", "missing_preset", "unknown_variable"])
def test_remote_identity_and_selected_snapshot_set_must_match(mutation):
    snapshot = snapshots()
    if mutation == "version":
        snapshot["clean_studio"]["version"] = 2
    elif mutation == "bool_version":
        snapshot["clean_studio"]["version"] = True
    elif mutation == "name":
        snapshot["clean_studio"]["name"] = "beautyai/cosmetics/vivid-pop"
    elif mutation == "type":
        snapshot["clean_studio"]["type"] = "chat"
    elif mutation == "extra_preset":
        snapshot.update(snapshots(("vivid_pop",)))
    elif mutation == "missing_preset":
        snapshot.clear()
    else:
        snapshot["clean_studio"]["prompt"] += "{{unknown_secret_variable}}"
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        remote(prompt_snapshots=snapshot)


@pytest.mark.parametrize("variable", VARIABLES)
def test_every_required_template_variable_must_be_present(variable):
    snapshot = snapshots()
    snapshot["clean_studio"]["prompt"] = snapshot["clean_studio"]["prompt"].replace("{{" + variable + "}}", "")
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        remote(prompt_snapshots=snapshot)


@pytest.mark.parametrize("products,references", [(0, 0), (1, 0), (6, 0), (2, 6)])
def test_record_counts_are_revalidated_at_plan_boundary(products, references):
    with pytest.raises(ValueError, match="^EXPERIMENT_"):
        local(records(products, references))


@pytest.mark.parametrize("mutation", ["role", "index", "boolean_index", "normalized_sha", "raw_sha",
                                      "asset_id_duplicate", "normalized_duplicate", "mime", "role_order", "missing_ref"])
def test_tampered_records_are_rejected(mutation):
    items = records(2, 1)
    if mutation == "role":
        items[0]["role"] = "untrusted"
    elif mutation == "index":
        items[1]["index"] = 1
    elif mutation == "boolean_index":
        items[0]["index"] = True
    elif mutation == "normalized_sha":
        items[0]["normalized_sha256"] = "0" * 64
    elif mutation == "raw_sha":
        items[0]["source_sha256"] = "secret-invalid-hash"
    elif mutation == "asset_id_duplicate":
        items[1]["asset_ref"]["asset_id"] = items[0]["asset_ref"]["asset_id"]
    elif mutation == "normalized_duplicate":
        items[1]["normalized_sha256"] = items[1]["asset_ref"]["sha256"] = items[0]["normalized_sha256"]
    elif mutation == "mime":
        items[0]["asset_ref"]["mime_type"] = "image/jpeg"
    elif mutation == "role_order":
        items = [items[2], items[0], items[1]]
    else:
        del items[0]["asset_ref"]
    with pytest.raises(ValueError, match="^EXPERIMENT_") as caught:
        local(items)
    assert "secret" not in str(caught.value)


def test_offline_planning_does_not_read_environment_files_or_call_network(monkeypatch):
    items, snapshot = records(), snapshots()
    class NoEnvironment(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("Environment access is forbidden")
        def __getitem__(self, key):
            raise AssertionError("Environment access is forbidden")
    def deny(*args, **kwargs):
        raise AssertionError("No file, process or network access is allowed")
    with monkeypatch.context() as patch:
        patch.setattr(os, "environ", NoEnvironment())
        patch.setattr(socket, "socket", deny)
        patch.setattr(subprocess, "run", deny)
        patch.setattr(Path, "read_text", deny)
        a = local(items)
        b = remote(items, prompt_snapshots=snapshot)
    assert len(a) == len(b) == 1
