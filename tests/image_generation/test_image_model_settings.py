"""Model migration and model-specific options, without a provider connection."""
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from src.image_generation.backend_adapter import BackendImageAdapter
from src.image_generation.contracts import ExternalImageRequest, content_hash
from src.image_generation.errors import GatewayError
from src.image_generation.korean_image_policy import build_korean_cutout_request
from src.image_generation.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError

from . import test_backend_adapter as backend
from . import test_openai_images as transport


LEGACY_MODELS = ("gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2-2026-04-21")


def test_new_default_and_cutout_snapshot_resolve_high_separately_from_quality():
    request = transport.request()
    cutout = build_korean_cutout_request(transport.asset(transport.picture()))
    for snapshot in (request, cutout):
        assert snapshot.model == "gpt-image-2.6-sunburst"
        assert snapshot.complexity == "high" and snapshot.quality == "medium"
        saved = json.loads(snapshot.model_dump_json())
        assert saved["complexity"] == "high"
        assert ExternalImageRequest.model_validate(saved) == snapshot


@pytest.mark.parametrize("complexity", ["low", "medium", "high"])
def test_supported_complexity_is_in_snapshot_and_json_wire(complexity):
    request = transport.request().model_copy(update={"complexity": complexity})
    def handler(wire):
        body = json.loads(wire.content)
        assert body["model"] == "gpt-image-2.6-sunburst"
        assert body["complexity"] == complexity and body["quality"] == "medium"
        return transport.response()
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handler))
    transport.call(adapter, request)
    assert content_hash(request) != content_hash(request.model_copy(update={"quality": "high"}))


@pytest.mark.parametrize("model,expected_hash", [
    (LEGACY_MODELS[0], "b0479056efcf99bf10e742f83ba2e6af529f593f5eebea0d6d624942d2966ee0"),
    (LEGACY_MODELS[1], "25dd551d8f9075b33bc53e5528167abca93dc87a01a6fa89107f88c34b4c1d95"),
])
def test_explicit_legacy_model_keeps_pre_migration_json_and_hash(model, expected_hash):
    # Exact serialization/hash from e7d2034, before the complexity field existed.
    saved = ('{"kind":"api_image","operation":"generate","pipeline_profile_id":"openai-images",'
             '"pipeline_revision":"2","model":"' + model + '","product_images":[],"reference_images":[],'
             '"prompt":{"positive":"한국어 배경","negative":"","prompt_version":"v1",'
             '"source_revision":1,"language":"ko"},"watermark_policy":"exclude-v1",'
             '"cosmetics_policy_version":"ko-v1","width":1152,"height":1440,"quality":"medium"}')
    request = ExternalImageRequest.model_validate_json(saved)
    assert request.complexity is None
    assert request.model_dump_json() == saved
    assert content_hash(request) == expected_hash
    explicit = ExternalImageRequest(operation="generate", model=model, prompt=request.prompt)
    assert explicit.model_dump_json() == saved
    assert "complexity" not in explicit.model_dump(mode="json")
    cutout = build_korean_cutout_request(transport.asset(transport.picture()), model=model)
    assert cutout.complexity is None and "complexity" not in cutout.model_dump()


@pytest.mark.parametrize("model", LEGACY_MODELS)
@pytest.mark.parametrize("operation", ["generate", "edit", "cutout"])
def test_legacy_transport_omits_unsupported_complexity(model, operation):
    products = [] if operation == "generate" else [transport.picture()]
    request = transport.request(operation, products=products).model_copy(
        update={"model": model, "complexity": None})
    def handler(wire):
        if operation == "generate":
            fields = json.loads(wire.content)
        else:
            fields = {name: value.decode() for name, value, filename, _ in transport.multipart(wire)
                      if filename is None}
        assert fields["model"] == model and fields["quality"] == "medium"
        assert "complexity" not in fields
        return transport.response()
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handler))
    transport.call(adapter, request, products)


@pytest.mark.parametrize("model,complexity", [
    (LEGACY_MODELS[0], "high"), (LEGACY_MODELS[1], "high"),
    ("gpt-image-2.6-sunburst", "auto"), ("gpt-image-2.6-sunburst", True),
])
def test_unsupported_option_fails_before_client_or_binding_io(tmp_path, monkeypatch, model, complexity):
    data = transport.request().model_dump()
    with pytest.raises(ValidationError):
        ExternalImageRequest.model_validate({**data, "model": model, "complexity": complexity})
    transport.no_network(monkeypatch)
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda wire: pytest.fail("Invalid option must not reach the transport")))
    mutated = transport.request().model_copy(update={"model": model, "complexity": complexity})
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        transport.call(adapter, mutated)
    path = tmp_path / "must-not-exist" / "bindings.sqlite3"
    with pytest.raises(ValueError):
        BackendImageAdapter(None, path, model=model, complexity=complexity)
    assert not path.parent.exists()


def test_existing_job_requires_pinned_legacy_config_and_new_model_uses_new_job(tmp_path):
    calls = []
    handler = lambda wire: calls.append(wire) or backend.response()
    project, payload = uuid4(), backend.body()
    old = backend.setup(tmp_path, handler, model=LEGACY_MODELS[0])
    saved = old.generate(payload, project_id=project)
    assert saved.receipt["model"] == LEGACY_MODELS[0]
    assert "complexity" not in saved.receipt
    new = backend.setup(tmp_path, handler)
    # Receipt reads use the saved snapshot, regardless of the new-job settings.
    assert new.get_receipt(payload.job_id, project_id=project) == saved.receipt
    with pytest.raises(GatewayError) as error:
        new.generate(payload, project_id=project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT" and len(calls) == 1
    pinned = backend.setup(tmp_path, lambda wire: pytest.fail("Saved result must not be resubmitted"),
                           model=LEGACY_MODELS[0])
    assert pinned.generate(payload, project_id=project) == saved
    generated = new.generate(backend.body(job_id="new-model-job"), project_id=project)
    assert generated.receipt["model"] == "gpt-image-2.6-sunburst"
    assert generated.receipt["complexity"] == "high" and len(calls) == 2
    with new._connect() as db:
        request_json = db.execute("SELECT request_json FROM backend_image_bindings "
                                  "WHERE service_job_id=?", ("new-model-job",)).fetchone()[0]
    assert json.loads(request_json)["complexity"] == "high"
    lower = backend.setup(tmp_path, handler, complexity="low")
    with pytest.raises(GatewayError) as error:
        lower.generate(backend.body(job_id="new-model-job"), project_id=project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT" and len(calls) == 2
