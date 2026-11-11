"""Real package boundary: one mocked generation, then local-only composition."""
from uuid import uuid4

import pytest

from src.image_generation import (CompositionInput, build_ittem_image_adapter,
    compose_ittem_ad, make_a_template)
from src.image_generation.contracts import content_hash
from src.image_generation.errors import GatewayError
from . import test_backend_adapter as b
from . import test_a_template as a


def test_combined_generation_and_local_export_keep_approved_copy_identity(tmp_path):
    calls = []
    def handler(request):
        calls.append(1)
        return b.response()
    supplied = b.setup(tmp_path, handler)
    adapter = build_ittem_image_adapter(supplied.processor, tmp_path/'hotfix.sqlite')
    copy = a.helpers.copy_output()
    body = b.body(copy_output=copy, copy_hash=content_hash(copy), ad_copy=copy['ad_copy'],
                  caption=copy['caption'], hashtags=copy['hashtags'])
    candidate = adapter.generate(body, project_id=uuid4())
    assert candidate.receipt['scene_mode'] == 'dimensions14'
    assert candidate.receipt['model'] == 'gpt-image-2.6-sunburst'
    assert candidate.receipt['complexity'] == 'high'
    result = compose_ittem_ad(CompositionInput(job_id=body.job_id, image_bytes=candidate.image_bytes,
        image_sha256=candidate.sha256, copy_revision=body.copy_revision, copy_hash=body.copy_hash,
        copy_output=copy, layout=make_a_template()[1]), font_path=a.helpers.FONT)
    assert calls == [1] and result.receipt['provider_calls'] == 0
    assert result.copy_hash == body.copy_hash
    assert result.receipt['horizontal_center_locked']
    for field in result.receipt['placements'].values():
        assert field['position_check']['offset_px'][0] == 0
        assert field['color_check']['background_overlay_rgba'] is None


def test_profile_retries_locally_then_records_explicit_lower_fallback(monkeypatch):
    from src.image_generation import ittem_hotfix as m
    options = []
    def render(body, *, font_path, adaptive):
        options.append(adaptive)
        if len(options) == 1:
            raise GatewayError('COMPOSITION_NO_SAFE_POSITION', 'No position')
        return type('LocalResult', (), {'receipt': {'text_contrast_passed':False}})()
    monkeypatch.setattr(m,'compose_ad_png',render)
    result = m.compose_ittem_ad(a.compose_body(make_a_template()[1]),font_path=a.helpers.FONT)
    assert [v.clearance_px for v in options] == [12,4]
    assert all(v.movement_axis == 'vertical' for v in options)
    assert options[-1].fallback_lower and options[-1].fallback_shift_px == 48
    assert result.receipt['text_contrast_passed'] is False


def test_generation_mode_conflict_stops_before_upload_or_provider(tmp_path,monkeypatch):
    supplied = b.setup(tmp_path,lambda _:pytest.fail('No call'))
    adapter = build_ittem_image_adapter(supplied.processor,tmp_path/'hotfix.sqlite')
    monkeypatch.setattr(adapter.processor.assets,'put_upload',lambda *a,**k:pytest.fail('No upload'))
    body = b.body(generation_mode='freeform',copy_output=a.helpers.copy_output())
    with pytest.raises(GatewayError) as error:
        adapter._bind(body,uuid4())
    assert error.value.code == 'DIMENSIONS_LAYOUT_CONFLICT'


def test_layout_and_bad_options_are_not_repaired_silently():
    zone,layout=make_a_template();body=a.compose_body(layout)
    with pytest.raises(ValueError):
        compose_ittem_ad(body,font_path=a.helpers.FONT,relaxed_clearance_px=13)
    other = make_a_template()[1]
    other.text_regions['cta'].align='left'
    with pytest.raises(GatewayError) as error:
        compose_ittem_ad(body.model_copy(update={'layout':other}),font_path=a.helpers.FONT)
    assert error.value.code == 'COMPOSITION_HOTFIX_LAYOUT_INVALID'
