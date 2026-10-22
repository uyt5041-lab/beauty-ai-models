import hashlib
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from beauty_ai.openai_images_demo import main


def test_demo_cli_uses_only_mock_transport_and_marks_every_artifact(tmp_path, monkeypatch, capsys):
    def network_forbidden(*_args, **_kwargs):
        pytest.fail("The offline demo attempted real HTTP transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", network_forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-must-not-be-used")
    monkeypatch.setenv("BEAUTY_OPENAI_IMAGE_LIVE_CALLS", "true")
    main(["--output", str(tmp_path)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["actual_provider_calls"] == 0
    assert summary["mock_provider_calls"] == 3
    assert summary["mode"] == "MOCK_ONLY"
    assert summary["quality_approval"] == "NOT_ASSESSED"
    report = json.loads(Path(summary["report_path"]).read_text())
    assert [entry["operation"] for entry in report["operations"]] == ["generate", "edit", "cutout"]
    for entry in report["operations"]:
        assert entry["synthetic_fixture"] and entry["usage_is_synthetic"]
        assert entry["quality_approval"] == "NOT_ASSESSED"
        assert entry["job"]["status"] == "succeeded"
        assert entry["provider_receipt"]["state"] == "succeeded"
        assert entry["provider_receipt"]["actual_cost_usd"] is None
        assert entry["provider_receipt"]["provider_request_id"].startswith("mock-")
        assert entry["job"]["result"]["usage"]["total_tokens"] == 40
        image_path = Path(entry["image_path"])
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == entry["job"]["result"]["image"]["sha256"]
        with Image.open(image_path) as image:
            assert image.size == (1024, 1024)
            assert image.getpixel((25, 25)) == (255, 255, 255, 255)
            # The shared top banner has visible foreground text, not only metadata.
            with image.crop((24, 24, 1000, 145)) as banner:
                assert len(banner.getcolors(maxcolors=200_000)) > 1
            if entry["operation"] == "cutout":
                assert image.getpixel((0, 0))[3] == 0
                assert "GENERATIVE_CUTOUT_REQUIRES_REVIEW" in entry["job"]["result"]["warnings"]
    serialized = json.dumps(report)
    assert "not-a-real-key" not in serialized and "offline-demo-only-token" not in serialized


def test_demo_has_no_live_switch(capsys):
    with pytest.raises(SystemExit) as error:
        main(["--live"])
    assert error.value.code == 2
    assert "unrecognized arguments: --live" in capsys.readouterr().err
