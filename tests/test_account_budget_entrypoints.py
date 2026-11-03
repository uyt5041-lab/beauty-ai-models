"""Live entrypoints reject missing account config before key reads or HTTP."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from beauty_ai import experiment_execution, experiment_runner, inference_runtime
from beauty_ai.errors import GatewayError
from beauty_ai.openai_images import OpenAIImagesConfig


def test_build_app_requires_budget_before_creating_runtime(tmp_path):
    root = tmp_path / "runtime"
    with pytest.raises(GatewayError) as error:
        inference_runtime.build_app(root, uuid4(), "fixture-token", openai_config=OpenAIImagesConfig(live_calls=True))
    assert error.value.code == "ACCOUNT_BUDGET_REQUIRED"
    assert not root.exists()


def test_execute_plan_requires_budget_before_key_and_plan_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment_execution, "_load_settings", lambda *_: pytest.fail("Key read before account gate"))
    with pytest.raises(GatewayError) as error:
        experiment_execution.execute_plan(tmp_path / "missing.json", expected_sha256="a" * 64,
            allow_live=True, env_file=tmp_path / "private.env")
    assert error.value.code == "ACCOUNT_BUDGET_REQUIRED"


@pytest.mark.parametrize("config", [None, "missing-policy.json"])
def test_runtime_env_loads_budget_before_reading_api_key(config, tmp_path, monkeypatch, capsys):
    class Environment(dict):
        def get(self, key, default=None):
            if key == "OPENAI_API_KEY":
                pytest.fail("Runtime read key before validating account policy")
            return super().get(key, default)
    environment = Environment(BEAUTY_PROJECT_ID=str(uuid4()), INFERENCE_INTERNAL_TOKEN="t" * 32,
                              BEAUTY_OPENAI_IMAGE_LIVE_CALLS="true")
    if config:
        environment["BEAUTY_ACCOUNT_BUDGET_CONFIG"] = str(tmp_path / config)
    monkeypatch.setattr(inference_runtime.os, "environ", environment)
    monkeypatch.setattr(sys, "argv", ["inference-runtime"])
    with pytest.raises(SystemExit) as error:
        inference_runtime.main()
    assert error.value.code == 2
    assert "ACCOUNT_BUDGET_" + ("CONFIG_INVALID" if config else "REQUIRED") in capsys.readouterr().err


def test_experiment_cli_loads_config_before_live_execution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(experiment_execution, "execute_plan", lambda *_args, **_kwargs: pytest.fail("Invalid policy executed"))
    status = experiment_runner.main(["execute", "--plan", str(tmp_path / "missing-plan.json"),
        "--expect-sha256", "a" * 64, "--env-file", str(tmp_path / "private.env"), "--budget-usd", "1",
        "--account-budget-config", str(tmp_path / "missing-policy.json"), "--execute"])
    assert status == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "ACCOUNT_BUDGET_CONFIG_INVALID"


def test_historical_helper_blocks_missing_policy_before_settings(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "scripts/compare_cosmetics_presets.py"
    spec = importlib.util.spec_from_file_location("historical_presets_budget_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "settings_from_file", lambda *_: pytest.fail("Historical helper read credentials"))
    with pytest.raises(GatewayError) as error:
        module.run(argparse.Namespace(execute=True))
    assert error.value.code == "ACCOUNT_BUDGET_REQUIRED"
