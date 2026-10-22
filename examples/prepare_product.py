"""Run local file/canvas validation on an existing product cutout (no AI call)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from PIL import Image

from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import VisualBrief
from beauty_ai.image_integrity import composite_approved_foreground
from beauty_ai.image_preparation import inspect_composite, prepare_product_canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cutout", type=Path, help="Existing RGBA product PNG; not an unsegmented photo")
    parser.add_argument("visual", type=Path, help="JSON matching VisualBrief")
    parser.add_argument("--project-id", required=True, type=UUID)
    parser.add_argument("--store", type=Path, default=Path("outputs/local-assets"))
    args = parser.parse_args()
    store = LocalAssetStore(args.store)
    # Bound the CLI read too; the storage boundary remains authoritative.
    with args.cutout.open("rb") as file:
        data = file.read(store.max_upload_bytes + 1)
    source = store.put_upload(data, project_id=args.project_id)
    visual = VisualBrief.model_validate_json(args.visual.read_text())
    prepared = prepare_product_canvas(store.read(source, project_id=args.project_id), visual)
    foreground = store.put_image(prepared.product_rgba, project_id=args.project_id)
    mask = store.put_image(prepared.preserve_mask, project_id=args.project_id)
    # A plain background is a local composition check, not generated scene quality.
    preview = composite_approved_foreground(
        Image.new("RGB", prepared.product_rgba.size, "white"), prepared.product_rgba)
    report = inspect_composite(preview, prepared.product_rgba)
    preview_ref = store.put_image(preview, project_id=args.project_id)
    print(json.dumps({
        "source": source.model_dump(mode="json"),
        "candidate_product_rgba": foreground.model_dump(mode="json"),
        "preserve_mask": mask.model_dump(mode="json"),
        "preview": preview_ref.model_dump(mode="json"),
        "needs_approval": prepared.needs_approval,
        "opaque_preserved": report.opaque_preserved,
        "partial_alpha_pixels": report.partial_alpha_pixels,
        "model_inference": False,
    }, indent=2))


if __name__ == "__main__":
    main()
