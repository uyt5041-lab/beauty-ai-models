import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.parametrize("body", [
    '{"positive":"private-prompt-body"',
    json.dumps({"positive": "private-prompt-body", "prompt_version": "", "source_revision": 1}),
    "private-prompt-body" * 3000,
])
def test_cli_rejects_invalid_prompt_before_network_without_echo(tmp_path, body):
    prompt = tmp_path / "private-prompt.json"
    prompt.write_text(body)
    env = os.environ.copy()
    env.pop("INFERENCE_INTERNAL_TOKEN", None)
    done = subprocess.run([sys.executable, "-m", "examples.run_image", "render", str(uuid4()),
        "--job-id", str(uuid4()), "--background-profile", "sdxl-inpaint", "--prompt", str(prompt)],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, timeout=10)
    assert done.returncode == 2
    assert done.stdout == ""
    assert json.loads(done.stderr) == {"error_code": "IMAGE_CLI_INPUT_OR_REQUEST_FAILED"}
    assert "private-prompt" not in done.stderr


def test_sdxl_cli_requires_explicit_prompt_before_any_submission():
    env = os.environ.copy()
    env.pop("INFERENCE_INTERNAL_TOKEN", None)
    done = subprocess.run([sys.executable, "-m", "examples.run_image", "render", str(uuid4()),
        "--job-id", str(uuid4()), "--background-profile", "sdxl-inpaint"],
        cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, timeout=10)
    assert done.returncode == 2
    assert done.stdout == ""
    assert json.loads(done.stderr)["error_code"] == "IMAGE_CLI_INPUT_OR_REQUEST_FAILED"
