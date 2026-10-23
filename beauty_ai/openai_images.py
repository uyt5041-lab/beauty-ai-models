"""Bounded, opt-in OpenAI Images transport. No network calls occur on import.

The adapter submits at most once. A client trace ID is not an external billing
idempotency key, and generated cutouts are not proof of product preservation.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
import time
from dataclasses import dataclass, field
from uuid import UUID

import httpx
from pydantic import ValidationError

from .contracts import ExternalImageRequest, ImageProviderUsage
from .cosmetics_prompts import render_cosmetics_provider_prompt

_MIB = 1024 * 1024
_BASE_URL = "https://api.openai.com/v1/"
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", re.ASCII)
_CUTOUT_PROMPT = (
    "Remove only the background from the provided product photo and return a "
    "transparent PNG. Keep the same product view, shape, color, label, logo and "
    "small printed details. Do not add, duplicate or redesign any product part. "
    "Do not add text, props, a background, or a new cast shadow."
)


class OpenAIImagesError(RuntimeError):
    """Only a stable code and validated request ID may leave the adapter."""

    def __init__(self, code: str, *, provider_request_id: str | None = None):
        if re.fullmatch(r"PROVIDER_[A-Z0-9_]{1,70}", code, re.ASCII) is None:
            code = "PROVIDER_RESPONSE_INVALID"
        super().__init__(code)
        self.code = code
        self.provider_request_id = _safe_request_id(provider_request_id)


@dataclass(frozen=True)
class OpenAIImagesConfig:
    live_calls: bool = False
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 120.0
    max_input_image_bytes: int = 20 * _MIB
    max_input_total_bytes: int = 100 * _MIB
    max_response_bytes: int = 32 * _MIB
    max_output_image_bytes: int = 20 * _MIB


@dataclass(frozen=True)
class ProviderImageResponse:
    image_bytes: bytes = field(repr=False)
    provider_request_id: str | None = None
    usage: ImageProviderUsage | None = None


def _safe_request_id(value: object) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


class OpenAIImagesAdapter:
    def __init__(self, config: OpenAIImagesConfig,
                 transport: httpx.MockTransport | None = None):
        self.config = config
        self._transport = transport

    def _validate_config(self) -> None:
        cfg = self.config
        if not isinstance(cfg, OpenAIImagesConfig) or type(cfg.live_calls) is not bool:
            raise OpenAIImagesError("PROVIDER_CONFIG_INVALID")
        timeout = cfg.timeout_seconds
        try:
            valid_timeout = (type(timeout) in (int, float) and math.isfinite(timeout)
                             and 0 < timeout <= 300)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise OpenAIImagesError("PROVIDER_CONFIG_INVALID")
        limits = (
            (cfg.max_input_image_bytes, 20 * _MIB),
            (cfg.max_input_total_bytes, 100 * _MIB),
            (cfg.max_response_bytes, 32 * _MIB),
            (cfg.max_output_image_bytes, 20 * _MIB),
        )
        if any(type(value) is not int or not 1 <= value <= ceiling
               for value, ceiling in limits):
            raise OpenAIImagesError("PROVIDER_CONFIG_INVALID")
        # Only this explicit test transport bypasses the live-call guard. A
        # custom HTTP transport could ignore proxy/redirect/retry restrictions.
        if self._transport is not None and not isinstance(self._transport, httpx.MockTransport):
            raise OpenAIImagesError("PROVIDER_CONFIG_INVALID")
        if isinstance(self._transport, httpx.MockTransport):
            return
        if not cfg.live_calls:
            raise OpenAIImagesError("PROVIDER_CALLS_DISABLED")
        key = cfg.api_key
        if (not isinstance(key, str) or not 1 <= len(key) <= 512
                or any(not 33 <= ord(character) <= 126 for character in key)):
            raise OpenAIImagesError("PROVIDER_CONFIG_INVALID")

    @staticmethod
    def _snapshot(request: ExternalImageRequest) -> ExternalImageRequest:
        try:
            if (not isinstance(request, ExternalImageRequest)
                    or set(request.__dict__) - set(ExternalImageRequest.model_fields)):
                raise ValueError
            # Revalidate even model_construct/model_copy objects; never trust
            # Pydantic's default "already validated instance" fast path here.
            snapshot = ExternalImageRequest.model_validate(
                request.model_dump(mode="python", warnings=False))
            if snapshot.prompt is not None:
                snapshot.prompt.positive.encode("utf-8")
                snapshot.prompt.negative.encode("utf-8")
            return snapshot
        except (ValidationError, ValueError, TypeError, AttributeError):
            raise OpenAIImagesError("PROVIDER_INPUT_INVALID") from None

    def validate_request(self, request: ExternalImageRequest) -> None:
        """Validate configuration and the request before an attempt is reserved."""
        self._validate_config()
        self._snapshot(request)

    def validate_input(self, request: ExternalImageRequest, product_images: list[bytes],
                       reference_images: list[bytes]) -> None:
        """Also check trusted asset bytes before reserving a paid submission."""
        self.validate_request(request)
        snapshot = self._snapshot(request)
        if (type(product_images) is not list or type(reference_images) is not list
                or len(product_images) != len(snapshot.product_images)
                or len(reference_images) != len(snapshot.reference_images)):
            raise OpenAIImagesError("PROVIDER_INPUT_INVALID")
        images = product_images + reference_images
        if any(type(value) is not bytes or not 0 < len(value) <= self.config.max_input_image_bytes
               for value in images):
            raise OpenAIImagesError("PROVIDER_INPUT_INVALID")
        if sum(map(len, images)) > self.config.max_input_total_bytes:
            raise OpenAIImagesError("PROVIDER_INPUT_INVALID")

    @staticmethod
    def _prompt(request: ExternalImageRequest) -> str:
        if request.pipeline_revision == "2":
            return render_cosmetics_provider_prompt(request)
        if request.operation == "cutout":
            return _CUTOUT_PROMPT
        assert request.prompt is not None  # Enforced by the validated snapshot.
        prompt = request.prompt.positive
        if request.prompt.negative:
            prompt += "\n\nAvoid: " + request.prompt.negative
        products, references = len(request.product_images), len(request.reference_images)
        if products:
            prompt += ("\n\nReference roles: images 1 through " + str(products)
                       + " show the same product from different views. Preserve its identity.")
        if references:
            prompt += ("\nBackground references: images " + str(products + 1)
                       + " through " + str(products + references)
                       + " describe the background and composition, not additional products.")
        return prompt

    def execute(self, request: ExternalImageRequest, product_images: list[bytes],
                reference_images: list[bytes], *, client_request_id: UUID) -> ProviderImageResponse:
        """Submit exactly once; the caller owns durable attempts and asset storage."""
        self.validate_input(request, product_images, reference_images)
        snapshot = self._snapshot(request)
        if not isinstance(client_request_id, UUID):
            raise OpenAIImagesError("PROVIDER_INPUT_INVALID")
        payload = {
            "model": snapshot.model,
            "prompt": self._prompt(snapshot),
            "n": 1,
            "size": f"{snapshot.width}x{snapshot.height}",
            "quality": snapshot.quality,
            "output_format": "png",
        }
        if snapshot.operation == "cutout":
            payload["background"] = "transparent"
        if snapshot.operation == "generate" and not reference_images:
            path, arguments = "images/generations", {"json": payload}
        else:
            references = snapshot.product_images + snapshot.reference_images
            extensions = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
            files = [
                ("image[]", (f"image-{index}.{extensions[ref.mime_type]}", value, ref.mime_type))
                for index, (ref, value) in enumerate(
                    zip(references, product_images + reference_images), start=1)
            ]
            path = "images/edits"
            arguments = {"data": {key: str(value) for key, value in payload.items()}, "files": files}
        offline = isinstance(self._transport, httpx.MockTransport)
        headers = {
            "Authorization": "Bearer " + ("offline-test" if offline else self.config.api_key),
            "X-Client-Request-Id": str(client_request_id),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        request_id = None
        deadline = time.monotonic() + self.config.timeout_seconds
        try:
            transport = self._transport if offline else httpx.HTTPTransport(retries=0, trust_env=False)
            with httpx.Client(base_url=_BASE_URL, transport=transport, trust_env=False,
                              follow_redirects=False, headers=headers,
                              timeout=httpx.Timeout(self.config.timeout_seconds,
                                                    connect=min(5.0, self.config.timeout_seconds))) as client:
                with client.stream("POST", path, **arguments) as response:
                    request_id = _safe_request_id(response.headers.get("x-request-id"))
                    if response.status_code >= 500 or response.status_code in (408, 409):
                        raise OpenAIImagesError("PROVIDER_SUBMISSION_UNKNOWN",
                                                provider_request_id=request_id)
                    raw = self._read_bounded(response, deadline, request_id)
                    if not response.is_success:
                        try:
                            body = self._json_object(raw)
                        except OpenAIImagesError:
                            body = {}
                        raise OpenAIImagesError(self._http_error(response.status_code, body),
                                                provider_request_id=request_id)
                    body = self._json_object(raw)
                    return self._decode(body, request_id)
        except OpenAIImagesError as exc:
            if request_id is not None and exc.provider_request_id is None:
                raise OpenAIImagesError(exc.code, provider_request_id=request_id) from None
            raise
        except (httpx.TransportError, OSError, TimeoutError):
            raise OpenAIImagesError("PROVIDER_SUBMISSION_UNKNOWN",
                                    provider_request_id=request_id) from None
        except Exception:
            # An injected transport or an invalid protocol implementation must
            # not leak a request body or authentication header into job logs.
            raise OpenAIImagesError("PROVIDER_SUBMISSION_UNKNOWN",
                                    provider_request_id=request_id) from None

    def _read_bounded(self, response: httpx.Response, deadline: float,
                      request_id: str | None) -> bytes:
        if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        length = response.headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal() or len(length) > 10:
                raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
            if int(length) > self.config.max_response_bytes:
                raise OpenAIImagesError("PROVIDER_RESPONSE_TOO_LARGE", provider_request_id=request_id)
        body = bytearray()
        # MockTransport may return an already-read response. For real HTTP,
        # iter_raw avoids transparent decompression and bounds streamed bytes.
        chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
        for chunk in chunks:
            if time.monotonic() >= deadline:
                raise OpenAIImagesError("PROVIDER_SUBMISSION_UNKNOWN", provider_request_id=request_id)
            if len(body) + len(chunk) > self.config.max_response_bytes:
                raise OpenAIImagesError("PROVIDER_RESPONSE_TOO_LARGE", provider_request_id=request_id)
            body.extend(chunk)
        if time.monotonic() >= deadline:
            raise OpenAIImagesError("PROVIDER_SUBMISSION_UNKNOWN", provider_request_id=request_id)
        return bytes(body)

    @staticmethod
    def _json_object(raw: bytes) -> dict:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID")
        return value

    @staticmethod
    def _http_error(status: int, body: dict) -> str:
        error = body.get("error")
        error = error if isinstance(error, dict) else {}
        codes = {value for value in (error.get("code"), error.get("type")) if isinstance(value, str)}
        if status == 401:
            return "PROVIDER_AUTH"
        if codes & {"content_policy_violation", "moderation_blocked", "safety_violations"}:
            return "PROVIDER_POLICY_REJECTED"
        if status == 403:
            return "PROVIDER_AUTH"
        if codes & {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
            return "PROVIDER_QUOTA_EXCEEDED"
        if status == 429:
            return "PROVIDER_RATE_LIMIT"
        return "PROVIDER_REQUEST_REJECTED"

    def _decode(self, body: dict, request_id: str | None) -> ProviderImageResponse:
        data = body.get("data")
        if (body.get("error") is not None or not isinstance(data, list)
                or len(data) != 1 or not isinstance(data[0], dict)
                or data[0].get("url") is not None):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        if len(encoded) > 4 * ((self.config.max_output_image_bytes + 2) // 3):
            raise OpenAIImagesError("PROVIDER_RESPONSE_TOO_LARGE", provider_request_id=request_id)
        try:
            image = base64.b64decode(encoded, validate=True)
            if base64.b64encode(image).decode("ascii") != encoded:
                raise ValueError
        except (ValueError, binascii.Error):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id) from None
        if not image or len(image) > self.config.max_output_image_bytes:
            raise OpenAIImagesError("PROVIDER_RESPONSE_TOO_LARGE", provider_request_id=request_id)
        return ProviderImageResponse(image, request_id, self._usage(body.get("usage"), request_id))

    @staticmethod
    def _usage(value: object, request_id: str | None) -> ImageProviderUsage | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        details = value.get("input_tokens_details")
        if details is not None and not isinstance(details, dict):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        details = details or {}
        counts = {name: value.get(name) for name in ("input_tokens", "output_tokens", "total_tokens")}
        counts.update(input_image_tokens=details.get("image_tokens"),
                      input_text_tokens=details.get("text_tokens"))
        if any(count is not None and (type(count) is not int or not 0 <= count <= 2**63 - 1)
               for count in counts.values()):
            raise OpenAIImagesError("PROVIDER_RESPONSE_INVALID", provider_request_id=request_id)
        return ImageProviderUsage(**counts) if any(count is not None for count in counts.values()) else None
