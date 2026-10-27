"""Versioned cosmetics prompts and explicit, optional Images API observations.

Prompt creation/fetching is deliberately outside the image worker. A caller
pins a fetched version before hashing/queueing; a worker never fetches latest.
Importing this module never imports the SDK or performs network operations.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from .contracts import (AssetRef, ExternalImageRequest, ImageProviderUsage,
                        ManagedPromptSnapshot, content_hash)
from .prompt_policy import WATERMARK_POLICY_ID, apply_watermark_policy
from .cosmetics_prompts import (CosmeticsPromptBrief, PRESETS, _LAYOUTS, _snapshot,
                               build_cosmetics_prompt, build_cosmetics_request,
                               render_cosmetics_provider_prompt)

_VARIABLES = frozenset({"product_description", "palette_section",
                        "creative_direction_section", "composition",
                        "reference_instructions"})
_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def _name(preset_id: str) -> str:
    if preset_id not in {preset.id for preset in PRESETS}:
        raise ValueError("Unknown cosmetics preset")
    return "beautyai/cosmetics/" + preset_id.replace("_", "-")


def _palette(brief: CosmeticsPromptBrief) -> str:
    if not brief.brand_colors:
        return ""
    return ("\n\nBACKGROUND PALETTE\nUse " + ", ".join(brief.brand_colors)
            + " instead of the preset's default background colors. Apply these "
            "only to the background and scene accents; never recolor the product.")


def _direction(brief: CosmeticsPromptBrief) -> str:
    if not brief.creative_direction:
        return ""
    return ("\n\nTEAM CREATIVE DIRECTION\n" + brief.creative_direction
            + "\nApply this direction only where compatible with product "
            "preservation and the empty copy space.")


def _references(request: ExternalImageRequest) -> str:
    text = render_cosmetics_provider_prompt(request)
    marker = "\n\nPRODUCT REFERENCES\n" if request.product_images else "\n\nCONCEPT ONLY\n"
    return text[text.rindex(marker):]


def cosmetics_prompt_definition(preset_id: str) -> dict:
    """Return a CLI/API creation payload; this does not publish anything."""
    name = _name(preset_id)
    brief = CosmeticsPromptBrief(product_description="BEAUTY_DESCRIPTION_SENTINEL",
        preset_id=preset_id, source_revision=1, brand_colors=["#123456"],
        creative_direction="BEAUTY_DIRECTION_SENTINEL")
    asset = AssetRef(asset_id=UUID(int=1), sha256="0" * 64,
                     mime_type="image/png", width=1152, height=1440)
    request = build_cosmetics_request(brief, product_images=[asset])
    template = render_cosmetics_provider_prompt(request)
    template = template.replace(brief.product_description, "{{product_description}}", 1)
    template = template.replace(_palette(brief), "{{palette_section}}", 1)
    template = template.replace(_direction(brief), "{{creative_direction_section}}", 1)
    template = template.replace(_LAYOUTS[brief.layout_id], "{{composition}}", 1)
    references = _references(request)
    template = template[:-len(references)] + "{{reference_instructions}}"
    return {"name": name, "type": "text", "prompt": template,
            "labels": ["production"], "tags": ["beautyai", "cosmetics", "image-edit"],
            "config": {"pipeline_revision": "2", "source_prompt_version": "cosmetics-v1",
                       "watermark_policy": WATERMARK_POLICY_ID,
                       "variables": sorted(_VARIABLES)},
            "commitMessage": "Add exclude-v1 watermark policy; preserve product logo and label"}


def cosmetics_prompt_variables(brief: CosmeticsPromptBrief,
                               request: ExternalImageRequest) -> dict[str, str]:
    """Resolve optional sections locally; Langfuse templates need no conditionals."""
    try:
        brief = _snapshot(brief)
        request = ExternalImageRequest.model_validate(request.model_dump())
        if (request.pipeline_revision != "2" or request.operation == "cutout"
                or request.prompt != build_cosmetics_prompt(brief)):
            raise ValueError
        return {"product_description": brief.product_description,
                "palette_section": _palette(brief),
                "creative_direction_section": _direction(brief),
                "composition": _LAYOUTS[brief.layout_id],
                "reference_instructions": _references(request)}
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid cosmetics prompt variables") from None


def pin_cosmetics_prompt(request: ExternalImageRequest, remote_prompt: Mapping,
                         variables: Mapping[str, str]) -> ExternalImageRequest:
    """Freeze remote text/version into the request; substitute variables once.

    Supplied values may contain braces: they remain literal user data, never a
    second template pass. Remote edits are allowed only through this explicit
    pre-queue pinning step and therefore change the request hash.
    """
    try:
        snapshot = ExternalImageRequest.model_validate(request.model_dump())
        if snapshot.pipeline_revision != "2" or snapshot.operation == "cutout":
            raise ValueError
        if not isinstance(remote_prompt, Mapping) or not isinstance(variables, Mapping):
            raise ValueError
        if set(variables) != _VARIABLES or any(type(v) is not str for v in variables.values()):
            raise ValueError
        if snapshot.prompt is None:
            raise ValueError
        parts = snapshot.prompt.prompt_version.split("/")
        if len(parts) != 3 or parts[0] != "cosmetics-v1":
            raise ValueError
        if remote_prompt.get("type") != "text" or remote_prompt.get("name") != _name(parts[1]):
            raise ValueError
        template = remote_prompt.get("prompt")
        if type(template) is not str or not 1 <= len(template) <= 16000:
            raise ValueError
        # Reject malformed braces in the template; braces in values are data.
        remainder = _PLACEHOLDER.sub("", template)
        if "{{" in remainder or "}}" in remainder:
            raise ValueError
        if any(match.group(1) not in _VARIABLES for match in _PLACEHOLDER.finditer(template)):
            raise ValueError
        rendered = _PLACEHOLDER.sub(lambda match: variables[match.group(1)], template)
        rendered = apply_watermark_policy(rendered, snapshot.watermark_policy)
        pinned = ManagedPromptSnapshot(name=remote_prompt["name"],
            version=remote_prompt["version"], rendered_text=rendered,
            sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest())
        body = snapshot.model_dump()
        body["managed_prompt"] = pinned.model_dump()
        return ExternalImageRequest.model_validate(body)
    except (ValueError, TypeError, AttributeError, KeyError, UnicodeError):
        raise ValueError("Invalid managed cosmetics prompt") from None


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError("Invalid observation identifier")
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("Invalid observation digest")
    return value


@dataclass
class ImageGenerationTrace:
    """Best-effort telemetry only; never runs, retries or approves an image job."""
    client: Any = field(default=None, repr=False)
    observation: Any = field(default=None, repr=False)
    metadata: dict = field(default_factory=dict, repr=False)
    status: str = "NOT_CONFIGURED"
    _receipt: dict | None = field(default=None, repr=False)

    def finish(self, *, output_sha256: str | None = None,
               usage: ImageProviderUsage | None = None,
               provider_request_id: str | None = None,
               estimated_cost_usd: float | None = None,
               error_code: str | None = None) -> dict:
        if self._receipt is not None:
            return dict(self._receipt)
        if self.observation is None:
            self._receipt = {"status": self.status, "readback_verified": False}
            return dict(self._receipt)
        try:
            output = {"image_sha256": _digest(output_sha256) if output_sha256 else None,
                      "human_review": "PENDING", "quality_approved": False}
            metadata = dict(self.metadata)
            metadata["provider_request_id"] = (_identifier(provider_request_id)
                                                if provider_request_id else None)
            if estimated_cost_usd is not None and (type(estimated_cost_usd) not in (int, float)
                    or not math.isfinite(estimated_cost_usd) or estimated_cost_usd < 0):
                raise ValueError
            metadata.update(estimated_cost_usd=estimated_cost_usd,
                            actual_cost_usd=None, billing_status="UNRECONCILED")
            usage_details = {}
            if usage is not None:
                usage = ImageProviderUsage.model_validate(usage.model_dump())
                # Modalities are disjoint: do not send input aggregate as well.
                if usage.input_text_tokens is not None:
                    usage_details["input_text"] = usage.input_text_tokens
                if usage.input_image_tokens is not None:
                    usage_details["input_image"] = usage.input_image_tokens
                if not usage_details and usage.input_tokens is not None:
                    usage_details["input"] = usage.input_tokens
                if usage.output_tokens is not None:
                    usage_details["output_image"] = usage.output_tokens
                metadata["provider_usage"] = usage.model_dump(exclude_none=True)
            if error_code is not None and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", error_code) is None:
                raise ValueError
            self.observation.update(output=output, metadata=metadata,
                usage_details=usage_details or None,
                level="ERROR" if error_code else "DEFAULT", status_message=error_code)
            self.observation.end()
            self.client.flush()
            self._receipt = {"status": "FLUSH_REQUESTED", "readback_verified": False,
                "trace_id": self.observation.trace_id, "observation_id": self.observation.id}
        except Exception:
            # Never expose exception bodies/headers, or disturb the paid job.
            self._receipt = {"status": "OBSERVABILITY_FAILED", "readback_verified": False}
        return dict(self._receipt)


def begin_image_generation(request: ExternalImageRequest, *, run_id: str,
                           job_id: str, request_hash: str,
                           client: Any | None = None) -> ImageGenerationTrace:
    """Start one explicit SDK generation before the bounded Images API call.

    The optional SDK imports only on this explicitly called path. Cloud keys
    are read by its own client; source bytes, local paths, keys and HTTP bodies
    are never observation fields. Successful flush is not delivery evidence.
    """
    try:
        snapshot = ExternalImageRequest.model_validate(request.model_dump())
        managed = snapshot.managed_prompt
        if managed is None or request_hash != content_hash(snapshot):
            raise ValueError
        metadata = {"run_id": _identifier(run_id), "job_id": _identifier(job_id),
                    "request_hash": _digest(request_hash),
                    "rendered_prompt_sha256": managed.sha256,
                    "source_asset_sha256": [a.sha256 for a in snapshot.product_images],
                    "reference_asset_sha256": [a.sha256 for a in snapshot.reference_images],
                    "pipeline_revision": snapshot.pipeline_revision,
                    "billing_status": "UNRECONCILED", "actual_cost_usd": None,
                    "human_review": "PENDING"}
        if client is None and not all(os.environ.get(name) for name in
                ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")):
            return ImageGenerationTrace()
        from langfuse import Langfuse, propagate_attributes
        from langfuse.api.prompts.types.prompt import Prompt_Text
        from langfuse.model import TextPromptClient
        if client is None:
            client = Langfuse(environment="beautyai-preset-comparison", timeout=15)
        prompt = TextPromptClient(Prompt_Text(type="text", name=managed.name,
            version=managed.version, prompt=managed.rendered_text,
            config={}, labels=[], tags=["beautyai"]))
        with propagate_attributes(session_id=run_id, tags=["beautyai", "cosmetics"],
                                  trace_name="create-cosmetics-ad"):
            observation = client.start_observation(name="edit-cosmetics-image",
                as_type="generation", model=snapshot.model, prompt=prompt,
                input={"prompt": managed.rendered_text,
                       "product_image_sha256": metadata["source_asset_sha256"],
                       "reference_image_sha256": metadata["reference_asset_sha256"]},
                metadata=metadata,
                model_parameters={"operation": snapshot.operation, "quality": snapshot.quality,
                                  "size": f"{snapshot.width}x{snapshot.height}", "n": 1})
        return ImageGenerationTrace(client=client, observation=observation,
                                    metadata=metadata, status="STARTED")
    except Exception:
        return ImageGenerationTrace(status="OBSERVABILITY_FAILED")
