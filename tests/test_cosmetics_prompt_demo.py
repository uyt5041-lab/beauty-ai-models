import json
from pathlib import Path
import socket

import pytest

from beauty_ai.contracts import ExternalImageRequest, content_hash
from beauty_ai.cosmetics_prompt_demo import main
from beauty_ai.cosmetics_prompts import render_cosmetics_provider_prompt


def test_preview_exports_reproducible_templates_without_network_or_credentials(tmp_path, monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Prompt preview attempted network access")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-must-never-be-read-or-exported")
    monkeypatch.setenv("BEAUTY_OPENAI_IMAGE_LIVE_CALLS", "true")
    reports = []
    for _ in range(2):
        main(["--output", str(tmp_path)])
        summary = json.loads(capsys.readouterr().out)
        assert summary["actual_provider_calls"] == summary["generated_images"] == 0
        assert summary["mode"] == "PROMPT_PREVIEW_ONLY"
        report_path = Path(summary["report_path"])
        report = json.loads(report_path.read_text())
        assert report["requires_uploaded_assets"]
        assert report["quality_approval"] == "NOT_ASSESSED"
        assert len(report["presets"]) == 3
        for item in report["presets"]:
            folder = report_path.parent / item["directory"]
            request = ExternalImageRequest.model_validate_json((folder / "request-template.json").read_text())
            assert request.operation == "edit" and request.pipeline_revision == "2"
            assert content_hash(request) == item["template_request_hash"]
            assert (folder / "provider-prompt.txt").read_text().rstrip("\n") == render_cosmetics_provider_prompt(request)
            assert item["human_review"] == "PENDING"
        cutout = ExternalImageRequest.model_validate_json(
            (report_path.parent / "cutout-request-template.json").read_text())
        assert cutout.operation == "cutout" and cutout.prompt is None
        for path in report_path.parent.rglob("*"):
            if path.is_file():
                assert "test-key-must-never-be-read-or-exported" not in path.read_text()
        reports.append(report)
    assert reports[0]["presets"] == reports[1]["presets"]
    assert reports[0]["report_path"] != reports[1]["report_path"]


def test_bad_brief_is_rejected_without_leaking_input_or_creating_output(tmp_path, capsys):
    brief = tmp_path / "bad.json"
    brief.write_text(json.dumps({"product_description": "private-test-description", "source_revision": False}))
    destination = tmp_path / "output"
    with pytest.raises(SystemExit):
        main(["--brief", str(brief), "--output", str(destination)])
    assert "private-test-description" not in capsys.readouterr().err
    assert not destination.exists()
