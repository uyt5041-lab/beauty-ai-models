"""A preset wiring and historical safe-zone replay, fully offline."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageChops, ImageDraw
from pydantic import ValidationError
import pytest

from src.image_generation import CompositionInput, compose_ad_png, make_a_template
from src.image_generation.ad_resize import resize_ad_png
from src.image_generation.composition import CompositionRect
from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError
from . import helpers
from . import test_backend_adapter as backend


def protected():
    return CompositionRect(left=.34, top=.28, right=.70, bottom=.81)


def picture():
    image = Image.new("RGB", (1152, 1440), "#fff3ee")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((430, 490, 730, 1100), radius=18, fill="#d5748c")
    draw.rectangle((450, 420, 710, 490), fill="#f4d7de")
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def compose_body(layout, copy=None):
    data = picture()
    copy = copy or helpers.copy_output()
    return CompositionInput(job_id="a-template-001", image_bytes=data,
        image_sha256=sha256(data).hexdigest(), copy_revision="approved-copy-r2",
        copy_hash=content_hash(copy), copy_output=copy, layout=layout)


def test_preset_pairs_upper_copy_with_existing_safe_zone_contract():
    zone, layout = make_a_template()
    assert zone.model_dump() == dict(coordinate_system="final_canvas_normalized",
                                     left=.06, top=.05, right=.94, bottom=.25)
    for field in ("headline", "subline"):
        region = layout.text_regions[field]
        assert zone.left <= region.left < region.right <= zone.right
        assert zone.top <= region.top < region.bottom <= zone.bottom
    assert layout.text_regions["cta"].top > zone.bottom
    assert all(r.align == "center" and r.background is None
               for r in layout.text_regions.values())
    assert [layout.text_regions[k].min_font_size for k in
            ("headline", "subline", "cta")] == [48, 45, 48]
    # A caller mutation must not poison the next request's preset.
    layout.text_regions["headline"].color = "#ffffff"
    assert make_a_template()[1].text_regions["headline"].color == "#151515"


@pytest.mark.parametrize("bounds", [
    dict(left=.40, top=.12, right=.65, bottom=.80),
    dict(left=.40, top=.20, right=.65, bottom=.80),
    dict(left=.40, top=.30, right=.65, bottom=.90),
])
def test_protection_rejects_headline_subline_and_cta_overlap(bounds):
    with pytest.raises(ValidationError, match="overlap"):
        make_a_template(protected_regions=[CompositionRect(**bounds)])


def test_local_a_composition_preserves_product_region_copy_and_caption():
    _, layout = make_a_template(protected_regions=[protected()])
    value = compose_body(layout)
    result = compose_ad_png(value, font_path=helpers.FONT)
    baseline = resize_ad_png(value.image_bytes)
    with Image.open(BytesIO(result.image_bytes)) as final, Image.open(BytesIO(baseline.image_bytes)) as before:
        assert final.format == "PNG" and final.size == (1080, 1350)
        assert ImageChops.difference(final.crop(protected().pixels()),
                                    before.crop(protected().pixels())).getbbox() is None
    assert result.source_image_sha256 == value.image_sha256
    assert result.copy_hash == value.copy_hash
    assert result.copy_revision == value.copy_revision
    expected = value.copy_output["caption"] + "\n\n" + " ".join(value.copy_output["hashtags"])
    assert result.caption_bytes.decode("utf-8") == expected
    assert result.receipt["provider_calls"] == 0
    assert result.receipt["needs_manual_check"] is True
    assert result.receipt["protected_region_check"] is True
    assert result.receipt["warnings"] == []
    for field, placement in result.receipt["placements"].items():
        region = layout.text_regions[field]
        assert placement["font_size"] >= region.min_font_size
        assert placement["text_sha256"] == sha256(value.copy_output["ad_copy"][field].encode()).hexdigest()
        left, _, right, _ = placement["region"]
        for x1, _, x2, _ in placement["ink_boxes"]:
            assert abs((x1+x2)/2-(left+right)/2) <= 1


def test_missing_protection_still_requires_manual_review():
    result = compose_ad_png(compose_body(make_a_template()[1]), font_path=helpers.FONT)
    assert result.receipt["warnings"] == ["PRODUCT_REGION_NOT_PROVIDED"]
    assert not result.receipt["protected_region_check"]
    assert result.receipt["needs_manual_check"]


def test_long_copy_is_not_cut_or_shrunk_below_preset_minimum():
    copy = helpers.copy_output()
    copy["ad_copy"]["headline"] = "가" * 100
    with pytest.raises(GatewayError) as error:
        compose_ad_png(compose_body(make_a_template()[1], copy), font_path=helpers.FONT)
    assert error.value.code == "COMPOSITION_TEXT_OVERFLOW"


def test_explicit_backing_keeps_protection_validation():
    _, layout = make_a_template(protected_regions=[protected()], background="#FFFFFF")
    assert all(r.background == "#FFFFFF" for r in layout.text_regions.values())
    result = compose_ad_png(compose_body(layout), font_path=helpers.FONT)
    assert result.receipt["protected_region_check"]
    with pytest.raises(ValidationError, match="overlap"):
        make_a_template(protected_regions=[CompositionRect(left=.1, top=.1, right=.9, bottom=.8)],
                        background="#FFFFFF")


@pytest.mark.parametrize("case", ["legacy", "string_revision"])
def test_pre_v3_safe_zone_binding_replays_exact_stored_request(tmp_path, monkeypatch, case):
    # Captured from actual b9d0015 _bind before this implementation, not rebuilt.
    fixture = json.loads((Path(__file__).parent / "fixtures/safe_zone_bindings_b9d0015.json").read_text())
    assert fixture["source_commit"] == "b9d00152de352499ce7773ac398fe8efb5408e8e"
    record = fixture["cases"][case]
    project = uuid4()
    adapter = backend.setup(tmp_path, lambda req: pytest.fail("No provider call is allowed"),
                            model="gpt-image-2.5-sunburst-2026-09-08")
    payload = backend.body(**record["changes"])
    with adapter._connect() as db:
        db.execute("INSERT INTO backend_image_bindings VALUES (?,?,?,?,?)",
                   (str(project), payload.job_id, record["input_hash"],
                    record["request_json"], record["request_hash"]))

    def forbidden(*args, **kwargs):
        pytest.fail("Historical replay must bypass prompt building and asset upload")

    monkeypatch.setattr(adapter, "_build_prompt", forbidden)
    monkeypatch.setattr(adapter.processor.assets, "put_upload", forbidden)
    request = adapter._bind(payload, project)
    assert request.model_dump_json() == record["request_json"]
    assert content_hash(request) == record["request_hash"]
    assert request.prompt.prompt_version in {"backend-v02-ko-v1", "backend-v02-ko-v2"}
    with pytest.raises(GatewayError) as error:
        adapter._bind(backend.body(**dict(record["changes"], caption="changed")), project)
    assert error.value.code == "BACKEND_INPUT_CONFLICT"


@pytest.mark.parametrize("revision", [1, "approved-copy-r2"])
def test_new_safe_zone_policy_is_versioned_and_replays_without_upload(tmp_path, monkeypatch, revision):
    adapter = backend.setup(tmp_path, lambda req: pytest.fail("Binding is local"))
    project = uuid4()
    zone, _ = make_a_template()
    payload = backend.body(copy_revision=revision, safe_zone=zone)
    request = adapter._bind(payload, project)
    assert request.prompt.prompt_version == "backend-v02-ko-v3"
    assert request.prompt.positive.startswith(payload.image_prompt.prompt)
    for word in ("제품·소품", "복잡한 무늬", "강한 반사광", "카피 글자는 직접 생성하지 않는다"):
        assert word in request.prompt.positive

    def forbidden(*args, **kwargs):
        pytest.fail("Same input must reuse the stored new binding")

    monkeypatch.setattr(adapter, "_build_prompt", forbidden)
    monkeypatch.setattr(adapter.processor.assets, "put_upload", forbidden)
    assert adapter._bind(payload, project) == request


def test_oversized_new_prompt_fails_before_asset_or_attempt(tmp_path, monkeypatch):
    adapter = backend.setup(tmp_path, lambda req: pytest.fail("No provider call"))
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid prompt must fail before asset upload or paid reservation")
    monkeypatch.setattr(adapter.processor.assets, "put_upload", forbidden)
    monkeypatch.setattr(adapter.processor, "process_job", forbidden)
    value = backend.body(safe_zone=make_a_template()[0],
        image_prompt=dict(prompt="가"*3000, concept="나"*600, mood=["다"*80]*8))
    with pytest.raises(GatewayError) as error:
        adapter.generate(value, project_id=uuid4())
    assert error.value.code == "BACKEND_BINDING_INVALID"
    with adapter._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM backend_image_bindings").fetchone()[0] == 0


def test_paired_template_generation_replay_to_png_caption_is_offline(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data":[{"b64_json":base64.b64encode(picture()).decode()}]})
    adapter, project = backend.setup(tmp_path, handler), uuid4()
    zone, layout = make_a_template(protected_regions=[protected()])
    copy = helpers.copy_output()
    payload = backend.body(safe_zone=zone, copy_revision="approved-copy-r2",
        copy_hash=content_hash(copy), ad_copy=copy["ad_copy"], caption=copy["caption"],
        hashtags=copy["hashtags"])
    generated = adapter.generate(payload, project_id=project)
    assert adapter.generate(payload, project_id=project) == generated
    # This supplied snapshot stands in for serving approval; no UI is exercised.
    result = compose_ad_png(CompositionInput(job_id=generated.job_id,
        image_bytes=generated.image_bytes, image_sha256=generated.sha256,
        copy_revision=generated.copy_revision, copy_hash=generated.copy_hash,
        copy_output=copy, layout=layout), font_path=helpers.FONT)
    assert len(calls) == 1  # MockTransport once; no actual provider calls.
    assert result.receipt["provider_calls"] == 0
    assert result.receipt["needs_manual_check"]
    assert (result.width, result.height) == (1080, 1350)
    assert result.caption_bytes.decode() == copy["caption"] + "\n\n" + " ".join(copy["hashtags"])


def test_explicit_korean_subline_break_keeps_both_lines_at_minimum_size():
    copy = helpers.copy_output()
    copy["ad_copy"]["subline"] = "매일 편안하게 만나는\n나만의 시간"
    result = compose_ad_png(compose_body(make_a_template()[1], copy), font_path=helpers.FONT)
    placement = result.receipt["placements"]["subline"]
    assert placement["line_count"] == 2
    assert placement["font_size"] >= 45
    assert placement["text_sha256"] == sha256(copy["ad_copy"]["subline"].encode()).hexdigest()
