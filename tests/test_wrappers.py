from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from beauty_ai.contracts import (
    AdOptions, AssetRef, BackgroundRequest, CallMeta, CampaignDraft, CopyBundle,
    CopyVariant, CutoutRequest, ImageJob, LLMReview, ModelResult, ProductFacts,
    ProductObservation, Rect, ReviewIssue, Rule, RulePack, VisualBrief, content_hash,
)
from beauty_ai.errors import GatewayError, JobStillPending, SubmissionUncertain
from beauty_ai.image_gateway import HTTPImageGateway
from beauty_ai.image_integrity import composite_approved_foreground, to_diffusers_inpaint_mask
from beauty_ai.review_service import ReviewService, require_current_review, review_snapshot_hash
from beauty_ai.text_gateway import OpenAITextGateway

UID = UUID("00000000-0000-4000-8000-000000000001")
JOB_ID = UUID("00000000-0000-4000-8000-000000000002")
ASSET = AssetRef(asset_id=UID, sha256="a" * 64, width=1024, height=1024,
                 mime_type="image/png")
FACTS = ProductFacts(product_id=UID, revision=1, product_name="Test cream",
                     functional_status="general", functional_categories=[], confirmed_claims=[])
COPY = CopyBundle(headline="일상을 위한 케어", subheadline="나만의 루틴", cta="제품 보기",
                   caption="제품 소개", hashtags=["#테스트"], badges=[])
OPTIONS = AdOptions(purpose="신제품 소개", target="일반 소비자", tone="친근함", channel="instagram_feed",
                    market="KR", style_preset_id="minimal", layout_template_id="split-v1")
RULES = RulePack(version="test-only-v1", status="reviewed", reviewed_at="2026-10-20",
                 rules=[Rule(rule_id="TEST-BRAND-1", pattern="금칙샘플", severity="block",
                             reason="Synthetic test fixture, NOT a law", source_ref="test fixture")])
META = CallMeta(operation="review_copy", provider="fake", model="fake-review",
                provider_request_id="req-test", elapsed_ms=1, input_tokens=10, output_tokens=5,
                prompt_version="review-v1")
VISUAL = VisualBrief(
    concept="미니멀", style_preset_id="minimal", style_preset_version="1",
    layout_template_id="split", layout_template_version="1",
    generation_width=1024, generation_height=1024,
    product_box=Rect(x=.55, y=.25, width=.4, height=.65),
    text_safe_zone=Rect(x=.05, y=.15, width=.4, height=.6),
)


class FakeResponses:
    def __init__(self, response=None, exc=None, delay=0):
        self.response = response
        self.exc = exc
        self.delay = delay
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.response


def sdk_response(data, status="completed", output=None):
    return SimpleNamespace(
        status=status, output=output or [], output_parsed=data,
        model="fake-text", _request_id="req-123",
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def gateway(responses):
    return OpenAITextGateway(SimpleNamespace(responses=responses),
                             text_model="fake-text", vision_model="fake-vision",
                             review_model="fake-review")


class FakeReviewer:
    review_model = "fake-review"
    review_prompt_version = "review-v1"

    def __init__(self, review=None, exc=None):
        self.review_result = review or LLMReview(issues=[], uncertainties=[])
        self.exc = exc
        self.calls = 0

    async def review_copy(self, *args):
        self.calls += 1
        if self.exc:
            raise self.exc
        return ModelResult(data=self.review_result, meta=META)


def request():
    return CutoutRequest(source=ASSET, pipeline_profile_id="test-cutout", pipeline_revision="1")


def job_data(req, status="queued"):
    return {"job_id": str(JOB_ID), "request_hash": content_hash(req), "status": status,
            "result": None, "error_code": None}


async def test_text_parse_is_typed_and_records_usage():
    responses = FakeResponses(sdk_response(LLMReview(issues=[], uncertainties=[])))
    result = await gateway(responses).review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert isinstance(result.data, LLMReview)
    assert result.meta.input_tokens == 11
    call = responses.calls[0]
    assert call["store"] is False
    assert call["text_format"] is LLMReview
    assert "temperature" not in call
    assert "tools" not in call


async def test_vision_uses_images_without_inventing_product_facts():
    observation = ProductObservation(container_shape="tube", visible_colors=[],
                                     label_text=None, uncertainties=["small text"])
    responses = FakeResponses(sdk_response(observation))
    result = await gateway(responses).analyze_product(["data:image/png;base64,AA=="])
    assert result.data.label_text is None
    assert responses.calls[0]["model"] == "fake-vision"
    assert responses.calls[0]["input"][0]["content"][1]["type"] == "input_image"
    assert "functional_status" not in result.data.model_dump()


async def test_vision_rejects_arbitrary_remote_urls():
    with pytest.raises(ValueError):
        await gateway(FakeResponses()).analyze_product(["http://localhost/internal"])


async def test_refusal_is_not_an_empty_success():
    refusal = SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])
    responses = FakeResponses(sdk_response(None, output=[refusal]))
    with pytest.raises(GatewayError) as caught:
        await gateway(responses).review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert caught.value.code == "TEXT_REFUSAL"


async def test_incomplete_is_not_accepted_even_with_parsed_content():
    responses = FakeResponses(sdk_response(LLMReview(issues=[], uncertainties=[]), status="incomplete"))
    with pytest.raises(GatewayError) as caught:
        await gateway(responses).review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert caught.value.code == "TEXT_INCOMPLETE"


async def test_missing_structured_output_fails():
    with pytest.raises(GatewayError) as caught:
        await gateway(FakeResponses(sdk_response(None))).review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert caught.value.code == "INVALID_STRUCTURED_OUTPUT"


async def test_total_deadline_is_bounded_without_retry_loop():
    responses = FakeResponses(delay=.2)
    adapter = gateway(responses)
    adapter.total_timeout = .01
    with pytest.raises(GatewayError) as caught:
        await adapter.review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert caught.value.code == "TEXT_DEADLINE"
    assert len(responses.calls) == 1


async def test_programming_errors_are_not_hidden_as_provider_failures():
    with pytest.raises(TypeError):
        await gateway(FakeResponses(exc=TypeError("bug"))).review_copy(FACTS, COPY, OPTIONS, RULES, [])


async def test_sdk_rate_limit_is_normalized_without_wrapper_retry():
    class TestRateLimit(Exception):
        status_code = 429
        request_id = "req-limited"
    TestRateLimit.__module__ = "openai"
    responses = FakeResponses(exc=TestRateLimit())
    with pytest.raises(GatewayError) as caught:
        await gateway(responses).review_copy(FACTS, COPY, OPTIONS, RULES, [])
    assert caught.value.code == "TEXT_RATE_LIMIT"
    assert caught.value.retryable
    assert len(responses.calls) == 1


async def test_invented_claim_id_is_rejected():
    draft = CampaignDraft(variants=[CopyVariant(concept="minimal", key_message="routine",
                                                copy_bundle=COPY, used_claim_ids=["invented"])])
    with pytest.raises(GatewayError) as caught:
        await gateway(FakeResponses(sdk_response(draft))).draft_campaign(FACTS, OPTIONS, count=1)
    assert caught.value.code == "INVALID_DOMAIN_OUTPUT"


async def test_draft_count_must_match_request():
    draft = CampaignDraft(variants=[CopyVariant(concept="minimal", key_message="routine",
                                                copy_bundle=COPY, used_claim_ids=[])])
    with pytest.raises(GatewayError):
        await gateway(FakeResponses(sdk_response(draft))).draft_campaign(FACTS, OPTIONS, count=3)


async def test_deterministic_block_survives_clean_llm_review():
    text = FakeReviewer()
    copy = COPY.model_copy(update={"headline": "금칙샘플"})
    report = await ReviewService(text).review(FACTS, copy, OPTIONS, copy_revision=1, rules=RULES)
    assert report.status == "blocked"
    assert report.issues[0].rule_ids == ["TEST-BRAND-1"]
    assert report.legal_approval is False


async def test_captions_hashtags_and_badges_are_checked():
    copy = COPY.model_copy(update={"caption": "금칙샘플", "hashtags": ["#금칙샘플"],
                                   "badges": ["금칙샘플"]})
    report = await ReviewService(FakeReviewer()).review(FACTS, copy, OPTIONS, copy_revision=1, rules=RULES)
    assert {i.field for i in report.issues} == {"caption", "hashtags", "badges"}


async def test_review_outage_never_means_no_flags():
    report = await ReviewService(FakeReviewer(exc=GatewayError("TEXT_AUTH", "test"))).review(
        FACTS, COPY, OPTIONS, copy_revision=1, rules=RULES)
    assert report.status == "unavailable"
    assert report.uncertainties == ["TEXT_AUTH"]


async def test_draft_rule_pack_prevents_clean_review_and_skips_api():
    text = FakeReviewer()
    report = await ReviewService(text).review(
        FACTS, COPY, OPTIONS, copy_revision=1, rules=RULES.model_copy(update={"status": "draft"}))
    assert report.status == "unavailable"
    assert text.calls == 0


async def test_invalid_rule_pattern_is_unavailable():
    bad_rule = RULES.rules[0].model_copy(update={"pattern": "["})
    report = await ReviewService(FakeReviewer()).review(
        FACTS, COPY, OPTIONS, copy_revision=1, rules=RULES.model_copy(update={"rules": [bad_rule]}))
    assert report.status == "unavailable"


@pytest.mark.parametrize("changes", [
    {"rule_ids": ["invented-rule"]}, {"quote": "not present in the copy"},
])
async def test_invented_review_references_fail_closed(changes):
    issue = ReviewIssue(field="headline", quote=COPY.headline, severity="warning",
                        reason="test", suggested_text=None, rule_ids=[]).model_copy(update=changes)
    text = FakeReviewer(LLMReview(issues=[issue], uncertainties=[]))
    report = await ReviewService(text).review(FACTS, COPY, OPTIONS, copy_revision=1, rules=RULES)
    assert report.status == "unavailable"
    assert "INVALID_REVIEW_REFERENCES" in report.uncertainties


async def test_unconfirmed_functional_status_requires_review():
    facts = FACTS.model_copy(update={"functional_status": "unconfirmed"})
    report = await ReviewService(FakeReviewer()).review(facts, COPY, OPTIONS,
                                                       copy_revision=1, rules=RULES)
    assert report.status == "warnings"


async def test_review_becomes_stale_after_copy_edit():
    text = FakeReviewer()
    report = await ReviewService(text).review(FACTS, COPY, OPTIONS, copy_revision=1, rules=RULES)
    require_current_review(report, report.snapshot_hash)
    edited = COPY.model_copy(update={"caption": "수정한 캡션"})
    new_hash = review_snapshot_hash(FACTS, edited, OPTIONS, copy_revision=2, rules=RULES,
                                    prompt_version=text.review_prompt_version, model=text.review_model)
    with pytest.raises(GatewayError) as caught:
        require_current_review(report, new_hash)
    assert caught.value.code == "STALE_REVIEW"


def test_policy_content_changes_invalidate_hash_even_without_version_bump():
    args = dict(copy_revision=1, prompt_version="review-v1", model="fake")
    h1 = review_snapshot_hash(FACTS, COPY, OPTIONS, rules=RULES, **args)
    changed = RULES.model_copy(update={"rules": [RULES.rules[0].model_copy(update={"pattern": "changed"})]})
    h2 = review_snapshot_hash(FACTS, COPY, OPTIONS, rules=changed, **args)
    assert h1 != h2


def test_rule_ids_must_be_unique():
    with pytest.raises(ValidationError):
        RulePack(version="bad", status="draft", reviewed_at=None, rules=[RULES.rules[0]] * 2)


async def test_image_submission_uses_one_stable_job_id_and_no_paths():
    req = request()
    calls = []
    def handle(http_request):
        calls.append(http_request)
        assert http_request.method == "PUT"
        assert http_request.url.path == f"/internal/v1/jobs/{JOB_ID}"
        body = json.loads(http_request.content)
        assert body["source"]["asset_id"] == str(UID)
        assert "path" not in body["source"]
        return httpx.Response(202, json=job_data(req))
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        job = await HTTPImageGateway(client).submit(req, job_id=JOB_ID)
    assert job.status == "queued"
    assert len(calls) == 1


async def test_image_timeout_is_uncertain_and_not_resubmitted():
    count = 0
    def handle(http_request):
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("lost ack", request=http_request)
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SubmissionUncertain) as caught:
            await HTTPImageGateway(client).submit(request(), job_id=JOB_ID)
    assert caught.value.job_id == JOB_ID
    assert count == 1


async def test_image_5xx_is_uncertain_not_success_or_auto_retry():
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(503))) as client:
        with pytest.raises(SubmissionUncertain):
            await HTTPImageGateway(client).submit(request(), job_id=JOB_ID)


async def test_conflicting_idempotency_key_is_rejected():
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(409))) as client:
        with pytest.raises(GatewayError) as caught:
            await HTTPImageGateway(client).submit(request(), job_id=JOB_ID)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"


async def test_wrong_job_hash_is_rejected():
    data = job_data(request())
    data["request_hash"] = "b" * 64
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(202, json=data))) as client:
        with pytest.raises(GatewayError) as caught:
            await HTTPImageGateway(client).submit(request(), job_id=JOB_ID)
    assert caught.value.code == "IMAGE_PROTOCOL_ERROR"


async def test_pending_job_stays_pending_when_waiter_expires():
    req = request()
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=job_data(req, "running")))) as client:
        with pytest.raises(JobStillPending):
            await HTTPImageGateway(client).wait(JOB_ID, expected_request=req,
                                                 deadline_seconds=.01, poll_seconds=.002)


async def test_completed_image_job_returns_typed_assets():
    req = request()
    data = job_data(req, "succeeded")
    data["result"] = {"kind": "cutout", "product_rgba": ASSET.model_dump(mode="json"),
                      "preserve_mask": ASSET.model_dump(mode="json"),
                      "needs_manual_check": True, "warnings": ["transparent container"]}
    async with httpx.AsyncClient(base_url="http://inference", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=data))) as client:
        job = await HTTPImageGateway(client).wait(JOB_ID, expected_request=req)
    assert job.result.product_rgba.asset_id == UID
    assert job.result.needs_manual_check


def test_success_without_image_result_is_invalid():
    with pytest.raises(ValidationError):
        ImageJob.model_validate(job_data(request(), "succeeded"))


def test_image_hash_ignores_copy_but_changes_with_seed():
    req = BackgroundRequest(source=ASSET, product_rgba=ASSET, preserve_mask=ASSET,
                            visual=VISUAL, seed=42, pipeline_profile_id="sdxl-test", pipeline_revision="r1")
    assert "copy" not in req.model_dump()
    assert content_hash(req) != content_hash(req.model_copy(update={"seed": 43}))
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})


def test_generation_mask_polarity_is_explicit():
    preserve = Image.new("L", (2, 1))
    preserve.putdata([255, 0])
    result = to_diffusers_inpaint_mask(preserve)
    assert [result.getpixel((0, 0)), result.getpixel((1, 0))] == [0, 255]


def test_original_opaque_product_pixels_are_restored():
    generated = Image.new("RGB", (2, 1), (0, 0, 255))
    original = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    original.putpixel((0, 0), (10, 20, 30, 255))
    result = composite_approved_foreground(generated, original)
    assert result.getpixel((0, 0)) == (10, 20, 30, 255)
    assert result.getpixel((1, 0)) == (0, 0, 255, 255)


def test_transparency_is_blended_not_claimed_pixel_exact():
    generated = Image.new("RGB", (1, 1), (0, 0, 255))
    original = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    result = composite_approved_foreground(generated, original)
    assert result.getpixel((0, 0)) != original.getpixel((0, 0))


def test_resizing_is_not_hidden_inside_preservation_function():
    with pytest.raises(ValueError):
        composite_approved_foreground(Image.new("RGB", (2, 2)), Image.new("RGBA", (1, 1)))


def test_layout_overlapping_product_and_copy_is_rejected():
    values = VISUAL.model_dump()
    values["text_safe_zone"] = values["product_box"]
    with pytest.raises(ValidationError):
        VisualBrief.model_validate(values)
