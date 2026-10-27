"""Verify effective prompts and immutable historical request snapshots offline."""
from uuid import UUID
import hashlib

import pytest
from pydantic import ValidationError

from beauty_ai.contracts import AssetRef, ExternalImageRequest, content_hash
from beauty_ai.cosmetics_prompts import (CosmeticsPromptBrief, build_cosmetics_request,
    build_cosmetics_cutout_request, render_cosmetics_provider_prompt)
from beauty_ai.langfuse_prompts import (cosmetics_prompt_definition,
    cosmetics_prompt_variables, pin_cosmetics_prompt)
from beauty_ai.openai_images import OpenAIImagesAdapter
from beauty_ai.prompt_policy import WATERMARK_POLICY_TEXT


def inputs(preset):
    brief = CosmeticsPromptBrief(product_description="Original cosmetic tube", source_revision=1,
                                 preset_id=preset)
    asset = AssetRef(asset_id=UUID(int=1), sha256="0" * 64, width=64, height=64, mime_type="image/png")
    return brief, asset


@pytest.mark.parametrize("preset", ["clean_studio", "premium_minimal", "vivid_pop"])
def test_new_local_and_remote_requests_pin_policy_without_erasing_product_label(preset):
    brief, asset = inputs(preset)
    request = build_cosmetics_request(brief, product_images=[asset])
    effective = OpenAIImagesAdapter._prompt(request)
    assert WATERMARK_POLICY_TEXT in effective
    assert "printed brand logo, label text" in effective
    remote = dict(cosmetics_prompt_definition(preset), version=42)
    # A caller can select a historical Langfuse template for a NEW request.
    remote["prompt"] = remote["prompt"].replace("\n\n" + WATERMARK_POLICY_TEXT, "")
    pinned = pin_cosmetics_prompt(request, remote, cosmetics_prompt_variables(brief, request))
    assert WATERMARK_POLICY_TEXT in pinned.managed_prompt.rendered_text
    assert OpenAIImagesAdapter._prompt(pinned) == pinned.managed_prompt.rendered_text
    assert pinned.managed_prompt.sha256 == hashlib.sha256(pinned.managed_prompt.rendered_text.encode()).hexdigest()
    tampered = pinned.model_dump(mode="json")
    tampered["managed_prompt"]["rendered_text"] = "missing policy"
    tampered["managed_prompt"]["sha256"] = hashlib.sha256(b"missing policy").hexdigest()
    with pytest.raises(ValidationError):
        ExternalImageRequest.model_validate(tampered)


def test_cutout_and_concept_defaults_include_policy():
    brief, asset = inputs("clean_studio")
    for request in (build_cosmetics_cutout_request(asset),
                    build_cosmetics_request(brief, product_images=[], operation="generate")):
        assert WATERMARK_POLICY_TEXT in OpenAIImagesAdapter._prompt(request)


def test_old_request_hash_and_prompt_are_not_silently_migrated():
    brief, asset = inputs("vivid_pop")
    body = build_cosmetics_request(brief, product_images=[asset]).model_dump(mode="json")
    body.pop("watermark_policy")
    legacy = ExternalImageRequest.model_validate(body)
    assert legacy.model_dump(mode="json") == body
    assert content_hash(legacy) == content_hash(body)
    assert WATERMARK_POLICY_TEXT not in render_cosmetics_provider_prompt(legacy)
    assert content_hash(legacy) != content_hash(build_cosmetics_request(brief, product_images=[asset]))
