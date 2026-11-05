"""Saved results remain readable when new provider execution is disabled."""
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError
from src.image_generation.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from . import test_api_image_processor as api
from . import test_backend_adapter as backend


def saved_success(tmp_path):
    calls = []
    proc, assets, attempts = api.processor(tmp_path,
        lambda req: calls.append(1) or api.response(api.png()))
    project, job = uuid4(), uuid4()
    payload = api.request()
    result = proc.process_job(payload, project, job)
    return proc, assets, attempts, project, job, payload, result, calls


def forbid(*args, **kwargs):
    pytest.fail("Saved-result replay must not reserve, upload or execute")


@pytest.mark.parametrize("disabled,zero_budget", [(True, False), (False, True), (True, True)])
def test_saved_success_replay_without_execution_allowance(tmp_path, monkeypatch, disabled, zero_budget):
    proc, assets, attempts, project, job, payload, result, calls = saved_success(tmp_path)
    before = attempts.get(job, project)
    if disabled:
        proc.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    if zero_budget:
        proc.budget_limit_usd = "0"
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    monkeypatch.setattr(attempts, "begin", forbid)
    monkeypatch.setattr(assets, "put_upload", forbid)
    original_read = assets.read_bytes
    reads = []
    def read(ref, *, project_id):
        reads.append((ref, project_id))
        return original_read(ref, project_id=project_id)
    monkeypatch.setattr(assets, "read_bytes", read)
    assert proc.process_job(payload, project, job) == result
    assert reads == [(result.image, project)]
    assert attempts.get(job, project) == before
    assert calls == [1]


@pytest.mark.parametrize("state", ["submitting", "failed", "unknown"])
def test_disabled_provider_does_not_reopen_uncertain_or_failed_job(tmp_path, monkeypatch, state):
    proc, _, attempts = api.processor(tmp_path)
    project, job = uuid4(), uuid4()
    payload = api.request()
    attempt = attempts.begin(job, project, content_hash(payload), budget_limit_usd="1", reservation_usd="1")
    if state != "submitting":
        attempts.finish(job, project, attempt, state=state, error_code="FIXTURE_REVIEW_REQUIRED")
    before = attempts.get(job, project)
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    monkeypatch.setattr(attempts, "begin", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, project, job)
    assert error.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    assert attempts.get(job, project) == before


def test_changed_request_still_conflicts_when_calls_are_disabled(tmp_path, monkeypatch):
    proc, _, attempts, project, job, payload, _, calls = saved_success(tmp_path)
    proc.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    monkeypatch.setattr(attempts, "begin", forbid)
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload.model_copy(update={"quality":"low"}), project, job)
    assert error.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    assert calls == [1]


def test_saved_result_contract_mismatch_cannot_trigger_replacement(tmp_path, monkeypatch):
    proc, _, attempts, project, job, payload, _, calls = saved_success(tmp_path)
    record = attempts.get(job, project)["result"]
    record["image"]["width"] = 64
    with attempts._connection(write=True) as db:
        db.execute("UPDATE provider_attempts SET result_json=? WHERE job_id=?",
                   (json.dumps(record), str(job)))
    proc.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    monkeypatch.setattr(attempts, "begin", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, project, job)
    assert error.value.code == "PROVIDER_ATTEMPT_CONFLICT"
    assert calls == [1]


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_saved_pixels_fail_without_replacement(tmp_path, monkeypatch, damage):
    proc, _, attempts, project, job, payload, result, calls = saved_success(tmp_path)
    files = [p for p in (tmp_path / "assets").rglob("*")
             if p.is_file() and sha256(p.read_bytes()).hexdigest() == result.image.sha256]
    assert len(files) == 1
    if damage == "missing":
        files[0].unlink()
    else:
        files[0].write_bytes(b"broken fixture pixels")
    proc.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    before = attempts.get(job, project)
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    monkeypatch.setattr(attempts, "begin", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, project, job)
    assert error.value.code.startswith("ASSET_")
    assert attempts.get(job, project) == before
    assert calls == [1]


@pytest.mark.parametrize("payload", [api.request().model_copy(update={"width":1}), object()])
def test_malformed_snapshot_keeps_input_error_before_receipt_lookup(tmp_path, monkeypatch, payload):
    proc, _, attempts = api.processor(tmp_path)
    monkeypatch.setattr(attempts, "get", forbid)
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, uuid4(), uuid4())
    assert error.value.code == "PROVIDER_INPUT_INVALID"


def test_foreign_project_cannot_read_saved_image(tmp_path, monkeypatch):
    proc, assets, attempts, project, job, payload, _, calls = saved_success(tmp_path)
    other = uuid4()
    assert attempts.get(job, other) is None
    proc.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    monkeypatch.setattr(assets, "read_bytes", forbid)
    monkeypatch.setattr(attempts, "begin", forbid)
    monkeypatch.setattr(proc.adapter, "execute", forbid)
    with pytest.raises(GatewayError) as error:
        proc.process_job(payload, other, job)
    assert error.value.code == "PROVIDER_CALLS_DISABLED"
    assert calls == [1] and attempts.get(job, project)["state"] == "succeeded"


def test_backend_keeps_mock_and_live_binding_identities_separate(tmp_path):
    calls = []
    adapter = backend.setup(tmp_path, lambda req: calls.append(1) or backend.response())
    project = uuid4()
    payload = backend.body()
    adapter.generate(payload, project_id=project)
    adapter.processor.adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    with pytest.raises(GatewayError) as error:
        adapter.generate(payload, project_id=project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT"
    assert calls == [1]


def test_backend_saved_success_survives_disabling_live_calls(tmp_path, monkeypatch):
    # Exercise a stable non-MockTransport identity with an in-process execute
    # fixture. No credentials/network/provider are used. HTTP transport forbidden.
    adapter = backend.setup(tmp_path, None)
    provider = adapter.processor.adapter
    provider.config = OpenAIImagesConfig(live_calls=True, api_key="fixture-only")
    monkeypatch.setattr(httpx, "HTTPTransport", forbid)
    executes = []
    def fixture_execute(*args, **kwargs):
        executes.append(1)
        return SimpleNamespace(image_bytes=backend.png(size=(1152,1440)),
                               provider_request_id="req-fixture", usage=None)
    monkeypatch.setattr(provider, "execute", fixture_execute)
    payload, project = backend.body(), uuid4()
    result = adapter.generate(payload, project_id=project)
    provider.config = OpenAIImagesConfig()
    adapter.processor.budget_limit_usd = "0"
    monkeypatch.setattr(provider, "execute", forbid)
    monkeypatch.setattr(adapter.processor.attempts, "begin", forbid)
    assert adapter.generate(payload, project_id=project) == result
    assert adapter.get_receipt(payload.job_id, project_id=project) == result.receipt
    assert executes == [1]
