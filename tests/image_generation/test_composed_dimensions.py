"""Test the final provider payload, layout conflicts and paid-job replay offline."""
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest

from src.image_generation.a_template import make_a_template
from src.image_generation.composed_dimensions import (
    DIMENSION_KEYS, DIMENSIONS14_VERSION, a_layout_prompt_data,
)
from src.image_generation.contracts import ExternalImageRequest, ImagePrompt
from src.image_generation.errors import GatewayError
from src.image_generation.korean_image_policy import PRESERVE_KO, WATERMARK_POLICY_KO
from src.image_generation.openai_images import OpenAIImagesAdapter
from . import test_backend_adapter as b


def fields(request):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + request.headers["content-type"].encode()
        + b"\r\n\r\n" + request.read()
    )
    return {part.get_param("name", header="content-disposition"):
            part.get_payload(decode=True)
            for part in message.iter_parts() if part.get_filename() is None}


@pytest.mark.parametrize("safe_zone", [None, make_a_template()[0]])
@pytest.mark.parametrize("scene", [
    "맑은 민트빛 유리 배경. 오른쪽 위 주광원. 그림자 없음. 소품 없음.",
    "밝은 크림빛 석재 선반. 왼쪽 위 넓은 낮빛과 낮은 석재 받침 한 개.",
])
def test_opt_in_transmits_all_dimensions_once_and_keeps_input(tmp_path, safe_zone, scene):
    captured = []

    def handler(request):
        captured.append(fields(request))
        return b.response()

    adapter = b.setup(tmp_path, handler, scene_mode="dimensions14")
    body = b.body(image_prompt={"prompt": scene, "mood": ["청량"]}, safe_zone=safe_zone)
    result = adapter.generate(body, project_id=uuid4())
    assert len(captured) == 1
    sent = captured[0]["prompt"].decode()
    assert sent.startswith(scene)
    for name in DIMENSION_KEYS:
        assert sent.count(f"[dimension:{name}]") == 1
    assert "전달받은 광고 무드: 청량" in sent
    assert "unverified-ocr-not-for-provider" not in sent
    assert "오늘의 루틴" not in sent  # Ad copy belongs to the compositor.
    assert PRESERVE_KO not in sent
    assert "제품 정물의 조건부 조명·반사 보완" not in sent
    assert sent.count(WATERMARK_POLICY_KO) == 1
    # A directions are authoritative; no second placement profile is appended.
    assert "UI_SAFE" not in sent and "PRODUCT_TARGET" not in sent
    assert captured[0]["model"] == b"gpt-image-2.6-sunburst"
    assert captured[0]["complexity"] == b"high"
    assert captured[0]["quality"] == b"medium"
    assert (result.width, result.height) == (1152, 1440)
    assert result.receipt["scene_policy_version"] == DIMENSIONS14_VERSION
    assert result.receipt["scene_mode"] == "dimensions14"


def test_default_mode_keeps_existing_policy_and_receipt(tmp_path):
    captured = []

    def handler(request):
        captured.append(fields(request)["prompt"].decode())
        return b.response()

    result = b.setup(tmp_path, handler).generate(b.body(), project_id=uuid4())
    assert PRESERVE_KO in captured[0]
    assert "[dimension:" not in captured[0]
    assert "scene_mode" not in result.receipt
    assert "scene_policy_version" not in result.receipt


def test_coherent_lighting_fallback_and_product_specific_materials(tmp_path):
    adapter = b.setup(tmp_path, lambda _: pytest.fail("Binding only"), scene_mode="dimensions14")
    request = adapter._bind(b.body(), uuid4())
    sent = OpenAIImagesAdapter._prompt(request)
    # Explicit direction/no-shadow must outrank fallbacks. No second fixed rig.
    assert "방향이 없을 때만 왼쪽 위" in sent
    assert "바닥 그림자는 그 반대 방향" in sent
    assert "그림자 없음이 명시되면 그림자를 추가하지 않는다" in sent
    assert "원본 촬영의 색온도·노출을 고정한다는 뜻이 아니다" in sent
    assert "원본대로 유지하되 하이라이트의 위치는 새 주광원에 맞춘다" in sent
    assert "촬영자·휴대폰·카메라·삼각대" in sent
    assert "원본 사진을 기준으로 부품별" in sent
    assert "아벤느" not in sent and "흰 스포이드" not in sent
    assert "소품만 사용하고 없으면 추가하지 않는다" in sent
    assert "인물과 손은 추가하지 않는다" in sent


def test_generation_boxes_match_real_a_compositor():
    _, layout = make_a_template()
    data = a_layout_prompt_data()
    assert data["final_canvas"] == [1080, 1350]
    assert data["copy_regions"] == {
        name: [region.left, region.top, region.right, region.bottom]
        for name, region in layout.text_regions.items()
    }
    assert data["copy_regions"]["cta"] == [.08, .85, .92, .94]


@pytest.mark.parametrize("change", [
    {"layout_selection": {"aspect_ratio": "4:5", "placement": "feed"}},
    {"layout_selection": {"aspect_ratio": "3:4", "placement": "feed"}},
    {"layout_selection": {"aspect_ratio": "9:16", "placement": "reels"}},
    {"layout_selection": {"aspect_ratio": "9:16", "placement": "story"}},
    {"safe_zone": {"left": .1, "top": .3, "right": .9, "bottom": .8}},
])
def test_competing_layout_fails_before_assets_reservation_or_provider(tmp_path, monkeypatch, change):
    adapter = b.setup(tmp_path, lambda _: pytest.fail("No provider call"), scene_mode="dimensions14")

    def forbidden(*args, **kwargs):
        pytest.fail("Conflicting layout must stop before assets or a paid attempt")

    monkeypatch.setattr(adapter.processor.assets, "put_upload", forbidden)
    monkeypatch.setattr(adapter.processor, "process_job", forbidden)
    with pytest.raises(GatewayError) as error:
        adapter.generate(b.body(**change), project_id=uuid4())
    assert error.value.code == "DIMENSIONS_LAYOUT_CONFLICT"
    with adapter._connect() as db:
        assert db.execute("SELECT count(*) FROM backend_image_bindings").fetchone()[0] == 0


@pytest.mark.parametrize("before,after", [("standard", "dimensions14"), ("dimensions14", "standard")])
def test_changing_mode_cannot_reuse_candidate_or_charge_again(tmp_path, before, after):
    calls = []

    def handler(request):
        calls.append(1)
        return b.response()

    project = uuid4()
    b.setup(tmp_path, handler, scene_mode=before).generate(b.body(), project_id=project)
    with pytest.raises(GatewayError) as error:
        b.setup(tmp_path, handler, scene_mode=after).generate(b.body(), project_id=project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT"
    assert calls == [1]


def test_restart_replays_versioned_request_without_rebuilding_or_rebilling(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(fields(request)["prompt"])
        return b.response()

    project = uuid4()
    first = b.setup(tmp_path, handler, scene_mode="dimensions14")
    original = first.generate(b.body(), project_id=project)
    bound = first._bind(b.body(), project)
    restarted = b.setup(tmp_path, handler, scene_mode="dimensions14")

    def forbidden(*args, **kwargs):
        pytest.fail("Saved binding must replay without rebuilding")

    monkeypatch.setattr(restarted, "_build_prompt", forbidden)
    monkeypatch.setattr(restarted.processor.assets, "put_upload", forbidden)
    assert restarted._bind(b.body(), project).model_dump_json() == bound.model_dump_json()
    replay = restarted.generate(b.body(), project_id=project)
    assert replay.image_bytes == original.image_bytes
    assert len(calls) == 1


def test_mode_is_module_configuration_not_a_text_wire_field():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        b.body(scene_mode="dimensions14")


@pytest.mark.parametrize("invalid", [None, "dimensions13", True, 14])
def test_unknown_mode_rejected_before_bindings(tmp_path, invalid):
    with pytest.raises(ValueError, match="scene_mode"):
        b.setup(tmp_path, None, scene_mode=invalid)
    assert not (tmp_path / "bindings.sqlite3").exists()


def test_full_3000_character_input_is_not_truncated_to_make_room_for_fixed_rules(tmp_path):
    text = "가" * 2999 + "끝"
    adapter = b.setup(tmp_path, lambda _: pytest.fail("Binding only"), scene_mode="dimensions14")
    request = adapter._bind(b.body(image_prompt=text), uuid4())
    sent = OpenAIImagesAdapter._prompt(request)
    assert sent.startswith(text + "\n\n")
    assert sent.count("[dimension:") == 14


def test_freeform_and_cutout_keep_the_original_policy(tmp_path):
    adapter = b.setup(tmp_path, None)
    reference = adapter.processor.assets.put_upload(b.png(), project_id=uuid4())
    cutout = ExternalImageRequest(operation="cutout", product_images=[reference])
    freeform = ExternalImageRequest(operation="generate", prompt=ImagePrompt(
        positive="자유 배치", prompt_version="freeform-v1", source_revision=1))
    for request in (cutout, freeform):
        sent = OpenAIImagesAdapter._prompt(request)
        assert PRESERVE_KO in sent and "[dimension:" not in sent
