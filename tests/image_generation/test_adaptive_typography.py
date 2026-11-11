from dataclasses import replace

from PIL import Image, ImageDraw
import pytest

from src.image_generation.adaptive_typography import (
    AdaptiveTypographyOptions, boundary_mask, place_text, resolve_scene_color,
)
from src.image_generation.errors import GatewayError


@pytest.mark.parametrize('clearance', [4, 12, 20])
def test_moves_whole_text_away_from_shelf_edge_with_requested_gap(clearance):
    source = Image.new('RGB', (300, 140), '#efdbc5')
    ImageDraw.Draw(source).rectangle((0, 63, 299, 139), fill='#a79078')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((100, 54, 199, 73), fill=255)
    options = AdaptiveTypographyOptions(clearance_px=clearance)
    moved, colors, receipt = place_text(source, (0,0,300,140), mask, options=options)
    assert receipt['offset_px'] != [0,0]
    assert colors.background_overlay is None
    assert colors.receipt['passed']
    l,t,r,b = moved.getbbox()
    assert b <= 63-clearance or t >= 64+clearance
    assert (r-l,b-t) == (100,20)
    assert 6 <= l < r <= 294 and 6 <= t < b <= 134


def test_median_outlier_is_avoided_even_inside_uniform_product_interior():
    source = Image.new('RGB', (300,150), '#efdbc5')
    ImageDraw.Draw(source).rectangle((120,0,180,90), fill='#101044')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((105,60,195,79), fill=255)
    moved, _, receipt = place_text(source, (0,0,300,150), mask, options=AdaptiveTypographyOptions(clearance_px=12))
    assert moved.getbbox()[1] >= 103
    assert receipt['hazard_pixels_in_text_box'] == 0


def test_smooth_readable_region_keeps_original_position_and_uses_colored_ink():
    source = Image.new('RGB', (200,80), '#f4d5c2')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((60,25,140,45), fill=255)
    moved, color, receipt = place_text(source, (0,0,200,80), mask, options=AdaptiveTypographyOptions())
    assert receipt['offset_px'] == [0,0]
    assert moved.tobytes() == mask.tobytes()
    assert color.receipt['candidate_count'] >= 10
    rgb = tuple(int(color.color[i:i+2],16) for i in (1,3,5))
    assert max(rgb)-min(rgb) > 30


def test_no_safe_location_fails_without_adding_backing_or_mutating_inputs():
    source = Image.new('RGB', (200,80), 'white')
    ImageDraw.Draw(source).rectangle((0,40,199,79), fill='black')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((50,10,149,69), fill=255)
    before = source.tobytes(), mask.tobytes()
    with pytest.raises(GatewayError) as error:
        place_text(source, (0,0,200,80), mask, options=AdaptiveTypographyOptions(clearance_px=20))
    assert error.value.code == 'COMPOSITION_NO_SAFE_POSITION'
    assert before == (source.tobytes(), mask.tobytes())


def test_maximum_motion_is_respected():
    source = Image.new('RGB', (300,140), '#efdbc5')
    ImageDraw.Draw(source).rectangle((0,63,299,139), fill='#a79078')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((100,54,199,73), fill=255)
    with pytest.raises(GatewayError):
        place_text(source, (0,0,300,140), mask, options=AdaptiveTypographyOptions(max_move_px=0))


def test_vertical_default_never_escapes_sideways_when_only_horizontal_space_exists():
    source = Image.new('RGB', (300,100), '#f5dfc8')
    ImageDraw.Draw(source).rectangle((120,0,179,99), fill='#121244')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((130,40,149,59), fill=255)
    options = AdaptiveTypographyOptions(clearance_px=4)
    with pytest.raises(GatewayError):
        place_text(source, (0,0,300,100), mask, options=options)
    _, _, receipt = place_text(source, (0,0,300,100), mask,
                              options=replace(options,movement_axis='both'))
    assert receipt['offset_px'][0] != 0


def test_vertical_placement_preserves_exact_horizontal_ink_bounds():
    source = Image.new('RGB', (300,140), '#efdbc5')
    ImageDraw.Draw(source).rectangle((0,63,299,139), fill='#a79078')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((100,54,199,73), fill=255)
    moved, _, receipt = place_text(source,(0,0,300,140),mask,options=AdaptiveTypographyOptions())
    assert receipt['offset_px'][0] == 0 and receipt['offset_px'][1] != 0
    assert moved.getbbox()[::2] == mask.getbbox()[::2]


def test_explicit_lower_fallback_stays_centered_and_reports_unmet_clearance():
    source = Image.new('RGB', (300,140), '#efdbc5')
    ImageDraw.Draw(source).rectangle((120,0,179,139), fill='#121244')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((130,40,149,59), fill=255)
    moved, colors, receipt = place_text(source,(0,0,300,140),mask,
        options=AdaptiveTypographyOptions(fallback_lower=True,fallback_shift_px=48))
    assert receipt['offset_px'] == [0,48]
    assert receipt['forced_lower_fallback'] is True
    assert receipt['clearance_passed'] is False
    assert receipt['hazard_pixels_in_text_box'] > 0
    assert moved.getbbox()[::2] == mask.getbbox()[::2]
    assert colors.background_overlay is None


def test_lower_fallback_is_clamped_inside_allowed_region_without_clipping():
    source = Image.new('RGB', (300,100), '#efdbc5')
    ImageDraw.Draw(source).rectangle((120,0,179,99), fill='#121244')
    mask = Image.new('L', source.size)
    ImageDraw.Draw(mask).rectangle((130,40,149,59), fill=255)
    moved, _, receipt = place_text(source,(0,0,300,100),mask,
        options=AdaptiveTypographyOptions(fallback_lower=True,fallback_shift_px=100,inset_px=6))
    assert moved.getbbox() == (130,74,150,94)
    assert receipt['offset_px'] == [0,34]


@pytest.mark.parametrize('field,value', [('clearance_px',-1), ('clearance_px',True), ('step_px',0), ('max_move_px',10000)])
def test_invalid_options_rejected(field,value):
    with pytest.raises(ValueError):
        replace(AdaptiveTypographyOptions(), **{field:value})


def test_renderer_opt_in_records_positions_and_never_adds_rectangle():
    from . import test_a_template as a
    from src.image_generation import compose_ad_png, make_a_template
    body = a.compose_body(make_a_template()[1])
    result = compose_ad_png(body, font_path=a.helpers.FONT, adaptive=AdaptiveTypographyOptions())
    assert result.receipt['provider_calls'] == 0
    assert result.receipt['text_contrast_passed']
    assert result.receipt['background_overlays_allowed'] is False
    for field in result.receipt['placements'].values():
        assert field['position_check']['hazard_pixels_in_text_box'] == 0
        assert field['color_check']['background_overlay_rgba'] is None


def test_renderer_rejects_explicit_rectangle_in_adaptive_mode():
    from . import test_a_template as a
    from src.image_generation import compose_ad_png, make_a_template
    body = a.compose_body(make_a_template(background='#ffffff')[1])
    with pytest.raises(GatewayError) as error:
        compose_ad_png(body, font_path=a.helpers.FONT, adaptive=AdaptiveTypographyOptions())
    assert error.value.code == 'COMPOSITION_ADAPTIVE_BACKGROUND_FORBIDDEN'
