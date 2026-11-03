"""Portable synthetic work execution with real SDK parsing and no live transports."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from PIL import Image
import pytest

from examples.run_ad_work import _sdk_client_kwargs, load_scenario, main, run_demo
from beauty_ai.work_contracts import normalize_work_dataset
from beauty_ai.work_ledger import read_work_dataset
from beauty_ai.work_metrics import summarize_work_dataset

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "evaluation"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("ad-work-demo") / "new-output"
    return asyncio.run(run_demo(root))


def test_demo_completes_installed_sdk_and_image_workflows_with_observed_counts(demo):
    assert demo["status"] == "SYNTHETIC_DEMO_COMPLETED" and demo["data_kind"] == "SYNTHETIC_FIXTURE"
    assert (demo["paid_provider_calls"], demo["mock_text_sdk_calls"], demo["mock_image_posts"], demo["image_reuse_events"]) == (0, 7, 3, 2)
    assert demo["attempt_count"] == demo["receipt_count"] == 12 and demo["restart_readback_verified"]
    saved = json.loads((Path(demo["ledger_path"]).parent / "execution.json").read_text())
    assert saved == demo
    summary = demo["summary"]
    assert (summary["work_count"], summary["completed_work_count"], summary["cancelled_work_count"], summary["open_work_count"]) == (4, 2, 1, 1)
    assert summary["estimated_total_usd"] is summary["actual_total_usd"] is summary["cost_per_completed_ad_usd"] is None
    assert summary["known_partial_estimated_usd"] == "0.002584000"
    assert (summary["known_input_tokens"], summary["known_output_tokens"], summary["missing_usage_count"], summary["missing_cost_count"]) == (102, 109, 1, 1)
    comparison = demo["comparison"]
    assert (comparison["compared_pair_count"], comparison["excluded_pair_count"], comparison["excluded_work_count"]) == (1, 2, 2)
    assert comparison["baseline"]["total_estimated_cost_usd"] == "0.001852000"
    assert comparison["candidate"]["total_estimated_cost_usd"] == "0.000724000"
    assert comparison["estimated_cost_reduction_pct"] == "60.907127430"


def test_demo_final_files_are_actual_composed_pngs_bound_to_explicit_completions(demo):
    data = json.loads(Path(demo["work_dataset_path"]).read_text())
    assert normalize_work_dataset(data) == data
    for arm, artifact in demo["artifacts"].items():
        path = Path(artifact["path"])
        assert sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        with Image.open(path) as final:
            assert final.format == "PNG" and final.size == (1024, 1024)
        source = path.with_name(arm + "-source.png")
        assert sha256(source.read_bytes()).hexdigest() != artifact["sha256"]
        work = next(row for row in data["works"] if row["work_id"] == demo["work_ids"][arm])
        assert work["status"] == "COMPLETED" and work["completion"]["quality_pass"] is True
        assert work["completion"]["artifact_sha256"] == artifact["sha256"]
        assert set(work["completion"]["expected_attempt_ids"]) == {
            row["attempt_id"] for row in data["attempts"] if row["work_id"] == work["work_id"]}


def test_failed_review_usage_cancelled_cost_and_unobserved_job_survive_restart(demo):
    data = json.loads(Path(demo["work_dataset_path"]).read_text())
    reread = read_work_dataset(demo["ledger_path"], demo["project_id"], data["rate_book"], data_kind="SYNTHETIC_FIXTURE")
    assert reread == data
    failed = [row for row in data["receipts"] if row["status"] == "FAILED"]
    assert len(failed) == 2 and all(row["error_code"] == "INVALID_STRUCTURED_OUTPUT" for row in failed)
    assert sorted((row["usage"]["input_tokens"], row["usage"]["output_tokens"]) for row in failed) == [(2, 1), (3, 2)]
    metrics = summarize_work_dataset(data)
    cancelled = next(row for row in metrics["works"] if row["status"] == "CANCELLED")
    assert cancelled["estimated_total_usd"] == "0.000008000"
    pending = next(row for row in metrics["works"] if row["status"] == "OPEN")
    assert pending["unobserved_image_job_count"] == 1 and pending["attempt_count"] == 0
    assert pending["estimated_total_usd"] is pending["input_tokens"] is None
    assert metrics["summary"] == demo["summary"]
    local = [row for row in data["receipts"] if row["model"] == "local"]
    assert len(local) == 2 and all(row["estimated_cost_usd"] == "0.000000000" and row["actual_cost_usd"] is None for row in local)
    report = json.loads(Path(demo["report_path"]).read_text())
    assert report["data_kind"] == "SYNTHETIC_FIXTURE" and report["work_costs"] == metrics
    html = Path(demo["html_path"]).read_text()
    assert 'id="work-costs"' in html and "합성 예제" in html and "60.90712743%" in html


def test_portable_module_cli_uses_fixture_paths_relative_to_its_module(tmp_path):
    output = tmp_path / "portable"
    environment = dict(os.environ, PYTHONPATH=str(ROOT), OPENAI_API_KEY="do-not-use-env-key",
                       OPENAI_BASE_URL="https://do-not-contact.invalid", OPENAI_ORG_ID="do-not-use-org",
                       OPENAI_PROJECT_ID="do-not-use-project", OPENAI_CUSTOM_HEADERS="Authorization: Bearer do-not-use-auth")
    process = subprocess.run([sys.executable, "-m", "examples.run_ad_work", "--output", str(output)],
                             cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    receipt = json.loads(process.stdout)
    assert receipt["paid_provider_calls"] == 0 and Path(receipt["report_path"]).is_file()
    assert "do-not-use" not in (output / "execution.json").read_text()
    repeated = subprocess.run([sys.executable, "-m", "examples.run_ad_work", "--output", str(output)],
                              cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)
    assert repeated.returncode == 2 and repeated.stderr.strip() == "AD_WORK_DEMO_FAILED"


def test_demo_does_not_read_credential_endpoint_environment_or_construct_live_transport(tmp_path, monkeypatch):
    real_get = os.environ.get
    forbidden = {"OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
                 "OPENAI_BASE_URL", "OPENAI_WEBHOOK_SECRET", "AZURE_OPENAI_API_KEY"}

    def guarded_get(key, *args):
        if key in forbidden:
            pytest.fail("The synthetic demo must not consult credential or endpoint environment configuration")
        return real_get(key, *args)

    def no_live_transport(*_args, **_kwargs):
        pytest.fail("The demo must always inject MockTransport")

    monkeypatch.setenv("OPENAI_API_KEY", "environment-key-must-not-be-read")
    monkeypatch.setenv("OPENAI_ORG_ID", "environment-org-must-not-be-read")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "environment-project-must-not-be-read")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://environment-endpoint.invalid")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer environment-auth-must-not-be-used")
    monkeypatch.setattr(os.environ, "get", guarded_get)
    monkeypatch.setattr(httpx.HTTPTransport, "__init__", no_live_transport)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "__init__", no_live_transport)
    receipt = asyncio.run(run_demo(tmp_path / "offline"))
    assert receipt["paid_provider_calls"] == 0 and receipt["mock_image_posts"] == 3
    for key in ("work_dataset_path", "report_path", "html_path"):
        assert "environment-" not in Path(receipt[key]).read_text()


def test_existing_output_is_rejected_without_mutating_any_saved_evidence(demo):
    root = Path(demo["ledger_path"]).parent
    before = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
              for path in root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="^AD_WORK_DEMO_OUTPUT_EXISTS$"):
        asyncio.run(run_demo(root))
    after = {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
             for path in root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(extra="not allowed"),
    lambda data: data.update(data_kind="DECLARED_DATA"),
    lambda data: data["text_cases"][0].update(input_tokens=True),
    lambda data: data["text_cases"][0].update(input_tokens=2**63 - 1, output_tokens=2),
    lambda data: data["text_cases"][0].update(operation="review_copy"),
    lambda data: data["text_cases"][1].update(response="valid"),
    lambda data: data["text_cases"].append(deepcopy(data["text_cases"][0])),
    lambda data: data["image_cases"][0].update(color=[255, -1, 0]),
    lambda data: data["image_cases"][0].update(color=[True, 1, 2]),
    lambda data: data.update(text_model="sk-private-value1234567"),
])
def test_invalid_manifest_is_rejected_before_creating_output(tmp_path, mutate):
    data = json.loads((FIXTURES / "ad-work-demo.json").read_text())
    mutate(data)
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(data))
    output = tmp_path / "not-created"
    with pytest.raises(ValueError, match=r"^(?:AD_WORK_DEMO|WORK)_[A-Z_]+$"):
        asyncio.run(run_demo(output, scenario_path=source))
    assert not output.exists()


@pytest.mark.parametrize("raw", ['{"schema_version":1,"schema_version":2}', '{"value":NaN}', ' ' * (1024 * 1024 + 1)])
def test_scenario_reader_rejects_duplicate_nonfinite_and_oversized_input(tmp_path, raw):
    source = tmp_path / "scenario.json"
    source.write_text(raw)
    with pytest.raises(ValueError, match=r"^WORK_[A-Z_]+$"):
        load_scenario(source)


def test_nonfictional_or_unpriced_rates_are_rejected_before_output(tmp_path):
    rates = json.loads((FIXTURES / "ad-work-rates.json").read_text())
    source = tmp_path / "rates.json"
    for mutation in ("name", "missing_model"):
        invalid = deepcopy(rates)
        if mutation == "name":
            invalid["rate_version"] = "provider-current-prices"
        else:
            invalid["models"].pop("synthetic-text-model")
        source.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match="^AD_WORK_DEMO_RATES_INVALID$"):
            asyncio.run(run_demo(tmp_path / "not-created", rates_path=source))
    assert not (tmp_path / "not-created").exists()


def test_cli_help_is_korean_and_explains_new_directory_requirement(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    text = capsys.readouterr().out
    assert "유료 호출 없이" in text and "기존 폴더는 덮어쓰지 않습니다" in text


async def test_client_kwargs_accept_a_legacy_constructor_without_new_optional_fields():
    class LegacyClient:
        def __init__(self, *, api_key, organization, project, base_url, default_headers,
                     max_retries, http_client):
            self.api_key, self.organization, self.project = api_key, organization, project
            self.base_url, self.headers = base_url, default_headers
            self.max_retries, self.http_client = max_retries, http_client

    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={"mock": True}))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as http_client:
        kwargs = _sdk_client_kwargs(http_client, constructor=LegacyClient)
        assert not {"admin_api_key", "webhook_secret", "websocket_base_url"}.intersection(kwargs)
        client = LegacyClient(**kwargs)
        assert client.api_key == "synthetic-unused-key" and client.organization == "synthetic-org"
        assert client.project == "synthetic-project" and client.base_url == "https://offline.invalid/v1"
        assert client.headers == {"Authorization": "Bearer synthetic-unused-key"} and client.max_retries == 2
        response = await client.http_client.get(client.base_url + "/responses", headers=client.headers)
        assert response.json() == {"mock": True} and len(calls) == 1
        assert calls[0].url.host == "offline.invalid"
