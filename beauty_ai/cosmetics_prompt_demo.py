"""Export cosmetics prompt previews only. No credentials, HTTP or image calls.

Run: python -m beauty_ai.cosmetics_prompt_demo --output outputs/cosmetics-prompts
Request templates contain a dummy AssetRef; replace it with an authorized upload
before submitting through the service's review and job flow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from .contracts import AssetRef, content_hash
from .cosmetics_prompts import (PRESETS, CosmeticsPromptBrief,
    build_cosmetics_request, build_cosmetics_cutout_request,
    render_cosmetics_provider_prompt)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_demo(output: str | Path, brief: CosmeticsPromptBrief | None = None) -> dict:
    """Write an isolated preview run; returned hashes cover template inputs only."""
    base = brief if brief is not None else CosmeticsPromptBrief(
        product_description="An illustrative lavender serum bottle with a coral "
                            "pump and a blank cream label; no efficacy claims.",
        source_revision=1)
    # Validate before creating files, including copies made without validation.
    base = CosmeticsPromptBrief.model_validate(base.model_dump(warnings=False))
    dummy_product = AssetRef(asset_id=UUID("00000000-0000-0000-0000-000000000001"),
        sha256="0" * 64, width=1152, height=1440, mime_type="image/png")
    previews = []
    for preset in PRESETS:
        variant = CosmeticsPromptBrief.model_validate({**base.model_dump(), "preset_id": preset.id})
        request = build_cosmetics_request(variant, product_images=[dummy_product])
        previews.append((preset, variant, request))
    cutout = build_cosmetics_cutout_request(dummy_product)
    run_dir = Path(output).resolve() / f"run-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    entries = []
    for preset, variant, request in previews:
        folder = run_dir / preset.id
        folder.mkdir()
        _write_json(folder / "brief.json", variant.model_dump(mode="json"))
        _write_json(folder / "image-prompt.json", request.prompt.model_dump(mode="json"))
        _write_json(folder / "request-template.json", request.model_dump(mode="json"))
        (folder / "provider-prompt.txt").write_text(
            render_cosmetics_provider_prompt(request) + "\n", encoding="utf-8")
        entries.append({"preset_id": preset.id, "title": preset.title,
            "prompt_version": request.prompt.prompt_version,
            "template_request_hash": content_hash(request),
            "directory": preset.id, "human_review": "PENDING",
            "quality_approval": "NOT_ASSESSED"})
    _write_json(run_dir / "cutout-request-template.json", cutout.model_dump(mode="json"))
    (run_dir / "cutout-provider-prompt.txt").write_text(
        render_cosmetics_provider_prompt(cutout) + "\n", encoding="utf-8")
    report = {"mode": "PROMPT_PREVIEW_ONLY", "actual_provider_calls": 0,
        "generated_images": 0, "requires_uploaded_assets": True,
        "asset_notice": "Dummy AssetRef only. Replace with authorized uploaded product assets; "
                        "request hashes will change. No image has been generated or reviewed.",
        "pipeline_revision": "2", "quality_approval": "NOT_ASSESSED",
        "evaluation_criteria": ["Product shape, color and small parts",
            "Original label, logo and packaging artwork",
            "Scene follows the selected preset and explicit creative direction",
            "Reserved copy area and 4:5 composition",
            "Cutout edges and real alpha transparency"],
        "presets": entries, "report_path": str(run_dir / "report.json")}
    _write_json(run_dir / "report.json", report)
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/cosmetics-prompts")
    parser.add_argument("--brief", type=Path,
                        help="Optional CosmeticsPromptBrief JSON; all three presets are previewed")
    args = parser.parse_args(argv)
    try:
        brief = (CosmeticsPromptBrief.model_validate_json(args.brief.read_text(encoding="utf-8"))
                 if args.brief else None)
        report = run_demo(args.output, brief)
    except (OSError, ValueError, ValidationError):
        parser.error("Could not read/validate the cosmetics brief or write preview files")
    print(json.dumps({key: report[key] for key in (
        "mode", "actual_provider_calls", "generated_images", "quality_approval", "report_path")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
