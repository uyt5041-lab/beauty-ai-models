"""Run only synthetic, in-process OpenAI wrapper fixtures; never call a provider.

Usage: python -m beauty_ai.openai_images_demo --output outputs/openai-wrapper-demo
This example deliberately has no live option and does not read credentials.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
from PIL import Image, ImageDraw, ImageFont

from .contracts import AssetRef, ExternalImageRequest, ImagePrompt
from .inference_runtime import build_app
from .openai_images import OpenAIImagesConfig


_INTERNAL_TOKEN = "offline-demo-only-token-0000000000000001"
_SIZE = (1024, 1024)


def fixture_png(label: str, *, transparent: bool = False) -> bytes:
    """Draw a labeled test fixture; this is not output from an image model."""
    with Image.new("RGBA", _SIZE, (0, 0, 0, 0) if transparent else (255, 236, 246, 255)) as image:
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((350, 260, 674, 830), radius=35, fill=(120, 72, 208, 255))
        draw.rectangle((400, 200, 624, 280), fill=(255, 204, 30, 255))
        draw.rounded_rectangle((378, 410, 646, 650), radius=10, fill=(255, 255, 255, 255))
        font = ImageFont.load_default(size=34)
        draw.text((424, 470), "FIXTURE", fill=(60, 20, 100, 255), font=font)
        draw.rectangle((24, 24, 1000, 145), fill=(255, 255, 255, 255))
        draw.text((45, 38), "MOCK ONLY - NOT AI OUTPUT", fill=(160, 0, 70, 255), font=font)
        draw.text((45, 88), label.upper(), fill=(70, 20, 90, 255), font=font)
        data = BytesIO()
        image.save(data, "PNG")
        return data.getvalue()


def _completed_job(client: TestClient, job_id, *, timeout_seconds: float = 20) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/internal/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] == "succeeded":
            return job
        if job["status"] in {"failed", "interrupted"}:
            raise RuntimeError(f"Mock demo job failed: {job['error_code']}")
        time.sleep(0.02)
    raise RuntimeError("Mock demo job did not finish before its deadline.")


def run_demo(output: str | Path) -> dict:
    """Exercise uploads, all three operations, receipts and downloads in-process."""
    run_dir = Path(output).resolve() / f"run-{uuid4().hex}"
    run_dir.mkdir(parents=True)
    calls = []
    operations = ("generate", "edit", "cutout")
    fixtures = {operation: fixture_png(operation, transparent=operation == "cutout")
                for operation in operations}

    def mock_provider(request: httpx.Request) -> httpx.Response:
        index = len(calls)
        if index >= len(operations):
            raise RuntimeError("Unexpected duplicate mock submission.")
        operation = operations[index]
        expected = "/v1/images/generations" if operation == "generate" else "/v1/images/edits"
        if request.method != "POST" or request.url.host != "api.openai.com" or request.url.path != expected:
            raise RuntimeError("Unexpected mock provider route.")
        calls.append({"operation": operation, "method": request.method, "path": request.url.path})
        return httpx.Response(200, headers={"x-request-id": f"mock-{operation}-{index + 1}"}, json={
            "data": [{"b64_json": base64.b64encode(fixtures[operation]).decode("ascii")}],
            "usage": {"input_tokens": 17, "output_tokens": 23, "total_tokens": 40,
                      "input_tokens_details": {"image_tokens": 8, "text_tokens": 9}},
        })

    # Explicit MockTransport is the adapter's only offline exception. No keys,
    # environmental settings, sockets or alternate live transport are accepted.
    app = build_app(run_dir / "runtime", uuid4(), _INTERNAL_TOKEN,
        openai_config=OpenAIImagesConfig(live_calls=False),
        openai_transport=httpx.MockTransport(mock_provider),
        image_budget_usd="3", image_reservation_usd="1")
    entries = []
    with TestClient(app, headers={"Authorization": f"Bearer {_INTERNAL_TOKEN}"}) as client:
        uploaded = []
        for label in ("product input fixture", "reference input fixture"):
            response = client.post("/internal/v1/assets", content=fixture_png(label))
            response.raise_for_status()
            uploaded.append(AssetRef.model_validate(response.json()))
        for operation in operations:
            payload = ExternalImageRequest(operation=operation, width=_SIZE[0], height=_SIZE[1],
                product_images=[] if operation == "generate" else [uploaded[0]],
                reference_images=[uploaded[1]] if operation == "edit" else [],
                prompt=None if operation == "cutout" else ImagePrompt(
                    positive="Offline fixture only: create a bright cosmetics scene.",
                    negative="unreviewed advertising text", prompt_version="offline-demo-v1", source_revision=1))
            job_id = uuid4()
            submitted = client.put(f"/internal/v1/jobs/{job_id}", json=payload.model_dump(mode="json"))
            submitted.raise_for_status()
            job = _completed_job(client, job_id)
            receipt = client.get(f"/internal/v1/jobs/{job_id}/provider")
            receipt.raise_for_status()
            downloaded = client.get(f"/internal/v1/assets/{job['result']['image']['asset_id']}")
            downloaded.raise_for_status()
            image_path = run_dir / f"mock-{operation}.png"
            image_path.write_bytes(downloaded.content)
            entries.append({"operation": operation, "job": job, "provider_receipt": receipt.json(),
                            "image_path": str(image_path), "synthetic_fixture": True,
                            "usage_is_synthetic": True, "quality_approval": "NOT_ASSESSED"})

    report = {"schema_version": 1, "mode": "MOCK_ONLY", "actual_provider_calls": 0,
              "mock_provider_calls": len(calls), "quality_approval": "NOT_ASSESSED",
              "fixture_notice": "Synthetic labeled fixtures; not model output or a provider quality evaluation.",
              "usage_notice": "All token counts and reservation receipts in this run are mock data.",
              "run_directory": str(run_dir), "report_path": str(run_dir / "report.json"),
              "mock_routes": calls, "operations": entries}
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/openai-wrapper-demo",
                        help="Parent directory for a new mock fixture run")
    args = parser.parse_args(argv)
    report = run_demo(args.output)
    print(json.dumps({key: report[key] for key in (
        "mode", "actual_provider_calls", "mock_provider_calls", "quality_approval", "report_path")},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
