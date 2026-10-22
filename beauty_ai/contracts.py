from __future__ import annotations

import hashlib
import json
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler,
                      field_validator, model_serializer, model_validator)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def content_hash(value: BaseModel | dict) -> str:
    """Hash semantic inputs, never request timestamps or temporary download URLs."""
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ClaimFact(Contract):
    claim_id: str
    text: str
    evidence_ref: str  # Reference supplied/checked outside the model; not proof by itself.


class ProductFacts(Contract):
    product_id: UUID
    revision: int = Field(ge=1)
    product_name: str
    functional_status: Literal["general", "functional", "unconfirmed"]
    functional_categories: list[str]
    confirmed_claims: list[ClaimFact]


class ProductObservation(Contract):
    """VLM observations are suggestions, not functional classification or evidence."""
    container_shape: str | None
    visible_colors: list[str]
    label_text: str | None
    uncertainties: list[str]


class AdOptions(Contract):
    purpose: str
    target: str
    tone: str
    channel: str
    market: str
    style_preset_id: str
    layout_template_id: str


class CopyBundle(Contract):
    headline: str
    subheadline: str
    cta: str
    caption: str
    hashtags: list[str]
    badges: list[str]

    def text_fields(self) -> dict[str, str]:
        return {
            k: " ".join(v) if isinstance(v, list) else v
            for k, v in self.model_dump().items()
        }


class CopyVariant(Contract):
    concept: str
    key_message: str
    copy_bundle: CopyBundle
    used_claim_ids: list[str]


class CampaignDraft(Contract):
    variants: list[CopyVariant]


class ReviewIssue(Contract):
    field: Literal["headline", "subheadline", "cta", "caption", "hashtags", "badges"]
    quote: str
    severity: Literal["warning", "block"]
    reason: str
    suggested_text: str | None
    rule_ids: list[str]


class LLMReview(Contract):
    issues: list[ReviewIssue]
    uncertainties: list[str]


class Rule(Contract):
    rule_id: str
    pattern: str  # Trusted, curated regex only. Never accept patterns from end users.
    severity: Literal["warning", "block"]
    reason: str
    source_ref: str  # Official document/version/section reference, curated by the team.


class RulePack(Contract):
    version: str
    status: Literal["draft", "reviewed"]
    reviewed_at: str | None
    rules: list[Rule]

    @model_validator(mode="after")
    def unique_rule_ids(self):
        ids = [r.rule_id for r in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("Rule IDs must be unique")
        if self.status == "reviewed" and (not self.rules or not self.reviewed_at):
            raise ValueError("Reviewed packs require rules and a review timestamp")
        return self


class CallMeta(Contract):
    operation: str
    provider: str
    model: str
    provider_request_id: str | None
    elapsed_ms: int
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str


T = TypeVar("T", bound=BaseModel)


class ModelResult(Contract, Generic[T]):
    data: T
    meta: CallMeta


class ReviewReport(Contract):
    status: Literal["no_flags", "warnings", "blocked", "unavailable"]
    snapshot_hash: str
    copy_revision: int
    product_revision: int
    rule_pack_version: str
    prompt_version: str
    configured_review_model: str
    issues: list[ReviewIssue]
    uncertainties: list[str]
    meta: CallMeta | None
    legal_approval: Literal[False] = False


class AssetRef(Contract):
    asset_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]


class Rect(Contract):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_canvas(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Normalized rectangle exceeds canvas")
        return self


class VisualBrief(Contract):
    """Frozen visual intent; deliberately excludes mutable ad-copy text."""
    concept: str
    style_preset_id: str
    style_preset_version: str
    layout_template_id: str
    layout_template_version: str
    generation_width: int = Field(ge=256, le=2048)
    generation_height: int = Field(ge=256, le=2048)
    product_box: Rect
    text_safe_zone: Rect

    @model_validator(mode="after")
    def validate_geometry(self):
        if self.generation_width % 8 or self.generation_height % 8:
            raise ValueError("Working dimensions must be multiples of 8")
        a, b = self.product_box, self.text_safe_zone
        if (max(a.x, b.x) < min(a.x + a.width, b.x + b.width)
                and max(a.y, b.y) < min(a.y + a.height, b.y + b.height)):
            raise ValueError("MVP product area must not overlap the text safe zone")
        return self


class CutoutRequest(Contract):
    kind: Literal["cutout"] = "cutout"
    source: AssetRef
    pipeline_profile_id: str
    pipeline_revision: str


class ImagePrompt(Contract):
    """Versioned input supplied by the text service, not generated or translated here.

    Character limits bound the wire payload. A real model adapter must also check
    its tokenizer limits; ``language`` is the sender's declaration, not detection.
    """
    positive: str = Field(min_length=1, max_length=4000)
    negative: str = Field(default="", max_length=2000)
    prompt_version: str = Field(min_length=1, max_length=80)
    source_revision: int = Field(ge=1, strict=True)
    language: Literal["en"] = "en"

    @field_validator("positive", "prompt_version")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt text and version must not be blank")
        return value  # Preserve all effective input bytes; never silently trim.


class BackgroundRequest(Contract):
    kind: Literal["render_background"] = "render_background"
    source: AssetRef
    product_rgba: AssetRef  # Approved working-canvas foreground, not arbitrary upload.
    preserve_mask: AssetRef  # White means preserve in OUR contract; adapter inverts it.
    visual: VisualBrief
    seed: int = Field(ge=0, le=2**32 - 1)
    pipeline_profile_id: str  # Server-owned profile: checkpoint/adapters/scheduler/steps.
    pipeline_revision: str
    image_prompt: ImagePrompt | None = None

    @model_serializer(mode="wrap")
    def serialize_legacy_compatible(self, handler: SerializerFunctionWrapHandler):
        # Old queued snapshots have no prompt field. Do not add null to their
        # canonical body/hash, while retaining every field of an actual prompt.
        serialized = handler(self)
        if self.image_prompt is None:
            serialized.pop("image_prompt", None)
        return serialized

    @model_validator(mode="after")
    def matching_working_canvas(self):
        canvas = (self.visual.generation_width, self.visual.generation_height)
        if any((a.width, a.height) != canvas for a in (self.product_rgba, self.preserve_mask)):
            raise ValueError("Foreground, mask, and visual brief must share a canvas")
        if self.product_rgba.mime_type != "image/png":
            raise ValueError("Foreground must be an alpha-capable PNG")
        return self


ImageRequest = CutoutRequest | BackgroundRequest


class CutoutResult(Contract):
    kind: Literal["cutout"] = "cutout"
    product_rgba: AssetRef
    preserve_mask: AssetRef
    needs_manual_check: bool
    warnings: list[str]


class BackgroundResult(Contract):
    kind: Literal["render_background"] = "render_background"
    scene_underlay: AssetRef  # NOT a guaranteed product-free background/clean plate.
    product_rgba: AssetRef
    composed_preview: AssetRef  # Original approved foreground composited back in.
    warnings: list[str]


ImageResult = Annotated[CutoutResult | BackgroundResult, Field(discriminator="kind")]


class ImageJob(Contract):
    job_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    result: ImageResult | None
    error_code: str | None

    @model_validator(mode="after")
    def coherent_job(self):
        if self.status == "succeeded" and (self.result is None or self.error_code):
            raise ValueError("Successful jobs require a result and no error")
        if self.status != "succeeded" and self.result is not None:
            raise ValueError("Non-successful jobs must not publish a success result")
        if self.status in ("failed", "interrupted") and not self.error_code:
            raise ValueError("Terminal failures require an error code")
        return self
