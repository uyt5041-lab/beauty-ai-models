"""Offline evidence limits, failed-output observations, and receipt reuse."""
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import sqlite3
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, ImageProviderUsage, content_hash
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import (OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError,
                                    ProviderImageResponse, validate_raw_usage)
from beauty_ai.provider_attempts import ProviderAttemptStore


USAGE = {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
         "input_tokens_details": {"image_tokens": 4, "text_tokens": 1},
         "future_details": {"cached": [None, 0, 1.5]}}


def request():
    return ExternalImageRequest(operation="generate", width=1024, height=1024,
        prompt=ImagePrompt(positive="제품을 위한 밝은 배경", prompt_version="image-v1", source_revision=1))


def png():
    output = BytesIO()
    with Image.new("RGB", (1024, 1024), "white") as image:
        image.save(output, format="PNG")
    return output.getvalue()


def processor(tmp_path, *, encoded=None, usage=USAGE):
    calls = []
    def handler(req):
        calls.append(req)
        body = {"data": [{"b64_json": encoded if encoded is not None else base64.b64encode(png()).decode()}]}
        if usage is not None:
            body["usage"] = usage
        return httpx.Response(200, json=body, headers={"x-request-id": "req_observed"})
    assets = LocalAssetStore(tmp_path / "assets")
    attempts = ProviderAttemptStore(tmp_path / "attempts.sqlite3")
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handler))
    return ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd="10"), calls


@pytest.mark.parametrize("value", [[], "text", True, 1, {"count": True}, {"count": "secret-text"},
    {"count": -1}, {"count": float("nan")}, {"count": float("inf")}, {"count": float("-inf")},
    {"bad-key": 0}, {"_count": 0}, {"한글": 0}, {"A" * 65: 0},
    {"apiKey": 1}, {"api_key": 1}, {"AUTHORIZATION": 1}, {"dbPasswordCount": 1},
    {"secret": 1}, {"credential_count": 1}, {"accessToken": 1}, {"access_token": 1}])
def test_raw_usage_rejects_text_private_keys_and_nonfinite_counts(value):
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_USAGE_INVALID$") as error:
        validate_raw_usage(value)
    assert error.value.usage is None and error.value.raw_usage is None
    assert "secret-text" not in str(error.value)


def test_raw_usage_depth_and_canonical_byte_boundary():
    depth_eight = 0
    for _ in range(8):
        depth_eight = {"a": depth_eight}
    assert validate_raw_usage(depth_eight) == depth_eight
    with pytest.raises(OpenAIImagesError, match="PROVIDER_USAGE_INVALID"):
        validate_raw_usage({"a": depth_eight})
    boundary = {"a": [0] * 32761, "b": 0}
    assert len(json.dumps(boundary, separators=(",", ":")).encode()) == 65535
    assert validate_raw_usage(boundary) == boundary
    boundary["a"].append(0)
    with pytest.raises(OpenAIImagesError, match="PROVIDER_USAGE_INVALID"):
        validate_raw_usage(boundary)


def test_raw_usage_is_detached_and_nullable():
    original = {"a": [None, 0, 1.5, {"nested": 3}]}
    observed = validate_raw_usage(original)
    original["a"][3]["nested"] = 99
    assert observed["a"][3]["nested"] == 3
    assert validate_raw_usage(None) is None


def test_success_observations_metadata_elapsed_and_public_shape(tmp_path, monkeypatch):
    proc, calls = processor(tmp_path)
    times = iter([50.0, 50.25])
    # Replace only the processor's clock dependency, preserving the adapter's
    # independent timeout clock.
    from types import SimpleNamespace
    monkeypatch.setattr("beauty_ai.api_image_processor.time", SimpleNamespace(monotonic=lambda: next(times)))
    project, job, payload = uuid4(), uuid4(), request()
    digest = content_hash(payload)
    result = proc.process_job(payload, project, job)
    receipt = proc.attempts.get(job, project)
    assert len(calls) == 1 and receipt["raw_usage"] == USAGE
    assert receipt["elapsed_seconds"] == 0.25
    assert receipt["metadata"] == {"operation": "generate", "model": payload.model, "quality": payload.quality,
        "width": 1024, "height": 1024, "prompt_version": "image-v1", "request_hash": digest}
    assert receipt["usage"]["input_image_tokens"] == 4
    assert content_hash(payload) == digest
    assert "raw_usage" not in result.model_dump() and "metadata" not in result.model_dump()
    assert "제품" not in json.dumps(receipt["metadata"], ensure_ascii=False)


@pytest.mark.parametrize("failure", ["base64", "pixels", "asset_storage", "unexpected_decode"])
def test_valid_usage_survives_image_and_storage_errors(tmp_path, monkeypatch, failure):
    encoded = "%%%" if failure == "base64" else "eA==" if failure == "pixels" else None
    proc, calls = processor(tmp_path, encoded=encoded)
    def fail(*args, **kwargs):
        raise OSError("private path or provider body")
    if failure == "asset_storage":
        monkeypatch.setattr(proc.assets, "put_upload", fail)
    if failure == "unexpected_decode":
        monkeypatch.setattr(proc.adapter, "_decode_image", fail)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    receipt = proc.attempts.get(job, project)
    assert len(calls) == 1 and receipt["state"] == "failed"
    assert receipt["raw_usage"] == USAGE and receipt["usage"]["total_tokens"] == 12
    assert receipt["provider_request_id"] == "req_observed" and receipt["elapsed_seconds"] >= 0
    assert "private" not in str(error.value) and receipt["result"] is None


@pytest.mark.parametrize("usage", [{"unknown": "private"}, {"total_tokens": 2.5}])
def test_invalid_usage_precedes_broken_image_and_records_null(tmp_path, usage):
    proc, _ = processor(tmp_path, encoded="%%%", usage=usage)
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    receipt = proc.attempts.get(job, project)
    assert error.value.code == receipt["error_code"] == "PROVIDER_USAGE_INVALID"
    assert receipt["raw_usage"] is None and receipt["usage"] is None


def test_missing_usage_does_not_invent_counts(tmp_path):
    proc, _ = processor(tmp_path, usage=None)
    project, job = uuid4(), uuid4()
    proc.process_job(request(), project, job)
    receipt = proc.attempts.get(job, project)
    assert receipt["raw_usage"] is None and receipt["usage"] is None


def test_untrusted_response_cannot_return_success_after_usage_rejected(tmp_path, monkeypatch):
    proc, _ = processor(tmp_path)
    monkeypatch.setattr(proc.adapter, "execute", lambda *a, **kw:
        ProviderImageResponse(png(), "req_safe", ImageProviderUsage(total_tokens=1), {"secret": 1}))
    project, job = uuid4(), uuid4()
    with pytest.raises(GatewayError) as error:
        proc.process_job(request(), project, job)
    receipt = proc.attempts.get(job, project)
    assert error.value.code == "PROVIDER_USAGE_INVALID"
    assert receipt["state"] == "failed" and receipt["result"] is None
    assert receipt["raw_usage"] is None and receipt["usage"] is None


def test_error_observations_cannot_carry_arbitrary_values():
    error = OpenAIImagesError("PROVIDER_RESPONSE_INVALID", raw_usage={"password": 1},
                              usage=ImageProviderUsage(total_tokens=3))
    assert error.code == "PROVIDER_USAGE_INVALID" and error.usage is None and error.raw_usage is None


def test_receipt_reuse_events_are_per_lookup_without_attempt_growth(tmp_path):
    proc, calls = processor(tmp_path)
    project, job, payload = uuid4(), uuid4(), request()
    result = proc.process_job(payload, project, job)
    original = proc.attempts.get(job, project)
    assert proc.attempts.get_reuse_events(job, project) == []
    assert proc.process_job(payload, project, job) == result
    assert proc.process_job(payload, project, job) == result
    events = proc.attempts.get_reuse_events(job, project)
    assert len(events) == 2 and len(calls) == 1 and events[0]["event_id"] != events[1]["event_id"]
    assert all(UUID(row["event_id"]) and row["attempt_id"] == str(result.attempt_id) for row in events)
    assert proc.attempts.get(job, project) == original
    assert proc.attempts.get_reuse_events(job, uuid4()) == []
    with pytest.raises(GatewayError):
        proc.attempts.record_reuse(job, project, "f" * 64)
    assert len(proc.attempts.get_reuse_events(job, project)) == 2


def test_reuse_event_storage_failure_has_no_post_or_replacement(tmp_path):
    proc, calls = processor(tmp_path)
    project, job, payload = uuid4(), uuid4(), request()
    proc.process_job(payload, project, job)
    with sqlite3.connect(proc.attempts.path) as db:
        db.execute("CREATE TRIGGER fail_reuse BEFORE INSERT ON provider_reuse_events BEGIN SELECT RAISE(ABORT,'private'); END")
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, project, job)
    assert error.value.code == "PROVIDER_ATTEMPT_STORAGE_ERROR" and "private" not in str(error.value)
    assert len(calls) == 1 and proc.attempts.get_reuse_events(job, project) == []


def test_old_receipt_migrates_concurrently_without_inventing_observations(tmp_path):
    path, project, job, attempt = tmp_path / "old.sqlite3", uuid4(), uuid4(), uuid4()
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE provider_attempts (job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
            reservation_units INTEGER NOT NULL, provider_request_id TEXT, usage_json TEXT,
            result_json TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute("INSERT INTO provider_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (str(job), str(project), "a" * 64, str(attempt), "unknown", 1000000,
                    None, None, None, "PROVIDER_SUBMISSION_UNKNOWN", "2026-01-01", "2026-01-01"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(pool.map(lambda _: ProviderAttemptStore(path), range(4)))
    receipt = stores[0].get(job, project)
    assert receipt["attempt_id"] == str(attempt) and receipt["reservation_usd"] == "1.000000"
    assert all(receipt[key] is None for key in ("raw_usage", "metadata", "elapsed_seconds", "account_reservation_id"))
    assert stores[0].get_reuse_events(job, project) == []
