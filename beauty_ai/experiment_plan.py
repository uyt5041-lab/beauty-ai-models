"""Resolve selected cosmetics prompts locally, before any paid submission."""
from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from .contracts import AssetRef, content_hash
from .cosmetics_prompts import (PRESETS, PROMPT_VERSION, CosmeticsPromptBrief,
                               build_cosmetics_request, render_cosmetics_provider_prompt)

DEFAULT_MODEL = "gpt-image-2.5-sunburst-2026-09-08"
REQUIRED_VARIABLES = {"product_description", "palette_section", "creative_direction_section",
                      "composition", "reference_instructions"}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_refs(records: list[dict]) -> tuple[list[AssetRef], list[AssetRef]]:
    """Validate ordered records; file integrity is checked again before execution."""
    try:
        if type(records) is not list:
            raise ValueError
        products, references, seen = [], [], set()
        reference_started = False
        for record in records:
            if type(record) is not dict or record.get("role") not in {"product", "reference"}:
                raise ValueError
            role = record["role"]
            if role == "reference":
                reference_started = True
            elif reference_started:
                raise ValueError
            target = products if role == "product" else references
            if type(record.get("index")) is not int or record["index"] != len(target) + 1:
                raise ValueError
            ref = AssetRef.model_validate(record["asset_ref"])
            if ref.mime_type != "image/png" or record["normalized_sha256"] != ref.sha256:
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"]):
                raise ValueError
            if ref.sha256 in seen or str(ref.asset_id) in seen:
                raise ValueError
            seen.update((ref.sha256, str(ref.asset_id)))
            target.append(ref)
        if not 2 <= len(products) <= 5 or len(references) > 5:
            raise ValueError
        return products, references
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("EXPERIMENT_INPUT_RECORDS_INVALID") from None


def build_cases(records: list[dict], *, product_description: str, presets: list[str],
                prompt_version: str, prompt_snapshots: dict[str, dict] | None = None,
                repeats: int = 1, quality: str = "medium", layout_id: str = "product_right",
                model: str = DEFAULT_MODEL) -> list[dict]:
    """Freeze full requests and provider text, using local or exact remote snapshots.

    No label lookup, environment loading, remote publishing or SDK call occurs.
    Remote snapshots are supplied explicitly by the caller and are not treated
    as proof of remote provenance. Numeric versions are checked exactly.
    """
    products, references = input_refs(records)
    catalogue = {preset.id: preset for preset in PRESETS}
    if (type(presets) is not list or not 1 <= len(presets) <= 3
            or any(type(preset) is not str or preset not in catalogue for preset in presets)
            or len(set(presets)) != len(presets)):
        raise ValueError("EXPERIMENT_PRESET_SELECTION_INVALID")
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("EXPERIMENT_REPEATS_INVALID")
    if type(prompt_version) is not str:
        raise ValueError("EXPERIMENT_PROMPT_VERSION_INVALID")
    local = prompt_snapshots is None
    if local:
        if prompt_version != PROMPT_VERSION:
            raise ValueError("EXPERIMENT_PROMPT_VERSION_INVALID")
    elif (not re.fullmatch(r"[1-9][0-9]{0,8}", prompt_version)
          or type(prompt_snapshots) is not dict or set(prompt_snapshots) != set(presets)):
        raise ValueError("EXPERIMENT_PROMPT_SNAPSHOTS_INVALID")

    cases = []
    try:
        for preset_id in presets:
            brief = CosmeticsPromptBrief(product_description=product_description, preset_id=preset_id,
                                          layout_id=layout_id, source_revision=1)
            request = build_cosmetics_request(brief, product_images=products, reference_images=references,
                                              quality=quality, model=model)
            if local:
                from .langfuse_prompts import cosmetics_prompt_definition
                provider_text = render_cosmetics_provider_prompt(request)
                template_sha = _digest(cosmetics_prompt_definition(preset_id)["prompt"])
            else:
                from .langfuse_prompts import cosmetics_prompt_variables, pin_cosmetics_prompt
                remote = prompt_snapshots[preset_id]
                if (type(remote) is not dict or type(remote.get("version")) is not int
                        or remote["version"] != int(prompt_version)
                        or type(remote.get("prompt")) is not str):
                    raise ValueError
                variables = set(re.findall(r"\{\{\s*([a-z_]+)\s*\}\}", remote["prompt"]))
                if variables != REQUIRED_VARIABLES:
                    raise ValueError
                request = pin_cosmetics_prompt(request, remote, cosmetics_prompt_variables(brief, request))
                provider_text = request.managed_prompt.rendered_text
                template_sha = _digest(remote["prompt"])
            for repeat_index in range(1, repeats + 1):
                cases.append({"case_id": f"case-{len(cases) + 1:03d}", "job_id": str(uuid4()),
                    "preset_id": preset_id, "title": catalogue[preset_id].title,
                    "repeat_index": repeat_index, "request": request.model_dump(mode="json"),
                    "request_sha256": content_hash(request), "provider_prompt": provider_text,
                    "provider_prompt_sha256": _digest(provider_text),
                    "prompt_source": "local" if local else "langfuse", "prompt_version": prompt_version,
                    "template_sha256": template_sha})
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ValueError("EXPERIMENT_PROMPT_RESOLUTION_INVALID") from None
    return cases
