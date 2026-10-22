from __future__ import annotations

import base64
import hashlib
import io
import json
import traceback
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from beauty_ai.contracts import AssetRef, ExternalImageRequest, ImagePrompt
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig, OpenAIImagesError


def picture(color="red"):
    output = io.BytesIO()
    Image.new("RGBA", (16, 16), color).save(output, format="PNG")
    return output.getvalue()


def asset(data):
    return AssetRef(asset_id=uuid4(), sha256=hashlib.sha256(data).hexdigest(),
                    width=16, height=16, mime_type="image/png")


def request(operation="generate", *, products=(), backgrounds=()):
    prompt = None if operation == "cutout" else ImagePrompt(
        positive="A bright flower studio with space for the ad copy.",
        negative="extra products, extra caps, text", prompt_version="image-v1", source_revision=1)
    return ExternalImageRequest(operation=operation, prompt=prompt,
                                product_images=[asset(data) for data in products],
                                reference_images=[asset(data) for data in backgrounds])


def response(body=None, **kwargs):
    if body is None:
        body = {"data": [{"b64_json": base64.b64encode(picture()).decode()}]}
    return httpx.Response(200, json=body, **kwargs)


def call(adapter, req=None, products=None, backgrounds=None):
    return adapter.execute(req or request(), products or [], backgrounds or [], client_request_id=uuid4())


def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Unexpected HTTP client construction")
    monkeypatch.setattr(httpx, "Client", fail)


def test_disabled_default_stops_before_client_construction(monkeypatch):
    no_network(monkeypatch)
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig())
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_CALLS_DISABLED$"):
        adapter.validate_request(request())
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_CALLS_DISABLED$"):
        call(adapter)


@pytest.mark.parametrize("key", [None, "", "secret\nvalue", "한글", "x" * 513])
def test_live_config_rejects_missing_or_unsafe_key_before_transport(monkeypatch, key):
    no_network(monkeypatch)
    cfg = OpenAIImagesConfig(live_calls=True, api_key=key)
    assert "api_key" not in repr(cfg)
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_CONFIG_INVALID$"):
        call(OpenAIImagesAdapter(cfg))


@pytest.mark.parametrize("change", [
    {"live_calls": 1}, {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": 301},
    {"max_input_image_bytes": 0}, {"max_input_image_bytes": 20 * 1024**2 + 1},
    {"max_input_total_bytes": 100 * 1024**2 + 1}, {"max_response_bytes": True},
    {"max_response_bytes": 32 * 1024**2 + 1}, {"max_output_image_bytes": -1},
])
def test_configuration_limits_are_validated_even_for_mock(change):
    cfg = replace(OpenAIImagesConfig(), **change)
    adapter = OpenAIImagesAdapter(cfg, httpx.MockTransport(lambda req: pytest.fail("Should not send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_CONFIG_INVALID$"):
        call(adapter)


def test_only_explicit_mock_transport_bypasses_disabled_calls():
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), object())
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_CONFIG_INVALID$"):
        adapter.validate_request(request())


def test_generate_is_one_fixed_url_json_call_without_real_key(monkeypatch):
    calls, options = [], []
    original_client = httpx.Client
    def capture_options(**kwargs):
        options.append(kwargs)
        return original_client(**kwargs)
    monkeypatch.setattr(httpx, "Client", capture_options)
    monkeypatch.setenv("HTTPS_PROXY", "http://not-a-provider.invalid:8888")
    def handle(req):
        calls.append(req)
        assert str(req.url) == "https://api.openai.com/v1/images/generations"
        assert req.method == "POST"
        assert req.headers["authorization"] == "Bearer offline-test"
        assert "idempotency-key" not in req.headers
        assert req.headers["accept-encoding"] == "identity"
        assert len(req.headers["x-client-request-id"]) == 36
        payload = json.loads(req.content)
        assert payload["n"] == 1 and payload["output_format"] == "png"
        assert payload["model"] == "gpt-image-2.5-sunburst-2026-09-08"
        assert payload["size"] == "1152x1440" and payload["quality"] == "medium"
        assert "Avoid: extra products, extra caps, text" in payload["prompt"]
        assert "input_fidelity" not in payload and "negative_prompt" not in payload
        return response(headers={"x-request-id": "req_abc-123"})
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(api_key="private-key-that-must-not-be-used"),
                                  httpx.MockTransport(handle))
    result = call(adapter)
    assert result.image_bytes == picture() and result.provider_request_id == "req_abc-123"
    assert result.usage is None
    assert len(calls) == 1 and options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False


def test_live_transport_is_constructed_without_retries_or_environment_proxy(monkeypatch):
    observed = []
    def mock_factory(**kwargs):
        observed.append(kwargs)
        # Even the opt-in configuration is tested entirely with MockTransport.
        return httpx.MockTransport(lambda req: response())
    monkeypatch.setattr(httpx, "HTTPTransport", mock_factory)
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(live_calls=True, api_key="offline-fixture-key"))
    assert call(adapter).image_bytes == picture()
    assert observed == [{"retries": 0, "trust_env": False}]


def multipart(req):
    envelope = b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.content
    return [(part.get_param("name", header="content-disposition"), part.get_payload(decode=True),
             part.get_filename(), part.get_content_type())
            for part in BytesParser(policy=policy.default).parsebytes(envelope).iter_parts()]


@pytest.mark.parametrize("operation,product_count,background_count", [
    ("edit", 5, 5), ("generate", 0, 2), ("cutout", 1, 0),
])
def test_edits_multipart_preserves_all_reference_roles_and_order(operation, product_count, background_count):
    images = [picture((index * 20, 10, 90, 255)) for index in range(product_count + background_count)]
    products, backgrounds = images[:product_count], images[product_count:]
    req = request(operation, products=products, backgrounds=backgrounds)
    def handle(wire):
        assert str(wire.url) == "https://api.openai.com/v1/images/edits"
        parts = multipart(wire)
        assert [content for name, content, _, _ in parts if name == "image[]"] == images
        fields = {name: content.decode() for name, content, filename, _ in parts if filename is None}
        assert fields["n"] == "1" and fields["output_format"] == "png"
        if operation == "cutout":
            assert fields["background"] == "transparent"
            assert "Remove only the background" in fields["prompt"]
        else:
            assert "Avoid: extra products" in fields["prompt"]
            assert "Background references:" in fields["prompt"]
            assert "not additional products" in fields["prompt"]
        return response()
    assert call(OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle)),
                req, products, backgrounds).image_bytes == picture()


@pytest.mark.parametrize("products,backgrounds", [([b""], []), ([bytearray(b"x")], []),
                                                        ([], []), ([b"x", b"y"], []), ([b"x"], [b"y"])])
def test_invalid_byte_lists_fail_before_reservation_or_http(products, backgrounds):
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda req: pytest.fail("No send")))
    req = request("cutout", products=[picture()])
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        adapter.validate_input(req, products, backgrounds)


@pytest.mark.parametrize("cfg", [OpenAIImagesConfig(max_input_image_bytes=1),
                                 OpenAIImagesConfig(max_input_total_bytes=1)])
def test_oversized_inputs_fail_before_http(cfg):
    images = [picture()]
    adapter = OpenAIImagesAdapter(cfg, httpx.MockTransport(lambda req: pytest.fail("No send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        adapter.validate_input(request("cutout", products=images), images, [])


@pytest.mark.parametrize("changes", [{"model": "arbitrary-model"}, {"width": 8192},
                                      {"seed": 42}, {"prompt": None}])
def test_bypassed_model_validation_is_checked_again(changes):
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda req: pytest.fail("No send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        call(adapter, request().model_copy(update=changes))


def test_invalid_client_trace_is_rejected_before_send():
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda req: pytest.fail("No send")))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_INPUT_INVALID$"):
        adapter.execute(request(), [], [], client_request_id="not-a-uuid")


@pytest.mark.parametrize("status,error,expected", [
    (401, {}, "PROVIDER_AUTH"), (403, {}, "PROVIDER_AUTH"),
    (429, {"code": "insufficient_quota"}, "PROVIDER_QUOTA_EXCEEDED"),
    (429, {"code": "rate_limit_exceeded"}, "PROVIDER_RATE_LIMIT"),
    (400, {"code": "content_policy_violation"}, "PROVIDER_POLICY_REJECTED"),
    (400, {"code": "bad_value"}, "PROVIDER_REQUEST_REJECTED"),
    (503, {}, "PROVIDER_SUBMISSION_UNKNOWN"), (408, {}, "PROVIDER_SUBMISSION_UNKNOWN"),
    (409, {}, "PROVIDER_SUBMISSION_UNKNOWN"), (302, {}, "PROVIDER_REQUEST_REJECTED"),
])
def test_http_failures_are_sanitized_and_never_retried(status, error, expected):
    seen = []
    def handle(req):
        seen.append(req)
        return httpx.Response(status, json={"error": {**error, "message": "private-key private-prompt"}},
                              headers={"x-request-id": "req_safe", "location": "https://other.invalid"})
    with pytest.raises(OpenAIImagesError) as found:
        call(OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle)))
    assert found.value.code == expected and str(found.value) == expected
    assert found.value.provider_request_id == "req_safe" and len(seen) == 1
    assert "private" not in "".join(traceback.format_exception(found.value))


@pytest.mark.parametrize("status,expected", [(401, "PROVIDER_AUTH"), (429, "PROVIDER_RATE_LIMIT")])
def test_non_json_error_body_does_not_hide_auth_or_rate_limit(status, expected):
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda req: httpx.Response(status, content=b"private-key gateway error")))
    with pytest.raises(OpenAIImagesError, match=f"^{expected}$"):
        call(adapter)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError, RuntimeError])
def test_lost_acknowledgement_stays_unknown_without_retry_or_cause_leak(error_type):
    seen = []
    def handle(req):
        seen.append(req)
        raise error_type("private-key private-prompt https://sensitive.invalid")
    with pytest.raises(OpenAIImagesError) as found:
        call(OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(handle)))
    assert found.value.code == "PROVIDER_SUBMISSION_UNKNOWN" and len(seen) == 1
    assert found.value.__cause__ is None
    assert "private" not in "".join(traceback.format_exception(found.value))


@pytest.mark.parametrize("body", [None, [], {}, {"data": []}, {"data": [None]},
    {"data": [{"url": "https://sensitive.invalid/image.png"}]},
    {"data": [{"b64_json": "eA==", "url": "https://sensitive.invalid"}]},
    {"data": [{"b64_json": "eA=="}, {"b64_json": "eA=="}]},
    {"data": [{"b64_json": "%%%"}]}, {"data": [{"b64_json": "한글"}]},
    {"data": [{"b64_json": "eA==\n"}]}, {"data": [{"b64_json": "eB=="}]},
    {"data": [{"b64_json": ""}]},
    {"data": [{"b64_json": "eA=="}], "error": {"message": "ambiguous result"}},
])
def test_invalid_json_shape_url_or_noncanonical_base64_is_rejected(body):
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda req: httpx.Response(200, json=body)))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_RESPONSE_INVALID$"):
        call(adapter)


def test_invalid_json_retains_only_sanitized_request_id():
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"private-prompt not json", headers={"x-request-id": "req_safe"})))
    with pytest.raises(OpenAIImagesError) as found:
        call(adapter)
    assert found.value.code == "PROVIDER_RESPONSE_INVALID"
    assert found.value.provider_request_id == "req_safe"


def test_response_stream_is_bounded_without_content_length():
    consumed = []
    class ChunkStream(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(4):
                consumed.append(index)
                yield b"x" * 9
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(max_response_bytes=16), httpx.MockTransport(
        lambda req: httpx.Response(200, stream=ChunkStream())))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_RESPONSE_TOO_LARGE$"):
        call(adapter)
    assert consumed == [0, 1]


def test_declared_body_limit_stops_before_read_and_compression_is_rejected():
    class ForbiddenStream(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Body should not be read")
            yield b""
    for headers, expected in [({"content-length": "999"}, "PROVIDER_RESPONSE_TOO_LARGE"),
                              ({"content-encoding": "gzip"}, "PROVIDER_RESPONSE_INVALID")]:
        adapter = OpenAIImagesAdapter(OpenAIImagesConfig(max_response_bytes=16), httpx.MockTransport(
            lambda req: httpx.Response(200, headers=headers, stream=ForbiddenStream())))
        with pytest.raises(OpenAIImagesError, match=f"^{expected}$"):
            call(adapter)


def test_decoded_image_limit_is_separate_from_json_limit():
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(max_output_image_bytes=1),
                                  httpx.MockTransport(lambda req: response()))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_RESPONSE_TOO_LARGE$"):
        call(adapter)


def test_response_after_deadline_is_uncertain_and_not_retried(monkeypatch):
    import beauty_ai.openai_images as module
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    calls = []
    def handle(req):
        calls.append(req)
        return response()
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(timeout_seconds=0.01), httpx.MockTransport(handle))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_SUBMISSION_UNKNOWN$"):
        call(adapter)
    assert len(calls) == 1


@pytest.mark.parametrize("usage,expected", [(None, None), ({}, None),
    ({"output_tokens": 20}, {"input_tokens": None, "output_tokens": 20, "total_tokens": None,
                             "input_image_tokens": None, "input_text_tokens": None}),
    ({"input_tokens": 11, "output_tokens": 20, "total_tokens": 31,
      "input_tokens_details": {"image_tokens": 10, "text_tokens": 1}},
     {"input_tokens": 11, "output_tokens": 20, "total_tokens": 31,
      "input_image_tokens": 10, "input_text_tokens": 1}),
])
def test_usage_preserves_only_observed_counts(usage, expected):
    body = {"data": [{"b64_json": "eA=="}], "usage": usage}
    result = call(OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda req: response(body))))
    assert (result.usage.model_dump() if result.usage else None) == expected


@pytest.mark.parametrize("usage", [[], {"input_tokens": True}, {"input_tokens": -1},
                                    {"total_tokens": "3"}, {"output_tokens": 2.5},
                                    {"input_tokens_details": []}, {"total_tokens": 2**63}])
def test_malformed_usage_never_becomes_cost_evidence(usage):
    body = {"data": [{"b64_json": "eA=="}], "usage": usage}
    adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(lambda req: response(body)))
    with pytest.raises(OpenAIImagesError, match="^PROVIDER_RESPONSE_INVALID$"):
        call(adapter)


@pytest.mark.parametrize("request_id", ["a" * 201, "req private token", "https://secret.invalid"])
def test_invalid_provider_identifier_is_not_propagated(request_id):
    result = call(OpenAIImagesAdapter(OpenAIImagesConfig(), httpx.MockTransport(
        lambda req: response(headers={"x-request-id": request_id}))))
    assert result.provider_request_id is None
