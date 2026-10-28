"""Internal image MVP DTOs; the team's HTTP contract remains server-owned.

Only the Korean service policy is supported. This package does not import
text-generation, review, experiment presets, Langfuse or local GPU models.
"""
from __future__ import annotations
import hashlib
import json
import unicodedata
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _revision_identity(value: str) -> str:
    if not value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Revision must be nonblank and contain no control characters")
    return value  # Opaque identity: never strip, parse or cast server revisions.


CopyRevision = (
    Annotated[str, Field(strict=True, min_length=1, max_length=128), AfterValidator(_revision_identity)]
    | Annotated[int, Field(strict=True, ge=1)]
)


def content_hash(value: BaseModel | dict) -> str:
    """Hash semantic inputs, never request timestamps or temporary download URLs."""
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AssetRef(Contract):
    asset_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]


class ImagePrompt(Contract):
    """Versioned input supplied by the text service, not generated or translated here.

    Character limits bound the wire payload. A real model adapter must also check
    its tokenizer limits; ``language`` is the sender's declaration, not detection.
    """
    positive: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=2000)
    prompt_version: str = Field(min_length=1, max_length=80)
    source_revision: CopyRevision
    language: Literal["ko"] = "ko"

    @field_validator("positive", "prompt_version")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt text and version must not be blank")
        return value  # Preserve all effective input bytes; never silently trim.


ImageProviderModel = Literal[
    "gpt-image-2.5-sunburst-2026-09-08", "gpt-image-2-2026-04-21"]
ImageOperation = Literal["generate", "edit", "cutout"]


class ExternalImageRequest(Contract):
    """One Korean API operation; input order is part of the snapshot hash."""
    kind: Literal["api_image"] = "api_image"
    operation: ImageOperation
    pipeline_profile_id: Literal["openai-images"] = "openai-images"
    pipeline_revision: Literal["2"] = "2"
    model: ImageProviderModel = "gpt-image-2.5-sunburst-2026-09-08"
    product_images: list[AssetRef] = Field(default_factory=list, max_length=5)
    reference_images: list[AssetRef] = Field(default_factory=list, max_length=5)
    prompt: ImagePrompt | None = None
    watermark_policy: Literal["exclude-v1"] = "exclude-v1"
    cosmetics_policy_version: Literal["ko-v1"] = "ko-v1"
    width: int = Field(default=1152, ge=16, le=3840, strict=True)
    height: int = Field(default=1440, ge=16, le=3840, strict=True)
    quality: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation != "cutout" and self.width * 5 != self.height * 4:
            raise ValueError("Generation and editing require a 4:5 canvas")
        if (self.width % 16 or self.height % 16
                or not 655360 <= self.width * self.height <= 3686400
                or self.width > 3 * self.height or self.height > 3 * self.width):
            raise ValueError("Unsupported image canvas")
        identifiers = [ref.asset_id for ref in self.product_images + self.reference_images]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Input assets must be unique")
        if self.operation == "generate":
            if self.product_images or self.prompt is None:
                raise ValueError("Generation requires a prompt and no product images")
        elif self.operation == "edit":
            if not self.product_images or self.prompt is None:
                raise ValueError("Editing requires product images and a prompt")
        elif (len(self.product_images) != 1 or self.reference_images or self.prompt is not None):
            raise ValueError("Cutout requires one product and the fixed wrapper prompt")
        return self


class ImageProviderUsage(Contract):
    """Observed provider token counts only; absent counts are not inferred as zero."""
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_tokens: int | None = Field(default=None, ge=0, strict=True)
    input_image_tokens: int | None = Field(default=None, ge=0, strict=True)
    input_text_tokens: int | None = Field(default=None, ge=0, strict=True)


class ExternalImageResult(Contract):
    """A generated flat/RGBA image, without the legacy layer-preservation claim."""
    kind: Literal["api_image"] = "api_image"
    operation: ImageOperation
    image: AssetRef
    provider: Literal["openai"] = "openai"
    model: ImageProviderModel
    attempt_id: UUID
    provider_request_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")] | None = None
    usage: ImageProviderUsage | None = None
    needs_manual_check: Literal[True] = True
    warnings: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]] = Field(
        default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_image(self):
        if self.image.mime_type != "image/png":
            raise ValueError("External image results must be normalized PNG assets")
        if (self.operation == "cutout"
                and "GENERATIVE_CUTOUT_REQUIRES_REVIEW" not in self.warnings):
            raise ValueError("Generative cutout requires a review warning")
        return self


def image_result_matches(request: ExternalImageRequest, result: ExternalImageResult) -> bool:
    """Reject a result for a different operation/model/canvas."""
    return (isinstance(result, ExternalImageResult) and result.kind == request.kind
            and result.operation == request.operation and result.model == request.model
            and (result.image.width, result.image.height) == (request.width, request.height))
