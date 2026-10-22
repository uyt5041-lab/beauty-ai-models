from __future__ import annotations

from io import BytesIO
import asyncio
import time
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import httpx
import pytest

from beauty_ai.contracts import BackgroundRequest, CutoutRequest, AssetRef, VisualBrief
from beauty_ai.inference_runtime import build_app
from beauty_ai.image_gateway import HTTPImageGateway
from beauty_ai.errors import GatewayError


TOKEN = "test-only-runtime-token-0000000000000001"
AUTH = {"Authorization": "Bearer " + TOKEN}


def visual():
    return VisualBrief(concept="Bright flower advertisement", style_preset_id="bright-ad",
                       style_preset_version="1", layout_template_id="product-right",
                       layout_template_version="1", generation_width=256, generation_height=256,
                       product_box={"x": .6, "y": .2, "width": .3, "height": .7},
                       text_safe_zone={"x": .05, "y": .1, "width": .45, "height": .7})


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/internal/v1/jobs/{job_id}", headers=AUTH).json()
        if job["status"] in {"succeeded", "failed", "interrupted"}:
            return job
        time.sleep(.01)
    raise AssertionError("Local worker did not complete")


def test_runtime_requires_explicit_approval_and_saves_real_layers(tmp_path):
    project = uuid4()
    with TestClient(build_app(tmp_path, project, TOKEN)) as client:
        image = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
        image.paste((20, 160, 200, 255), (8, 4, 24, 44))
        buffer = BytesIO()
        image.save(buffer, "PNG")
        upload = client.post("/internal/v1/assets", content=buffer.getvalue(), headers=AUTH)
        source = AssetRef.model_validate(upload.json())
        cutout = CutoutRequest(source=source, pipeline_profile_id="uploaded-alpha", pipeline_revision="1")
        job_id = uuid4()
        assert client.put(f"/internal/v1/jobs/{job_id}", json=cutout.model_dump(mode="json"), headers=AUTH).status_code == 202
        cutout_result = wait_job(client, job_id)
        assert cutout_result["status"] == "succeeded"
        prepared = client.post("/internal/v1/products/prepare", headers=AUTH, json={
            "source": source.model_dump(mode="json"),
            "cutout": cutout_result["result"]["product_rgba"], "visual": visual().model_dump(mode="json"),
        })
        assert prepared.status_code == 201, prepared.text
        candidate = prepared.json()
        assert candidate["approved"] is False
        background = BackgroundRequest(source=source, product_rgba=candidate["product_rgba"],
            preserve_mask=candidate["preserve_mask"], visual=visual(), seed=42,
            pipeline_profile_id="bright-template", pipeline_revision="1")
        render_id = uuid4()
        url = f"/internal/v1/jobs/{render_id}"
        assert client.put(url, headers=AUTH, json=background.model_dump(mode="json")).status_code == 409
        approve_url = f"/internal/v1/products/{candidate['candidate_id']}/approval"
        assert client.put(approve_url, headers=AUTH, json={"candidate_hash": "0" * 64, "confirmed": True}).status_code == 409
        assert client.put(approve_url, headers=AUTH, json={"candidate_hash": candidate["candidate_hash"], "confirmed": "true"}).status_code == 422
        assert client.put(approve_url, headers=AUTH, json={"candidate_hash": candidate["candidate_hash"], "confirmed": True}).status_code == 200
        assert client.put(url, headers=AUTH, json=background.model_dump(mode="json")).status_code == 202
        result = wait_job(client, render_id)
        assert result["status"] == "succeeded", result
        assert "TEMPLATE_NOT_AI_GENERATION" in result["result"]["warnings"]
        for name in ("scene_underlay", "product_rgba", "composed_preview"):
            asset = result["result"][name]
            assert client.get(f"/internal/v1/assets/{asset['asset_id']}", headers=AUTH).status_code == 200
    # A restart retains the real candidate approval and terminal job record.
    with TestClient(build_app(tmp_path, project, TOKEN)) as restarted:
        assert restarted.get(url, headers=AUTH).json() == result
        assert restarted.get(f"/internal/v1/products/{candidate['candidate_id']}", headers=AUTH).json()["approved"]


def test_gateway_distinguishes_approval_conflict_from_job_id_conflict():
    async def check(code):
        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(
                lambda _: httpx.Response(409, json={"detail": {"code": code}}))) as client:
            request = CutoutRequest(source=AssetRef(asset_id=uuid4(), sha256="a" * 64,
                width=32, height=48, mime_type="image/png"),
                pipeline_profile_id="uploaded-alpha", pipeline_revision="1")
            with pytest.raises(GatewayError) as error:
                await HTTPImageGateway(client).submit(request, job_id=uuid4())
            assert error.value.code == code
    for code in ("PRODUCT_APPROVAL_REQUIRED", "PRODUCT_APPROVAL_CONFLICT", "IDEMPOTENCY_CONFLICT"):
        asyncio.run(check(code))
