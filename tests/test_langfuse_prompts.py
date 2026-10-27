"""Managed prompt fidelity, immutable jobs and safe offline transport checks."""
from __future__ import annotations

import base64
from email import policy
from email.parser import BytesParser
import hashlib
from io import BytesIO
import json
from uuid import UUID, uuid4

import httpx
from PIL import Image
from pydantic import ValidationError
import pytest

from beauty_ai.contracts import (AssetRef, ExternalImageRequest, ImageProviderUsage,
                                ManagedPromptSnapshot, content_hash)
from beauty_ai.cosmetics_prompts import (
    CosmeticsPromptBrief, PRESETS, build_cosmetics_cutout_request,
    build_cosmetics_request, render_cosmetics_provider_prompt,
)
from beauty_ai.langfuse_prompts import (
    begin_image_generation, cosmetics_prompt_definition, cosmetics_prompt_variables,
    pin_cosmetics_prompt,
)
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError


def asset(number=1):
    return AssetRef(asset_id=UUID(int=number), sha256=f"{number:064x}", width=16,
                    height=16, mime_type="image/png")


def brief(**changes):
    return CosmeticsPromptBrief(product_description="A lavender serum bottle with its original printed label.",
                                source_revision=3, **changes)


def snapshot(text="Frozen managed prompt.", **changes):
    values = {"name": "beautyai/cosmetics/clean-studio", "version": 1,
              "rendered_text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return ManagedPromptSnapshot.model_validate({**values, **changes})


def png():
    with Image.new("RGB", (16, 16), "white") as image:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


def make_managed(source=None, *, remote_changes=None, products=1, references=0, operation="edit"):
    source = source or brief()
    request = build_cosmetics_request(source,
        product_images=[asset(i + 1) for i in range(products)],
        reference_images=[asset(i + 10) for i in range(references)], operation=operation)
    remote = {**cosmetics_prompt_definition(source.preset_id), "version": 1,
              **(remote_changes or {})}
    variables = cosmetics_prompt_variables(source, request)
    return request, remote, variables, pin_cosmetics_prompt(request, remote, variables)


@pytest.mark.parametrize("preset_id", [item.id for item in PRESETS])
@pytest.mark.parametrize("layout_id", ["product_right", "product_left", "product_center_bottom"])
@pytest.mark.parametrize("custom", [False, True])
def test_initial_remote_template_preserves_entire_effective_b11_prompt(preset_id, layout_id, custom):
    source = brief(preset_id=preset_id, layout_id=layout_id,
                   brand_colors=["#1155EE", "#FF7733"] if custom else [],
                   creative_direction="Place one white flower beside the bottle, outside the copy area." if custom else "")
    request, remote, _, pinned = make_managed(source, products=2, references=2)
    assert pinned.managed_prompt.rendered_text == render_cosmetics_provider_prompt(request)
    assert OpenAIImagesAdapter._prompt(pinned) == render_cosmetics_provider_prompt(request)
    assert pinned.managed_prompt.name == remote["name"]
    assert pinned.managed_prompt.version == 1
    assert pinned.product_images == request.product_images
    assert pinned.reference_images == request.reference_images
    assert pinned.prompt == request.prompt
    assert request.managed_prompt is None


def test_initial_concept_template_preserves_no_photo_warning():
    request, _, _, pinned = make_managed(products=0, references=1, operation="generate")
    assert OpenAIImagesAdapter._prompt(pinned) == render_cosmetics_provider_prompt(request)
    assert "CONCEPT ONLY" in OpenAIImagesAdapter._prompt(pinned)


def test_unmanaged_request_serialization_omits_new_optional_field():
    request = build_cosmetics_request(brief(), product_images=[asset()])
    original = request.model_dump(mode="json")
    assert "managed_prompt" not in original
    assert "managed_prompt" not in json.loads(request.model_dump_json())
    assert content_hash(request) == content_hash(original)
    assert ExternalImageRequest.model_validate(original).managed_prompt is None


def test_remote_version_and_intentional_edit_change_hash_and_frozen_prompt():
    request, remote, variables, first = make_managed()
    second = pin_cosmetics_prompt(request, {**remote, "version": 2}, variables)
    third = pin_cosmetics_prompt(request, {**remote, "version": 3,
        "prompt": remote["prompt"] + "\nUse a soft diagonal background gradient."}, variables)
    assert len({content_hash(request), content_hash(first), content_hash(second), content_hash(third)}) == 4
    assert OpenAIImagesAdapter._prompt(first) == OpenAIImagesAdapter._prompt(second)
    assert OpenAIImagesAdapter._prompt(third).endswith("Use a soft diagonal background gradient.")
    remote["prompt"] = "Changed remotely after job creation."
    assert OpenAIImagesAdapter._prompt(first) != remote["prompt"]
    assert content_hash(first) == content_hash(ExternalImageRequest.model_validate_json(first.model_dump_json()))


@pytest.mark.parametrize("change", [
    {"version": 0}, {"version": True}, {"version": "1"}, {"rendered_text": ""},
    {"rendered_text": "x" * 16001}, {"sha256": "0" * 64},
    {"sha256": "not-a-digest"}, {"name": ""}, {"name": "beautyai/../secret"},
])
def test_snapshot_rejects_invalid_identity_or_integrity(change):
    with pytest.raises(ValidationError):
        snapshot(**change)


def test_snapshot_cannot_be_attached_to_legacy_or_cutout_requests():
    request = build_cosmetics_request(brief(), product_images=[asset()])
    for invalid in (
        {**request.model_dump(), "pipeline_revision": "1", "managed_prompt": snapshot().model_dump()},
        {**build_cosmetics_cutout_request(asset()).model_dump(), "managed_prompt": snapshot().model_dump()},
    ):
        with pytest.raises(ValidationError):
            ExternalImageRequest.model_validate(invalid)


def test_pinned_snapshot_rejects_in_place_mutation():
    saved = snapshot()
    with pytest.raises(ValidationError):
        saved.version = 2
    with pytest.raises(ValidationError):
        saved.rendered_text = "Different content"


@pytest.mark.parametrize("change", [{"type": "chat"}, {"version": 0}, {"version": True},
                                     {"name": "unrelated-project/prompt"}, {"prompt": "{{missing_variable}}"}])
def test_remote_prompt_contract_rejects_malformed_or_unrelated_response(change):
    request, remote, variables, _ = make_managed()
    with pytest.raises(ValueError):
        pin_cosmetics_prompt(request, {**remote, **change}, variables)


def test_template_variable_values_are_literal_and_are_never_rendered_recursively():
    source = brief(creative_direction="Keep the literal token {{missing_variable}} as supplied reference text.")
    request, _, _, pinned = make_managed(source)
    assert source.creative_direction in OpenAIImagesAdapter._prompt(pinned)
    assert OpenAIImagesAdapter._prompt(pinned) == render_cosmetics_provider_prompt(request)


def test_missing_or_unknown_variables_are_rejected_before_provider_call():
    request, remote, variables, _ = make_managed()
    incomplete = dict(variables)
    incomplete.pop(next(iter(incomplete)))
    for invalid in (incomplete, {**variables, "undeclared_secret": "never-send-this"}):
        with pytest.raises(ValueError):
            pin_cosmetics_prompt(request, remote, invalid)


def test_edit_transport_sends_exact_pinned_text_once():
    _, _, _, request = make_managed(remote_changes={"prompt": "A reviewed remote art direction."})
    calls = []
    def handle(wire):
        calls.append(wire)
        envelope = b"Content-Type: " + wire.headers["content-type"].encode() + b"\r\n\r\n" + wire.content
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                  for part in BytesParser(policy=policy.default).parsebytes(envelope).iter_parts()}
        assert fields["prompt"].decode() == request.managed_prompt.rendered_text
        assert fields["prompt"].decode().startswith("A reviewed remote art direction.")
        assert "managed_prompt" not in fields
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png()).decode()}]})
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle))
    adapter.execute(request, [png()], [], client_request_id=uuid4())
    assert len(calls) == 1


def test_tampered_model_copy_is_revalidated_before_any_network_attempt():
    _, _, _, request = make_managed()
    corrupt = request.managed_prompt.model_copy(update={"rendered_text": "Unrecorded different prompt."})
    request = request.model_copy(update={"managed_prompt": corrupt})
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda _: pytest.fail("Unexpected send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        adapter.execute(request, [png()], [], client_request_id=uuid4())


class FakeObservation:
    trace_id = "a" * 32
    id = "b" * 16

    def __init__(self, fail=None):
        self.fail = fail
        self.updates = []
        self.end_calls = 0

    def update(self, **values):
        if self.fail == "update":
            raise RuntimeError("secret-response-body-that-must-never-leave-sdk")
        self.updates.append(values)

    def end(self):
        self.end_calls += 1
        if self.fail == "end":
            raise RuntimeError("secret-response-body-that-must-never-leave-sdk")


class FakeClient:
    def __init__(self, fail=None):
        self.fail = fail
        self.starts = []
        self.flush_calls = 0
        self.observation = FakeObservation(fail)

    def start_observation(self, **values):
        self.starts.append(values)
        if self.fail == "start":
            raise RuntimeError("secret-response-body-that-must-never-leave-sdk")
        return self.observation

    def flush(self):
        self.flush_calls += 1
        if self.fail == "flush":
            raise RuntimeError("secret-response-body-that-must-never-leave-sdk")


def begin_with_fake(client):
    pytest.importorskip("langfuse")
    _, _, _, request = make_managed(products=2, references=1)
    return request, begin_image_generation(request, run_id="comparison-run-01",
        job_id="image-job-01", request_hash=content_hash(request), client=client)


def test_missing_credentials_return_not_configured_without_importing_sdk(monkeypatch):
    import builtins
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "langfuse" or name.startswith("langfuse."):
            pytest.fail("Missing credentials must not import or initialize the SDK")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    _, _, _, request = make_managed()
    trace = begin_image_generation(request, run_id="run", job_id="job", request_hash=content_hash(request))
    assert trace.status == "NOT_CONFIGURED"
    assert trace.finish() == {"status": "NOT_CONFIGURED", "readback_verified": False}


def test_generation_records_frozen_prompt_identity_and_only_selected_asset_metadata():
    client = FakeClient()
    request, trace = begin_with_fake(client)
    assert trace.status == "STARTED"
    assert len(client.starts) == 1
    observed = client.starts[0]
    assert observed["name"] == "edit-cosmetics-image"
    assert observed["as_type"] == "generation" and observed["model"] == request.model
    assert observed["prompt"].name == request.managed_prompt.name
    assert observed["prompt"].version == request.managed_prompt.version
    assert observed["input"] == {
        "prompt": request.managed_prompt.rendered_text,
        "product_image_sha256": [item.sha256 for item in request.product_images],
        "reference_image_sha256": [item.sha256 for item in request.reference_images],
    }
    assert observed["metadata"]["request_hash"] == content_hash(request)
    assert observed["metadata"]["rendered_prompt_sha256"] == request.managed_prompt.sha256
    assert "asset_id" not in json.dumps(observed["metadata"])
    assert observed["model_parameters"] == {
        "operation": "edit", "quality": "medium", "size": "1152x1440", "n": 1}


def test_finish_uses_disjoint_token_modalities_without_claiming_confirmed_cost_or_delivery():
    client = FakeClient()
    _, trace = begin_with_fake(client)
    usage = ImageProviderUsage(input_tokens=102, input_text_tokens=12,
        input_image_tokens=90, output_tokens=30, total_tokens=132)
    receipt = trace.finish(output_sha256="c" * 64, usage=usage,
        provider_request_id="req-safe-id", estimated_cost_usd=0.01234)
    assert receipt == {"status": "FLUSH_REQUESTED", "readback_verified": False,
                       "trace_id": "a" * 32, "observation_id": "b" * 16}
    update = client.observation.updates[0]
    assert update["usage_details"] == {"input_text": 12, "input_image": 90, "output_image": 30}
    assert "cost_details" not in update
    assert update["metadata"]["estimated_cost_usd"] == 0.01234
    assert update["metadata"]["actual_cost_usd"] is None
    assert update["metadata"]["billing_status"] == "UNRECONCILED"
    assert update["metadata"]["provider_usage"]["total_tokens"] == 132
    assert update["output"] == {"image_sha256": "c" * 64, "human_review": "PENDING",
                                "quality_approved": False}
    assert trace.finish(output_sha256="d" * 64) == receipt
    assert len(client.observation.updates) == client.observation.end_calls == client.flush_calls == 1


def test_finish_retains_aggregate_input_only_when_no_modalities_were_reported():
    client = FakeClient()
    _, trace = begin_with_fake(client)
    trace.finish(usage=ImageProviderUsage(input_tokens=44, output_tokens=3, total_tokens=47))
    assert client.observation.updates[0]["usage_details"] == {"input": 44, "output_image": 3}


def test_missing_usage_and_cost_are_unknown_instead_of_zero():
    client = FakeClient()
    _, trace = begin_with_fake(client)
    trace.finish()
    update = client.observation.updates[0]
    assert update["usage_details"] is None
    assert update["metadata"]["estimated_cost_usd"] is None
    assert update["metadata"]["actual_cost_usd"] is None
    assert "provider_usage" not in update["metadata"]


def test_provider_failure_records_only_safe_code_and_does_not_retry_any_operation():
    client = FakeClient()
    _, trace = begin_with_fake(client)
    receipt = trace.finish(error_code="PROVIDER_SUBMISSION_UNKNOWN", provider_request_id="req-unknown")
    assert receipt["status"] == "FLUSH_REQUESTED"
    assert client.observation.updates[0]["level"] == "ERROR"
    assert client.observation.updates[0]["status_message"] == "PROVIDER_SUBMISSION_UNKNOWN"
    assert len(client.starts) == client.flush_calls == 1


@pytest.mark.parametrize("failure", ["start", "update", "end", "flush"])
def test_sdk_failures_are_nonblocking_sanitized_and_not_retried(failure):
    client = FakeClient(failure)
    _, trace = begin_with_fake(client)
    receipt = trace.finish(output_sha256="c" * 64)
    assert receipt == {"status": "OBSERVABILITY_FAILED", "readback_verified": False}
    assert "secret-response-body" not in repr(receipt)
    assert trace.finish() == receipt
    assert len(client.starts) == 1
    assert client.flush_calls <= 1


@pytest.mark.parametrize("values", [
    {"provider_request_id": "auth-key\nsecret"}, {"error_code": "secret HTTP response"},
    {"output_sha256": "file:///private/image.png"}, {"estimated_cost_usd": float("nan")},
    {"estimated_cost_usd": -1}, {"estimated_cost_usd": True},
])
def test_invalid_telemetry_values_fail_closed_without_leaking_into_observation(values):
    client = FakeClient()
    _, trace = begin_with_fake(client)
    assert trace.finish(**values) == {"status": "OBSERVABILITY_FAILED", "readback_verified": False}
    assert client.observation.updates == []
