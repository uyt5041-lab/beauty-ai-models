"""모의 SDK·이미지와 가상 단가로 광고 제작 원장의 전과정을 실행한다.

가상 원장 검증 예제이며 광고 생성 정책이나 실제 품질 검수를 구현하지 않는다.
환경의 키·모델 endpoint를 사용하지 않으며 모든 모델 전송은 MockTransport다.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from hashlib import sha256
from io import BytesIO
import inspect
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5

import httpx
from openai import AsyncOpenAI
from PIL import Image, ImageDraw

from beauty_ai.api_image_processor import ExternalImageProcessor
from beauty_ai.assets import LocalAssetStore
from beauty_ai.contracts import ExternalImageRequest, ImagePrompt, LLMReview
from beauty_ai.cost_reporting import write_report
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesAdapter, OpenAIImagesConfig
from beauty_ai.provider_attempts import ProviderAttemptStore
from beauty_ai.text_gateway import OpenAITextGateway
from beauty_ai.work_contracts import (MAX_JSON_BYTES, canonical_bytes, normalize_rate_book,
                                      normalize_usage, parse_work_json, validate_identifier)
from beauty_ai.work_ledger import AdWorkLedger, read_work_dataset
from beauty_ai.work_metrics import summarize_work_dataset
from beauty_ai.work_tracking import record_local_stage, track_ad_work

_FIXTURES = Path(__file__).resolve().parent / "evaluation"
_TEXT_CASES = {"baseline-plan": "draft_campaign", "baseline-review-failed": "review_copy",
               "baseline-review": "review_copy", "baseline-revision": "rewrite_copy",
               "candidate-plan": "draft_campaign", "candidate-review": "review_copy",
               "cancelled-review": "review_copy"}
_IMAGE_CASES = {"baseline-image", "baseline-revised", "candidate-image"}


def _fail(code="INPUT_INVALID"):
    raise ValueError("AD_WORK_DEMO_" + code)


def _read(path):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_JSON_BYTES + 1)
    return parse_work_json(raw)


def load_scenario(path) -> dict:
    """Accept only the public fixture fields used by this fixed telemetry flow."""
    data = _read(path)
    expected = {"schema_version", "data_kind", "fixture_ref", "description", "text_model", "text_cases", "image_cases"}
    if (type(data) is not dict or set(data) != expected
            or data["schema_version"] != "beautyai-ad-work-demo-v1" or data["data_kind"] != "SYNTHETIC_FIXTURE"
            or type(data["description"]) is not str or not 1 <= len(data["description"]) <= 1000):
        _fail()
    for key in ("fixture_ref", "text_model"):
        validate_identifier(data[key])
    for key, known in (("text_cases", _TEXT_CASES), ("image_cases", _IMAGE_CASES)):
        rows = data[key]
        if type(rows) is not list or len(rows) != len(known):
            _fail()
        found = set()
        fields = ({"case_ref", "operation", "prompt_version", "input_tokens", "output_tokens", "response"}
                  if key == "text_cases" else {"case_ref", "input_tokens", "output_tokens", "color"})
        for row in rows:
            if type(row) is not dict or set(row) != fields or type(row["case_ref"]) is not str:
                _fail()
            ref = row["case_ref"]
            if ref not in known or ref in found:
                _fail()
            found.add(ref)
            normalize_usage({"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"]})
            if row["input_tokens"] is None or row["output_tokens"] is None:
                _fail()
            normalize_usage({"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                             "total_tokens": row["input_tokens"] + row["output_tokens"]})
            if key == "text_cases":
                invalid = ref in {"baseline-review-failed", "cancelled-review"}
                if row["operation"] != _TEXT_CASES[ref] or row["response"] != ("schema_invalid" if invalid else "valid"):
                    _fail()
                validate_identifier(row["prompt_version"])
            elif (type(row["color"]) is not list or len(row["color"]) != 3
                  or any(type(channel) is not int or not 0 <= channel <= 255 for channel in row["color"])):
                _fail()
    return data


def _uid(name):
    return uuid5(NAMESPACE_URL, "beautyai-ad-work-demo/v1/" + name)


def _png(color):
    with Image.new("RGB", (1024, 1024), tuple(color)) as image:
        output = BytesIO()
        image.save(output, format="PNG")
    return output.getvalue()


class _MockProviders:
    """Own only deterministic response fixtures and counts; no live fallback."""
    def __init__(self, scenario):
        self.text = {row["case_ref"]: row for row in scenario["text_cases"]}
        self.images = {row["case_ref"]: row for row in scenario["image_cases"]}
        self.text_model = scenario["text_model"]
        self.active_text = self.active_image = None
        self.text_count = self.image_count = 0

    def text_response(self, request):
        row = self.text[self.active_text]
        self.text_count += 1
        if request.url.host != "offline.invalid" or request.headers.get("authorization") != "Bearer synthetic-unused-key":
            _fail("CLIENT_CONFIG_INVALID")
        if request.headers.get("x-stainless-retry-count") != "0":
            _fail("RETRY_UNEXPECTED")
        ref = row["case_ref"]
        return httpx.Response(200, headers={"x-request-id": "synthetic-text:" + ref}, json={
            "id": "response-" + ref, "object": "response", "created_at": 1793667600,
            "status": "completed", "model": self.text_model, "error": None, "incomplete_details": None,
            "output": [{"id": "message-" + ref, "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "annotations": [], "text": "{}" if row["response"] == "schema_invalid"
                             else '{"issues":[],"uncertainties":[]}'}]}],
            "usage": {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                      "total_tokens": row["input_tokens"] + row["output_tokens"]}})

    def image_response(self, request):
        row = self.images[self.active_image]
        self.image_count += 1
        return httpx.Response(200, headers={"x-request-id": "synthetic-image:" + row["case_ref"]}, json={
            "data": [{"b64_json": base64.b64encode(_png(row["color"])).decode("ascii")}],
            "usage": {"input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                      "total_tokens": row["input_tokens"] + row["output_tokens"]}})


def _processor(root, ledger, providers):
    return ExternalImageProcessor(LocalAssetStore(root / "assets"),
        OpenAIImagesAdapter(OpenAIImagesConfig(), transport=httpx.MockTransport(providers.image_response)),
        ProviderAttemptStore(root / "provider-attempts.sqlite3"), budget_limit_usd="10", work_ledger=ledger)


def _sdk_client_kwargs(http_client, constructor=AsyncOpenAI):
    """Keep the core 2.x API while supplying inert newer settings when supported."""
    kwargs = {"api_key": "synthetic-unused-key", "organization": "synthetic-org",
              "project": "synthetic-project", "base_url": "https://offline.invalid/v1",
              "default_headers": {"Authorization": "Bearer synthetic-unused-key"},
              "max_retries": 2, "http_client": http_client}
    optional = {"admin_api_key": "synthetic-unused-admin", "webhook_secret": "synthetic-unused-webhook",
                "websocket_base_url": "wss://offline.invalid/v1"}
    supported = inspect.signature(constructor).parameters
    return kwargs | {key: value for key, value in optional.items() if key in supported}


def _compose(root, ledger, project, work, processor, result, name):
    started = time.monotonic()
    directory = root / "artifacts"
    directory.mkdir(exist_ok=True)
    source = processor.assets.read_bytes(result.image, project_id=project)
    (directory / (name + "-source.png")).write_bytes(source)
    with Image.open(BytesIO(source)) as background:
        canvas = background.convert("RGB")
    try:
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((20, 20, 620, 110), fill="white")
        draw.text((40, 50), "Synthetic telemetry fixture / " + name, fill="black")
        final = directory / (name + "-final.png")
        canvas.save(final, format="PNG")
    finally:
        canvas.close()
    artifact = sha256(final.read_bytes()).hexdigest()
    record_local_stage(ledger, project, work, artifact_sha256=artifact,
                        elapsed_seconds=time.monotonic() - started)
    expected = [row["attempt_id"] for row in ledger.export_dataset(project)["attempts"] if row["work_id"] == str(work)]
    ledger.complete(project, work, {"artifact_sha256": artifact, "reviewer_ref": "synthetic-reviewer",
        "quality_policy_version": "synthetic-quality-v1", "quality_pass": True, "expected_attempt_ids": expected})
    return {"path": str(final), "sha256": artifact}


async def run_demo(output, *, scenario_path=None, rates_path=None) -> dict:
    """Create a new local directory and complete four explicitly synthetic works."""
    scenario = load_scenario(scenario_path or _FIXTURES / "ad-work-demo.json")
    rates = normalize_rate_book(_read(rates_path or _FIXTURES / "ad-work-rates.json"))
    if rates is None or not rates["rate_version"].startswith("synthetic-"):
        _fail("RATES_INVALID")
    for model in (scenario["text_model"], "gpt-image-2.5-sunburst-2026-09-08"):
        rate = rates["models"].get(model)
        if rate is None or rate["mode"] != "combined" or rate["input_usd"] is None or rate["output_usd"] is None:
            _fail("RATES_INVALID")
    root = Path(output).absolute()
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        _fail("OUTPUT_EXISTS")
    providers = _MockProviders(scenario)
    ledger = AdWorkLedger(root / "ad-works.sqlite3")
    project = _uid("project")
    works = {name: _uid("work-" + name) for name in ("baseline", "candidate", "cancelled", "open")}
    for name, work in works.items():
        paired = name in {"baseline", "candidate"}
        ledger.create_work(project, work, "synthetic-product", task_pair_id="synthetic-pair-1" if paired else None,
                            arm=name if paired else None)
    processor = _processor(root, ledger, providers)
    # Explicit inert credentials/endpoints; SDK-internal ambient headers cannot replace authentication.
    client = AsyncOpenAI(**_sdk_client_kwargs(httpx.AsyncClient(
        transport=httpx.MockTransport(providers.text_response), trust_env=False)))
    gateway = OpenAITextGateway(client, text_model=scenario["text_model"], vision_model=scenario["text_model"],
                                review_model=scenario["text_model"])
    requests, results, jobs, artifacts = {}, {}, {}, {}

    async def text(ref):
        providers.active_text = ref
        row = providers.text[ref]
        return await gateway._parse(operation=row["operation"], schema=LLMReview, model=scenario["text_model"],
            instruction="계측용 가상 자료만 처리한다. 실제 광고 승인이나 법률 검수가 아니다.",
            content=[{"type": "input_text", "text": "가상 제작 단계: " + ref}], prompt_version=row["prompt_version"])

    def image(ref):
        providers.active_image = ref
        jobs[ref] = _uid("job-" + ref)
        requests[ref] = ExternalImageRequest(operation="generate", width=1024, height=1024,
            prompt=ImagePrompt(positive="Synthetic product space for telemetry fixture " + ref,
                               prompt_version=ref + "-v1", source_revision=1))
        results[ref] = processor.process_job(requests[ref], project, jobs[ref])
        return results[ref]

    async def expected_failure(ref):
        try:
            await text(ref)
        except GatewayError as error:
            if error.code != "INVALID_STRUCTURED_OUTPUT":
                raise
        else:
            _fail("EXPECTED_FAILURE_MISSING")

    try:
        with track_ad_work(ledger, project, works["baseline"]):
            await text("baseline-plan")
            image("baseline-image")
            await expected_failure("baseline-review-failed")
            await text("baseline-review")
            await text("baseline-revision")
            revised = image("baseline-revised")
            if processor.process_job(requests["baseline-image"], project, jobs["baseline-image"]) != results["baseline-image"]:
                _fail("REUSE_MISMATCH")
            artifacts["baseline"] = _compose(root, ledger, project, works["baseline"], processor, revised, "baseline")
        with track_ad_work(ledger, project, works["candidate"]):
            await text("candidate-plan")
            candidate = image("candidate-image")
            await text("candidate-review")
            artifacts["candidate"] = _compose(root, ledger, project, works["candidate"], processor, candidate, "candidate")
        with track_ad_work(ledger, project, works["cancelled"]):
            await expected_failure("cancelled-review")
        ledger.cancel(project, works["cancelled"])
        ledger.bind_image_job(project, works["open"], _uid("job-unobserved"))
    finally:
        await client.close()
    before = ledger.export_dataset(project, rates, data_kind="SYNTHETIC_FIXTURE")
    restarted = AdWorkLedger(ledger.path)
    replay = _processor(root, restarted, providers)
    if replay.process_job(requests["candidate-image"], project, jobs["candidate-image"]) != results["candidate-image"]:
        _fail("REUSE_MISMATCH")
    dataset = read_work_dataset(ledger.path, project, rates, data_kind="SYNTHETIC_FIXTURE")
    if dataset != before:
        _fail("RESTART_MISMATCH")
    dataset_path = root / "work-dataset.json"
    dataset_path.write_bytes(canonical_bytes(dataset) + b"\n")
    paths = write_report({"schema_version": "beautyai-cost-dataset-v1", "cost_items": [], "batches": [], "acceptances": []},
                          output=root / "report", work_dataset=dataset)
    metrics = summarize_work_dataset(dataset)
    reuse_count = sum(len(replay.attempts.get_reuse_events(job, project)) for job in jobs.values())
    execution = {"schema_version": "beautyai-ad-work-demo-execution-v1", "status": "SYNTHETIC_DEMO_COMPLETED",
        "data_kind": "SYNTHETIC_FIXTURE", "fixture_ref": scenario["fixture_ref"], "paid_provider_calls": 0,
        "mock_text_sdk_calls": providers.text_count, "mock_image_posts": providers.image_count,
        "image_reuse_events": reuse_count, "restart_readback_verified": True,
        "project_id": str(project), "work_ids": {key: str(value) for key, value in works.items()},
        "job_ids": {key: str(value) for key, value in jobs.items()},
        "attempt_count": len(dataset["attempts"]), "receipt_count": len(dataset["receipts"]),
        "work_dataset_path": str(dataset_path), "ledger_path": str(ledger.path),
        "artifacts": artifacts, "summary": metrics["summary"], "comparison": metrics["comparison"], **paths}
    (root / "execution.json").write_bytes(canonical_bytes(execution) + b"\n")
    return execution


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="유료 호출 없이 모의 SDK·이미지·가상 단가로 제작 원장 전체 흐름을 실행합니다.")
    parser.add_argument("--output", required=True, type=Path, help="새로 만들 출력 폴더. 기존 폴더는 덮어쓰지 않습니다.")
    args = parser.parse_args(argv)
    try:
        receipt = asyncio.run(run_demo(args.output))
    except (ValueError, OSError, GatewayError):
        parser.exit(2, "AD_WORK_DEMO_FAILED\n")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
