"""Synthetic work lifecycle using the installed text SDK and image HTTP boundary."""
import base64
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
import time
from uuid import uuid4

import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageDraw
import pytest

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, LLMReview
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore
from beauty_ai.text_gateway import OpenAITextGateway
from beauty_ai.work_ledger import AdWorkLedger, read_work_dataset
from beauty_ai.work_metrics import summarize_work_dataset
from beauty_ai.work_tracking import record_local_stage, track_ad_work


def _png(color):
    with Image.new("RGB", (1024, 1024), color) as image:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _text_response(index, input_tokens, output_tokens, *, malformed=False):
    return httpx.Response(200, headers={"x-request-id": f"synthetic-text-{index}"}, json={
        "id": f"response-{index}", "object": "response", "created_at": 1793667600,
        "status": "completed", "model": "synthetic-text-model", "error": None,
        "incomplete_details": None,
        "output": [{"id": f"message-{index}", "type": "message", "status": "completed",
                    "role": "assistant", "content": [{"type": "output_text", "annotations": [],
                    "text": "{}" if malformed else '{"issues":[],"uncertainties":[]}'}]}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens,
                  "input_tokens_details": {"cached_tokens": 2},
                  "output_tokens_details": {"reasoning_tokens": 1}}})


async def test_work_from_planning_through_failed_review_revision_composition_and_restart(tmp_path):
    project, work = uuid4(), uuid4()
    ledger = AdWorkLedger(tmp_path / "ad-works.sqlite3")
    ledger.create_work(project, work, "synthetic-product")
    text_calls, image_calls = [], []
    text_usage = [(10, 4), (3, 2), (5, 3), (7, 4)]

    def text_handler(request):
        index = len(text_calls)
        text_calls.append(request)
        return _text_response(index, *text_usage[index], malformed=index == 1)

    image_usage = [(20, 30), (25, 35)]

    def image_handler(request):
        index = len(image_calls)
        image_calls.append(request)
        pixels = _png((220, 230, 240) if index == 0 else (235, 215, 195))
        incoming, outgoing = image_usage[index]
        return httpx.Response(200, headers={"x-request-id": f"synthetic-image-{index}"}, json={
            "data": [{"b64_json": base64.b64encode(pixels).decode("ascii")}],
            "usage": {"input_tokens": incoming, "output_tokens": outgoing,
                      "total_tokens": incoming + outgoing}})

    def image_processor(work_store):
        assets = LocalAssetStore(tmp_path / "assets")
        attempts = ProviderAttemptStore(tmp_path / "provider-attempts.sqlite3")
        adapter = OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(image_handler))
        return ExternalImageProcessor(assets, adapter, attempts, budget_limit_usd="10",
                                      work_ledger=work_store)

    first_job, revised_job = uuid4(), uuid4()
    first_request = ExternalImageRequest(operation="generate", width=1024, height=1024, prompt=ImagePrompt(
        positive="PRIVATE synthetic prompt 9142", prompt_version="image-v1", source_revision=1))
    revised_request = ExternalImageRequest(operation="generate", width=1024, height=1024, prompt=ImagePrompt(
        positive="PRIVATE revised prompt 9143", prompt_version="image-v2", source_revision=2))
    processor = image_processor(ledger)
    client = AsyncOpenAI(api_key="synthetic-integration-key", max_retries=2,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(text_handler)))
    gateway = OpenAITextGateway(client, text_model="synthetic-text-model", vision_model="synthetic-text-model",
                                review_model="synthetic-text-model")

    async def text(operation, version):
        return await gateway._parse(operation=operation, schema=LLMReview,
            model="synthetic-text-model", instruction="Evaluate only synthetic fixture data.",
            content=[{"type": "input_text", "text": "PRIVATE synthetic copy 9144"}],
            prompt_version=version)

    try:
        with track_ad_work(ledger, project, work):
            # The schema is a small typed SDK fixture; operation controls the real stage mapping.
            await text("draft_campaign", "plan-v1")
            first_result = processor.process_job(first_request, project, first_job)
            with pytest.raises(GatewayError) as failed_review:
                await text("review_copy", "review-v1")
            assert failed_review.value.code == "INVALID_STRUCTURED_OUTPUT"
            await text("review_copy", "review-v2")
            await text("rewrite_copy", "revision-v1")
            revised_result = processor.process_job(revised_request, project, revised_job)
        assert len(text_calls) == 4 and len(image_calls) == 2
        assert all(request.headers["x-stainless-retry-count"] == "0" for request in text_calls)
        assert client.max_retries == 2

        # A new object graph reads the durable work/job binding and reuses the first paid result.
        restarted = AdWorkLedger(ledger.path)
        restarted_processor = image_processor(restarted)
        before_reuse = restarted.export_dataset(project)
        reused = restarted_processor.process_job(first_request, project, first_job)
        assert reused == first_result and len(image_calls) == 2
        assert restarted.export_dataset(project) == before_reuse
        assert len(restarted_processor.attempts.get_reuse_events(first_job, project)) == 1

        started = time.monotonic()
        source = restarted_processor.assets.read_bytes(revised_result.image, project_id=project)
        with Image.open(BytesIO(source)) as background:
            canvas = background.convert("RGB")
        try:
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((20, 20, 500, 100), fill="white")
            draw.text((40, 45), "Synthetic reviewed advertisement", fill="black")
            final_path = tmp_path / "synthetic-final-ad.png"
            canvas.save(final_path, format="PNG")
        finally:
            canvas.close()
        final_bytes = final_path.read_bytes()
        final_hash = sha256(final_bytes).hexdigest()
        assert final_hash != revised_result.image.sha256
        with Image.open(BytesIO(final_bytes)) as final:
            assert final.format == "PNG" and final.size == (1024, 1024)
        record_local_stage(restarted, project, work, artifact_sha256=final_hash,
                            elapsed_seconds=time.monotonic() - started)
        expected_attempts = [row["attempt_id"] for row in restarted.export_dataset(project)["attempts"]]
        completed = {"artifact_sha256": final_hash, "reviewer_ref": "synthetic-reviewer",
                     "quality_policy_version": "synthetic-quality-v1", "quality_pass": True,
                     "expected_attempt_ids": expected_attempts}
        restarted.complete(project, work, completed)
        # An exact completion replay is safe, including after reopening the file.
        completed_store = AdWorkLedger(ledger.path)
        completed_store.complete(project, work, completed)
        rates = {"schema_version": "beautyai-work-rates-v1", "rate_version": "synthetic-rates-v1",
                 "models": {
                     "synthetic-text-model": {"mode": "combined", "input_usd": "2", "output_usd": "4",
                                                "input_text_usd": None, "input_image_usd": None},
                     first_request.model: {"mode": "combined", "input_usd": "10", "output_usd": "20",
                                           "input_text_usd": None, "input_image_usd": None}}}
        data = read_work_dataset(ledger.path, project, rates, data_kind="SYNTHETIC_FIXTURE")
        assert data == completed_store.export_dataset(project, rates, data_kind="SYNTHETIC_FIXTURE")
        assert data["works"][0]["status"] == "COMPLETED"
        assert data["works"][0]["completion"]["artifact_sha256"] == final_hash
        assert len(data["attempts"]) == len(data["receipts"]) == 7
        assert len(data["image_bindings"]) == 2
        assert Counter(row["stage"] for row in data["attempts"]) == {
            "prompt_build": 1, "image_generation": 2, "review": 2, "revision": 1, "composition": 1}
        summary = summarize_work_dataset(data)
        row, totals = summary["works"][0], summary["summary"]
        assert (row["attempt_count"], row["model_api_attempt_count"], row["local_stage_count"],
                row["failed_count"], row["revision_attempt_count"], row["pending_count"]) == (7, 6, 1, 1, 1, 0)
        assert (row["known_usage_count"], row["missing_usage_count"], row["missing_cost_count"]) == (6, 0, 0)
        assert (totals["input_tokens"], totals["output_tokens"]) == (70, 78)
        assert totals["estimated_total_usd"] == totals["cost_per_completed_ad_usd"] == "0.001852000"
        assert totals["actual_total_usd"] is None and totals["completed_ad_count"] == 1
        failed = next(receipt for receipt in data["receipts"] if receipt["status"] == "FAILED")
        assert (failed["usage"]["input_tokens"], failed["usage"]["output_tokens"]) == (3, 2)
        review = next(stage for stage in row["stages"] if stage["stage"] == "review")
        assert review["estimated_total_usd"] == "0.000036000"  # Includes the failed structured response.
        assert sum(stage["cached_input_tokens"] or 0 for stage in row["stages"]) == 8
        assert sum(stage["reasoning_tokens"] or 0 for stage in row["stages"]) == 4
        local = next(receipt for receipt in data["receipts"] if receipt["model"] == "local")
        assert local["estimated_cost_usd"] == "0.000000000" and local["actual_cost_usd"] is None
        assert "PRIVATE" not in json.dumps(data) and "synthetic-integration-key" not in json.dumps(data)
        assert summary["comparison"]["compared_pair_count"] == 0
        # The completed work remains replayable through the existing successful image receipt.
        assert restarted_processor.process_job(revised_request, project, revised_job) == revised_result
        assert len(image_calls) == 2 and len(text_calls) == 4
    finally:
        await client.close()
