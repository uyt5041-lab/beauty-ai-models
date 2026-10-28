"""Run with: python -m examples.backend_handoff --output outputs/backend-demo

Always offline: a MockTransport replaces the provider. No keys are loaded.
The output directory must be new. Source and resized PNG are both retained.
"""
import argparse
import base64
from dataclasses import asdict
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw

from beauty_ai.ad_resize import resize_ad_png
from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.backend_adapter import BackendImageAdapter, BackendImageInput
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=False)
    image = Image.new("RGB", (1152, 1440), "#ff788b")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((390, 300, 760, 1210), radius=50, fill="#d6c7e9")
    draw.rectangle((425, 230, 725, 350), fill="#3159e8")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    source = buffer.getvalue()
    calls = []

    def fake_provider(request):
        calls.append(1)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(source).decode()}]},
                              headers={"x-request-id": "offline-demo"})

    assets = LocalAssetStore(output / "assets")
    processor = ExternalImageProcessor(assets,
        OpenAIImagesAdapter(OpenAIImagesConfig(timeout_seconds=300), transport=httpx.MockTransport(fake_provider)),
        ProviderAttemptStore(output / "attempts.sqlite3"), budget_limit_usd="1", reservation_usd="1")
    adapter = BackendImageAdapter(processor, output / "bindings.sqlite3")
    body = BackendImageInput(job_id="demo-job-001", copy_revision=1, copy_hash="a" * 64,
        product_analysis={"analysis_succeeded": True, "error": None,
            "analysis": {"is_cosmetic": True, "product_type": "스킨케어",
                "container_type": "병", "colors": ["코랄"], "label_text": [],
                "mood": ["청량"], "is_transparent": False}},
        image_prompt={"concept": "밝은 화장품 광고", "prompt": "밝은 색감과 원본 제품 재질을 유지한다.", "mood": ["청량"]},
        ad_copy={"headline": "오늘의 루틴", "subline": "나를 위한 시간", "cta": "제품 살펴보기"},
        product_images=[source, source])
    project = uuid4()
    generated = adapter.generate(body, project_id=project)
    replay = BackendImageAdapter(processor, output / "bindings.sqlite3").generate(body, project_id=project)
    assert generated == replay and len(calls) == 1
    final = resize_ad_png(generated.image_bytes)
    (output / "source.png").write_bytes(generated.image_bytes)
    (output / "final.png").write_bytes(final.image_bytes)
    evidence = {"mode": "offline_mock", "paid_calls": 0, "fake_calls": len(calls),
                "replay_equal": generated == replay, "receipt": generated.receipt,
                "resize": {k: v for k, v in asdict(final).items() if k != "image_bytes"}}
    (output / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"paid_calls": 0, "fake_calls": len(calls), "final_size": [final.width, final.height]}))


if __name__ == "__main__":
    main()
