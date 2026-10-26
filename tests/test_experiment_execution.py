"""Stage 4 offline execution contracts; never use credentials or real providers."""
from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from beauty_ai import experiment_execution, experiment_runner
from beauty_ai.errors import GatewayError
from beauty_ai.experiment_runner import load_verified_plan, prepare_experiment


DESCRIPTION = "One matte cosmetic bottle, with its original shape and printed label."


def test_wandb_hook_runs_after_saved_final_report(prepare, mock_output_png, monkeypatch):
    path, root, _, digest = prepare()
    from beauty_ai import experiment_wandb
    observed = []
    def hook(plan_path, **kwargs):
        saved = json.loads((root / "report.json").read_text())
        assert saved["status"] == "SUCCEEDED" and saved["finished_at"] is not None
        observed.append(kwargs)
        return {"status":"SKIPPED_MOCK"}
    monkeypatch.setattr(experiment_wandb, "finish_recording", hook)
    report = experiment_execution.execute_plan(path, expected_sha256=digest, budget_usd="1",
        transport=httpx.MockTransport(lambda request: success_response(mock_output_png)), wandb_entity="team")
    assert observed == [{"expected_sha256":digest, "entity":"team", "project":"beautyai-image-experiments", "live":False}]
    assert_saved_report(root, report)
    from beauty_ai.experiment_trial_records import collect_trial
    from beauty_ai.wandb_experiments import prepare_package
    assert collect_trial(path) == []
    records = collect_trial(path, include_mock=True)
    assert len(records) == 1 and records[0]["family"] == "mock_api"
    package = prepare_package(records[0], root, root / "mock-package")
    assert package["payload"]["actual_cost_usd"] is None
    assert len(package["payload"]["images"]) == 3


@pytest.fixture(autouse=True)
def forbid_credentials_and_real_http(monkeypatch):
    def forbidden_credentials(*_args, **_kwargs):
        pytest.fail("Mock experiment execution must not read environment credentials")

    def forbidden_http(*_args, **_kwargs):
        pytest.fail("Experiment execution tests must not use real HTTP transport")

    async def forbidden_async_http(*_args, **_kwargs):
        pytest.fail("Experiment execution tests must not use real async HTTP transport")

    monkeypatch.setattr(experiment_execution, "_load_settings", forbidden_credentials)
    monkeypatch.setattr(experiment_execution, "begin_image_generation", forbidden_http)
    monkeypatch.setattr(experiment_execution, "_trace_environment", forbidden_credentials)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async_http)


@pytest.fixture
def prepare(tmp_path):
    products = []
    for index, color in enumerate(("#713d91", "#6f7b8d"), 1):
        path = tmp_path / f"product-{index}.png"
        with Image.new("RGB", (32, 40), color) as image:
            image.save(path, "PNG")
        products.append(path)

    def make(count=1):
        path = prepare_experiment(
            products=products,
            references=[],
            output=tmp_path / "outputs" / f"mock-{count}",
            product_description=DESCRIPTION,
            presets=["clean_studio", "vivid_pop"][:count],
            prompt_version="cosmetics-v1",
            max_calls=count,
        )
        root, plan, digest = load_verified_plan(path)
        return path, root, plan, digest

    return make


@pytest.fixture
def mock_output_png():
    stream = BytesIO()
    with Image.new("RGB", (1152, 1440), "#dcdfe8") as image:
        image.save(stream, "PNG")
    return stream.getvalue()


def success_response(png: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "mock-experiment-response"},
        json={
            "data": [{"b64_json": base64.b64encode(png).decode("ascii")}],
            "usage": {
                "input_tokens": 8,
                "output_tokens": 7,
                "total_tokens": 15,
                "input_tokens_details": {"image_tokens": 5, "text_tokens": 3},
            },
        },
    )


def execute(path: Path, digest: str, transport: httpx.MockTransport, *, budget="1") -> dict:
    return experiment_execution.execute_plan(
        path,
        expected_sha256=digest,
        # This path must not be opened in MockTransport mode.
        env_file=path.parent / "never-read.env",
        budget_usd=budget,
        reservation_usd="1",
        transport=transport,
    )


def assert_provider_posts(requests, count):
    assert len(requests) == count
    assert all(request.method == "POST" for request in requests)
    assert all(request.url.host == "api.openai.com" for request in requests)
    assert all(request.url.path == "/v1/images/edits" for request in requests)


def assert_saved_report(root: Path, report: dict):
    assert json.loads((root / "report.json").read_text(encoding="utf-8")) == report
    assert report["review_status"] == "NOT_APPROVED"
    assert report["billing_status"] == "UNRECONCILED"
    assert report["actual_cost_usd"] is None


@pytest.mark.parametrize("count", [1, 2])
def test_mock_selected_cases_succeed_once_with_frozen_artifacts(
    prepare, mock_output_png, count,
):
    path, root, plan, digest = prepare(count)
    requests = []

    def provider(request):
        requests.append(request)
        return success_response(mock_output_png)

    report = execute(path, digest, httpx.MockTransport(provider), budget=str(count))

    assert report["status"] == "SUCCEEDED"
    assert report["attempt_slots"] == count
    assert_provider_posts(requests, count)
    assert len(report["operations"]) == count
    assert_saved_report(root, report)
    for case, operation in zip(plan["cases"], report["operations"], strict=True):
        assert operation["status"] == "succeeded"
        assert operation["case_id"] == case["case_id"]
        assert operation["job_id"] == case["job_id"]
        assert operation["preset_id"] == case["preset_id"]
        assert operation["request_sha256"] == case["request_sha256"]
        assert operation["provider_prompt_sha256"] == case["provider_prompt_sha256"]
        assert operation["langfuse"]["status"] == "SKIPPED_OFFLINE_OR_LOCAL"
        folder = root / "cases" / case["case_id"]
        assert json.loads((folder / "request.json").read_text()) == case["request"]
        assert (folder / "provider-prompt.txt").read_text() == case["provider_prompt"]
        assert json.loads((folder / "report.json").read_text()) == operation
        assert operation["image_path"] == f"cases/{case['case_id']}/image.png"
        image_path = root / operation["image_path"]
        assert image_path.is_file()
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == operation["output_sha256"]
        with Image.open(image_path) as image:
            assert image.size == (1152, 1440)


def test_first_provider_failure_stops_remaining_cases(prepare):
    path, root, _plan, digest = prepare(2)
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"message": "private provider error"}})

    report = execute(path, digest, httpx.MockTransport(provider), budget="2")

    assert report["status"] == "FAILED"
    assert report["attempt_slots"] == 1
    assert [operation["status"] for operation in report["operations"]] == [
        "failed", "SKIPPED_AFTER_STOP",
    ]
    assert_provider_posts(requests, 1)
    assert_saved_report(root, report)
    assert not list((root / "cases").glob("*/image.png"))
    assert "private provider error" not in json.dumps(report)


def test_uncertain_submission_stops_without_retrying_or_starting_next_case(prepare):
    path, root, _plan, digest = prepare(2)
    requests = []

    def provider(request):
        requests.append(request)
        raise httpx.ReadTimeout("private request payload", request=request)

    report = execute(path, digest, httpx.MockTransport(provider), budget="2")

    assert report["status"] == "UNKNOWN"
    assert report["attempt_slots"] == 1
    assert [operation["status"] for operation in report["operations"]] == [
        "unknown", "SKIPPED_AFTER_STOP",
    ]
    assert_provider_posts(requests, 1)
    assert_saved_report(root, report)
    assert not list((root / "cases").glob("*/image.png"))
    assert "private request payload" not in json.dumps(report)


@pytest.mark.parametrize("provider_outcome", ["success", "failure", "unknown"])
def test_reexecution_cannot_submit_again_or_replace_original_report(
    prepare, mock_output_png, provider_outcome,
):
    path, root, _plan, digest = prepare()
    requests = []

    def provider(request):
        requests.append(request)
        if provider_outcome == "failure":
            return httpx.Response(400, json={"error": {"message": "mock rejection"}})
        if provider_outcome == "unknown":
            raise httpx.ReadTimeout("mock response lost", request=request)
        return success_response(mock_output_png)

    transport = httpx.MockTransport(provider)
    original = execute(path, digest, transport)
    report_bytes = (root / "report.json").read_bytes()
    with pytest.raises(GatewayError) as caught:
        execute(path, digest, transport)

    assert caught.value.code == "EXPERIMENT_ALREADY_CLAIMED_OR_MISMATCH"
    assert_provider_posts(requests, 1)
    assert (root / "report.json").read_bytes() == report_bytes
    assert_saved_report(root, original)


@pytest.mark.parametrize("budget", ["0", "1.999999"])
def test_insufficient_budget_rejects_entire_plan_before_any_provider_post(prepare, budget):
    path, root, _plan, digest = prepare(2)
    requests = []

    def provider(request):
        requests.append(request)
        pytest.fail("No case may start when the whole plan cannot be reserved")

    with pytest.raises(ValueError, match="^EXPERIMENT_BUDGET_INSUFFICIENT$"):
        execute(path, digest, httpx.MockTransport(provider), budget=budget)

    assert requests == []
    assert not (root / "report.json").exists()


def test_live_without_authorization_is_rejected_before_reading_plan(tmp_path, monkeypatch):
    def forbidden_plan_read(*_args, **_kwargs):
        pytest.fail("An unauthorized live request must be rejected before loading the plan")

    monkeypatch.setattr(experiment_runner, "load_verified_plan", forbidden_plan_read)
    with pytest.raises(ValueError, match="^EXPERIMENT_LIVE_NOT_AUTHORIZED$"):
        experiment_execution.execute_plan(
            tmp_path / "missing" / "plan.json",
            expected_sha256="0" * 64,
            budget_usd="1",
            allow_live=False,
        )


@pytest.mark.parametrize("tamper", ["expected_hash", "normalized_input", "request", "prompt"])
def test_changed_plan_inputs_or_prompt_are_rejected_before_provider_post(prepare, tamper):
    path, root, plan, digest = prepare()
    if tamper == "expected_hash":
        digest = "0" * 64
    elif tamper == "normalized_input":
        (root / plan["inputs"][0]["path"]).write_bytes(b"PRIVATE_TAMPERED_INPUT")
    elif tamper == "request":
        request_path = root / "cases" / plan["cases"][0]["case_id"] / "request.json"
        request_path.write_text("{}", encoding="utf-8")
    else:
        prompt_path = root / "cases" / plan["cases"][0]["case_id"] / "provider-prompt.txt"
        prompt_path.write_text("PRIVATE_TAMPERED_PROMPT", encoding="utf-8")
    requests = []

    def provider(request):
        requests.append(request)
        pytest.fail("Integrity rejection must precede any provider call")

    with pytest.raises(ValueError, match="^EXPERIMENT_PLAN_OR_ASSET_INTEGRITY_INVALID$"):
        execute(path, digest, httpx.MockTransport(provider))

    assert requests == []
    assert not (root / "report.json").exists()
