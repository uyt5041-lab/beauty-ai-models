"""Freeform preparation/execution stays behind existing offline and budget gates."""
import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest

from beauty_ai import experiment_execution, freeform_image
from beauty_ai.account_budget import AccountBudgetPolicy, AccountBudgetStore
from beauty_ai.errors import GatewayError


@pytest.fixture(autouse=True)
def no_credentials_or_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Freeform offline tests cannot load keys or use network")
    monkeypatch.setattr(experiment_execution, "_load_settings", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def inputs(tmp_path):
    product, reference = tmp_path / "product.png", tmp_path / "reference.png"
    Image.new("RGB", (32, 48), "red").save(product)
    Image.new("RGB", (48, 32), "blue").save(reference)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("제품을 보존하세요. <script>alert('fixture')</script>", encoding="utf-8")
    return {"products": [product], "references": [reference], "prompt_file": prompt,
            "output": tmp_path / "outputs" / "freeform", "project_id": uuid4(), "job_id": uuid4()}


def budget(tmp_path):
    return AccountBudgetStore(tmp_path / "account.sqlite3", AccountBudgetPolicy(
        account_ref="fixture", period_id="fixture-period", starts_at="2020-01-01T00:00:00Z",
        ends_at="2100-01-01T00:00:00Z", hard_limit_usd="10", opening_spend_usd="0",
        opening_evidence_ref="fixture:operator-declaration"))


def test_preview_preserves_revision_one_and_escapes_html(inputs):
    path = freeform_image.prepare_freeform(**inputs)
    saved = json.loads(path.read_text())
    assert saved["status"] == "PREPARED_NOT_RUN" and saved["image_api_calls"] == 0
    assert saved["request"]["pipeline_revision"] == "1"
    assert saved["request"]["prompt"]["language"] == "ko"
    assert saved["request"]["prompt"]["positive"] == inputs["prompt_file"].read_text()
    assert len(saved["request"]["product_images"]) == len(saved["request"]["reference_images"]) == 1
    assert "<script>" not in (path.parent / "index.html").read_text()
    assert "&lt;script&gt;" in (path.parent / "index.html").read_text()
    assert not (path.parent / "runtime/provider_attempts.sqlite3").exists()
    assert len(list((path.parent / "inputs").glob("*.png"))) == 2


def test_mock_execution_uses_shared_budget_and_reuses_receipt(inputs, tmp_path):
    path = freeform_image.prepare_freeform(**inputs)
    store, requests = budget(tmp_path), []
    data = BytesIO()
    Image.new("RGB", (1152, 1440), "green").save(data, "PNG")
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(data.getvalue()).decode()}],
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}})
    arguments = dict(project_id=inputs["project_id"], job_id=inputs["job_id"],
        attempt_db=path.parent / "runtime/provider_attempts.sqlite3", env_file=tmp_path / "never-read.env",
        account_budget=store, budget_usd="1", transport=httpx.MockTransport(respond))
    first = freeform_image.execute_freeform(path, **arguments)
    second = freeform_image.execute_freeform(path, **arguments)
    assert first == second
    assert len(requests) == 1 and requests[0].url.path == "/v1/images/edits"
    assert first["provider_receipt"]["account_reservation_id"]
    assert store.snapshot(store.policy.account_ref, store.policy.period_id)["reservation_counts"] == {
        "HELD": 0, "SUBMITTED": 1, "SETTLED": 0}
    from beauty_ai.provider_attempts import ProviderAttemptStore
    attempts = ProviderAttemptStore(arguments["attempt_db"])
    assert len(attempts.get_reuse_events(inputs["job_id"], inputs["project_id"])) == 1
    assert (path.parent / "image.png").is_file()
    assert first["actual_cost_usd"] is None and first["review_status"] == "NOT_APPROVED"


def test_live_requires_budget_before_preparation_credentials_or_database(tmp_path):
    with pytest.raises(GatewayError) as error:
        freeform_image.execute_freeform(tmp_path / "missing.json", project_id=uuid4(), job_id=uuid4(),
            attempt_db=tmp_path / "attempts.sqlite3", env_file=tmp_path / "private.env", allow_live=True)
    assert error.value.code == "ACCOUNT_BUDGET_REQUIRED"
    assert not (tmp_path / "attempts.sqlite3").exists()


def test_execution_rejects_foreign_identity_and_tampered_request(inputs, tmp_path):
    path = freeform_image.prepare_freeform(**inputs)
    transport = httpx.MockTransport(lambda _: pytest.fail("Tampering must stop before POST"))
    arguments = dict(project_id=inputs["project_id"], job_id=uuid4(),
        attempt_db=tmp_path / "attempts.sqlite3", budget_usd="1", transport=transport)
    with pytest.raises(ValueError, match="FREEFORM_PREPARATION_MISMATCH"):
        freeform_image.execute_freeform(path, **arguments)
    saved = json.loads(path.read_text())
    saved["request"]["prompt"]["positive"] = "changed"
    path.write_text(json.dumps(saved))
    arguments["job_id"] = inputs["job_id"]
    with pytest.raises(ValueError, match="FREEFORM_PREPARATION_MISMATCH"):
        freeform_image.execute_freeform(path, **arguments)
    assert not arguments["attempt_db"].exists()


@pytest.mark.parametrize("invalid", ["large", "symlink", "blank"])
def test_prompt_file_is_bounded_and_not_followed(inputs, tmp_path, invalid):
    if invalid == "large":
        inputs["prompt_file"].write_bytes(b"a" * 16001)
    elif invalid == "symlink":
        link = tmp_path / "linked.txt"
        link.symlink_to(inputs["prompt_file"])
        inputs["prompt_file"] = link
    else:
        inputs["prompt_file"].write_text("  ")
    with pytest.raises(ValueError):
        freeform_image.prepare_freeform(**inputs)
    assert not inputs["output"].exists()


def test_cli_default_is_offline_preview_and_execute_missing_config_is_safe(inputs, capsys):
    args = ["--prompt-file", str(inputs["prompt_file"]), "--product", str(inputs["products"][0]),
            "--output", str(inputs["output"])]
    assert freeform_image.main(args + ["--execute", "--env-file", "private-fixture.env"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error_code"] == "ACCOUNT_BUDGET_REQUIRED"
    assert not inputs["output"].exists()
    assert freeform_image.main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["image_api_calls"] == 0
    assert UUID(report["project_id"]) and UUID(report["job_id"])


def test_module_command_generates_preview_without_execution(inputs):
    command = [sys.executable, "-m", "beauty_ai.freeform_image", "--prompt-file", str(inputs["prompt_file"]),
               "--product", str(inputs["products"][0]), "--output", str(inputs["output"])]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                            cwd=Path(__file__).resolve().parents[1], check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["image_api_calls"] == 0
    assert not (inputs["output"] / "runtime/provider_attempts.sqlite3").exists()
