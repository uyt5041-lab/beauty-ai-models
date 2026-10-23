"""Offline safety checks for the explicitly authorized three-preset runner."""
from argparse import Namespace
import base64
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import httpx
from PIL import Image
import pytest


SPEC = importlib.util.spec_from_file_location(
    "cosmetics_comparison_runner", Path(__file__).parents[1] / "scripts/compare_cosmetics_presets.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_execution_requires_explicit_flag_before_reading_files(tmp_path):
    with pytest.raises(RuntimeError, match="LIVE_EXECUTION_NOT_AUTHORIZED"):
        runner.run(Namespace(execute=False))
    assert list(tmp_path.iterdir()) == []


def test_settings_read_only_selected_names_and_never_echo(tmp_path, capsys):
    settings = tmp_path / ".env"
    settings.write_text("OPENAI_API_KEY='secret-test'\nLANGFUSE_SECRET_KEY='secret-lf'\n"
                        "export LANGFUSE_BASE_URL=https://example.test\nUNRELATED_KEY=private\n")
    assert runner.settings_from_file(settings) == {
        "OPENAI_API_KEY": "secret-test", "LANGFUSE_SECRET_KEY": "secret-lf",
        "LANGFUSE_BASE_URL": "https://example.test"}
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status,action", [(200, "get"), (200, "create"), (201, "create")])
def test_cli_success_and_credential_scope(monkeypatch, status, action):
    seen = {}
    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": status, "body": {"version": 1}}))
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", "shell-secret")
    result = runner.langfuse_cli(["prompts", action, "x"], {
        "OPENAI_API_KEY": "file-secret", "INFERENCE_INTERNAL_TOKEN": "internal-secret",
        "LANGFUSE_SECRET_KEY": "lf-secret", "LANGFUSE_BASE_URL": "https://example.test"})
    assert result == {"version": 1}
    assert "OPENAI_API_KEY" not in seen["env"]
    assert "INFERENCE_INTERNAL_TOKEN" not in seen["env"]
    assert seen["env"]["LANGFUSE_HOST"] == "https://example.test"
    assert seen["capture_output"] is True


@pytest.mark.parametrize("response", ["private-secret", '{"status":401,"body":{"secret":"private-secret"}}'])
def test_cli_error_sanitizes_output(monkeypatch, response):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=response))
    with pytest.raises(RuntimeError) as caught:
        runner.langfuse_cli(["prompts", "get", "x"], {})
    assert str(caught.value) == "LANGFUSE_CLI_FAILED"


def test_cli_timeout_sanitizes_exception(monkeypatch):
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("secret-command", 60, output="private-secret")
    monkeypatch.setattr(runner.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="^LANGFUSE_CLI_FAILED$"):
        runner.langfuse_cli(["prompts", "get", "x"], {})


def test_main_fails_when_report_is_incomplete(monkeypatch, tmp_path):
    (tmp_path / "report.json").write_text('{"status":"RUNNER_ERROR"}')
    monkeypatch.setattr(runner, "run", lambda args: tmp_path)
    monkeypatch.setattr(sys, "argv", ["compare", "--execute", "--source", "unused", "--env-file", "unused"])
    with pytest.raises(SystemExit) as caught:
        runner.main()
    assert caught.value.code == 1


@pytest.mark.parametrize("trace_failure", ["begin", "finish"])
def test_trace_failure_preserves_three_images_without_duplicate_posts(monkeypatch, tmp_path, trace_failure):
    from beauty_ai.contracts import ExternalImageRequest
    from beauty_ai.cosmetics_prompts import render_cosmetics_provider_prompt

    source = tmp_path / "source.png"
    Image.new("RGB", (80, 100), "white").save(source)
    output = BytesIO()
    Image.new("RGB", (1152, 1440), "white").save(output, format="PNG")
    output_bytes = output.getvalue()
    settings = tmp_path / ".env"
    settings.write_text("OPENAI_API_KEY=secret-openai\nINFERENCE_INTERNAL_TOKEN=secret-internal-0123456789-0123456789\n"
                        "LANGFUSE_PUBLIC_KEY=pk-test\nLANGFUSE_SECRET_KEY=secret-lf\n"
                        "LANGFUSE_BASE_URL=https://example.test\n")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    posts = []
    trace_starts = []
    def http_response(transport, request):
        assert request.url.host == "api.openai.com"
        posts.append(request)
        return httpx.Response(200, request=request, headers={"x-request-id": "req_offline_test"}, json={
            "data": [{"b64_json": base64.b64encode(output_bytes).decode()}],
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15,
                      "input_tokens_details": {"text_tokens": 5, "image_tokens": 7}}})
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", http_response)
    # Stub prompt management only; retain the real app, ledger, adapter, and PNG checks.
    module = ModuleType("beauty_ai.langfuse_prompts")
    module.cosmetics_prompt_definition = lambda preset: {"name": "beautyai/cosmetics/" + preset.replace("_", "-")}
    module.cosmetics_prompt_variables = lambda brief, request: {}
    def pin(request, remote, variables):
        text = render_cosmetics_provider_prompt(request)
        return ExternalImageRequest.model_validate({**request.model_dump(), "managed_prompt": {
            "name": remote["name"], "version": 1, "rendered_text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest()}})
    module.pin_cosmetics_prompt = pin
    class Trace:
        def finish(self, **kwargs):
            raise RuntimeError("secret-tracing-failure")
    def begin(request, **kwargs):
        trace_starts.append(len(posts))
        if trace_failure == "begin":
            raise RuntimeError("secret-tracing-failure")
        return Trace()
    module.begin_image_generation = begin
    monkeypatch.setitem(sys.modules, "beauty_ai.langfuse_prompts", module)
    monkeypatch.setattr(runner, "langfuse_cli", lambda args, settings: {"name": args[2], "version": 1})
    run_dir = runner.run(Namespace(execute=True, source=source, env_file=settings,
                                  output=tmp_path / "runs", publish_prompts=False,
                                  product_description="Fictional serum bottle"))
    report = json.loads((run_dir / "report.json").read_text())
    assert report["status"] == "SUCCEEDED"
    assert report["http_post_attempts"] == len(posts) == 3
    assert trace_starts == [0, 1, 2]
    assert len({entry["job_id"] for entry in report["operations"]}) == 3
    assert all(Path(entry["image_path"]).read_bytes() == output_bytes for entry in report["operations"])
    assert all(entry["langfuse"]["status"] in {"BEGIN_FAILED", "FINISH_FAILED"} for entry in report["operations"])
    assert "secret-" not in (run_dir / "report.json").read_text()
    assert httpx.HTTPTransport.handle_request is http_response
