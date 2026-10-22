"""Compatibility fixtures captured before the optional prompt contract was added."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import BackgroundRequest, ImageJob, ImagePrompt, ImageRequest, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.job_ledger import JobLedger
from beauty_ai.local_processor import LocalTemplateProcessor
from beauty_ai.product_approval import ProductApprovalStore


# This payload and expected SHA were checked with the B3 contract before editing
# contracts.py. Keep them independent of the new model's default serialization.
LEGACY_BODY = {
    "kind": "render_background",
    "source": {"asset_id": "00000000-0000-0000-0000-000000000001", "sha256": "a" * 64,
               "width": 16, "height": 32, "mime_type": "image/png"},
    "product_rgba": {"asset_id": "00000000-0000-0000-0000-000000000002", "sha256": "b" * 64,
                     "width": 512, "height": 512, "mime_type": "image/png"},
    "preserve_mask": {"asset_id": "00000000-0000-0000-0000-000000000003", "sha256": "c" * 64,
                      "width": 512, "height": 512, "mime_type": "image/png"},
    "visual": {"concept": "Bright product photograph", "style_preset_id": "bright-ad",
               "style_preset_version": "1", "layout_template_id": "product-right",
               "layout_template_version": "1", "generation_width": 512, "generation_height": 512,
               "product_box": {"x": 0.6, "y": 0.2, "width": 0.3, "height": 0.65},
               "text_safe_zone": {"x": 0.05, "y": 0.12, "width": 0.45, "height": 0.65}},
    "seed": 42, "pipeline_profile_id": "bright-template", "pipeline_revision": "1",
}
LEGACY_HASH = "0e4c426d79a060fe31feda6776b655077ec484079f06848490bf17f51745ed1c"
PROMPT_BODY = {"positive": "A glass bottle among flowers", "negative": "letters, duplicate product",
               "prompt_version": "image-brief-v1", "source_revision": 3, "language": "en"}


def request_with_prompt(**changes):
    return BackgroundRequest.model_validate({**deepcopy(LEGACY_BODY),
                                            "image_prompt": {**PROMPT_BODY, **changes}})


@pytest.mark.parametrize("explicit_none", [False, True])
def test_legacy_body_and_golden_hash_remain_exact(explicit_none):
    body = deepcopy(LEGACY_BODY)
    if explicit_none:
        body["image_prompt"] = None
    request = BackgroundRequest.model_validate(body)
    assert request.image_prompt is None
    assert request.model_dump(mode="json") == LEGACY_BODY
    assert json.loads(request.model_dump_json()) == LEGACY_BODY
    assert content_hash(request) == LEGACY_HASH
    assert content_hash(LEGACY_BODY) == LEGACY_HASH


def test_real_legacy_queued_record_survives_reload_replay_and_claim(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    job_id, project_id = uuid4(), uuid4()
    # Insert the pre-B4 JSON directly, not through the new submit serializer.
    with sqlite3.connect(ledger.path) as db:
        db.execute("""INSERT INTO jobs
            (job_id, project_id, request_hash, request_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?)""", (
                str(job_id), str(project_id), LEGACY_HASH, json.dumps(LEGACY_BODY),
                "2026-10-22T00:00:00+00:00", "2026-10-22T00:00:00+00:00"))
    reopened = JobLedger(ledger.path)
    saved = reopened.get(job_id, project_id)
    assert saved.status == "queued" and saved.request_hash == LEGACY_HASH
    replay = reopened.submit(job_id, project_id, BackgroundRequest.model_validate(LEGACY_BODY))
    assert replay == saved
    claimed = reopened.claim_next("test-worker")
    assert claimed.request.image_prompt is None
    assert claimed.request.model_dump(mode="json") == LEGACY_BODY
    assert claimed.job.request_hash == LEGACY_HASH
    with pytest.raises(GatewayError) as error:
        reopened.submit(job_id, project_id, request_with_prompt())
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("change", [
    {"positive": "A blue bottle among flowers"},
    {"negative": "letters, duplicate product, harsh lighting"},
    {"prompt_version": "image-brief-v2"},
    {"source_revision": 4},
])
def test_each_effective_prompt_input_invalidates_request_hash(change):
    base, changed = request_with_prompt(), request_with_prompt(**change)
    assert content_hash(base) != LEGACY_HASH
    assert content_hash(changed) != content_hash(base)


def test_adapter_nested_and_json_serialization_keep_prompt_and_legacy_shapes():
    adapter = TypeAdapter(list[ImageRequest])
    legacy = BackgroundRequest.model_validate(LEGACY_BODY)
    prompted = request_with_prompt(positive="  Soft light, product name 은은  ")
    serialized = adapter.dump_json([legacy, prompted])
    restored = adapter.validate_json(serialized)
    assert json.loads(serialized)[0] == LEGACY_BODY
    assert json.loads(serialized)[1]["image_prompt"]["positive"] == "  Soft light, product name 은은  "
    assert restored[1].image_prompt.language == "en"  # Declaration; no language detection or rewrite.
    assert [content_hash(r) for r in restored] == [content_hash(legacy), content_hash(prompted)]
    job = ImageJob(job_id=uuid4(), request_hash=LEGACY_HASH, status="queued", result=None, error_code=None)
    assert "result" in job.model_dump() and job.model_dump()["result"] is None
    assert "error_code" in job.model_dump()  # Do not globally drop existing null fields.


def test_prompt_is_durable_in_job_snapshot(tmp_path):
    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    request = request_with_prompt()
    job_id, project_id = uuid4(), uuid4()
    saved = ledger.submit(job_id, project_id, request)
    reopened = JobLedger(ledger.path)
    assert reopened.get(job_id, project_id).request_hash == content_hash(request)
    claimed = reopened.claim_next("test-worker")
    assert claimed.request.image_prompt.model_dump() == PROMPT_BODY
    assert content_hash(claimed.request) == saved.request_hash


@pytest.mark.parametrize("change", [
    {"positive": ""}, {"positive": "x" * 4001},
    {"negative": "x" * 2001}, {"prompt_version": ""}, {"prompt_version": "v" * 81},
    {"source_revision": 0}, {"source_revision": -1}, {"language": "ko"},
    {"positive": " \t\n"}, {"prompt_version": " \u3000"},
    {"source_revision": True}, {"source_revision": "1"}, {"source_revision": 1.0},
    {"provider_model_path": "/arbitrary/model"},
])
def test_invalid_prompt_is_rejected_without_truncation(change):
    with pytest.raises(ValidationError):
        ImagePrompt.model_validate({**PROMPT_BODY, **change})


def test_prompt_bounds_and_optional_defaults_are_preserved():
    prompt = ImagePrompt(positive="x" * 4000, negative="y" * 2000,
                         prompt_version="v" * 80, source_revision=1)
    assert len(prompt.positive) == 4000 and len(prompt.negative) == 2000
    minimal = ImagePrompt(positive="x", prompt_version="1", source_revision=1)
    assert minimal.negative == "" and minimal.language == "en"


def test_local_template_rejects_prompt_instead_of_silently_ignoring_it(tmp_path):
    assets = LocalAssetStore(tmp_path / "assets")
    approvals = ProductApprovalStore(tmp_path / "products.sqlite3", assets)
    processor = LocalTemplateProcessor(assets, approvals)
    processor.validate_request(BackgroundRequest.model_validate(LEGACY_BODY))
    for action in (lambda: processor.validate_request(request_with_prompt()),
                   lambda: processor(request_with_prompt(), uuid4())):
        with pytest.raises(GatewayError) as error:
            action()
        assert error.value.code == "INVALID_IMAGE_REQUEST"


@pytest.mark.parametrize("model", [BackgroundRequest, ImagePrompt])
def test_published_schema_matches_contract(model):
    path = Path(__file__).resolve().parents[1] / "schemas" / f"{model.__name__}.schema.json"
    assert json.loads(path.read_text()) == model.model_json_schema()
