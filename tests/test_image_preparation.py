from dataclasses import FrozenInstanceError

from PIL import Image
import pytest

from beauty_ai.contracts import Rect, VisualBrief
from beauty_ai.image_integrity import composite_approved_foreground, to_diffusers_inpaint_mask
from beauty_ai.image_preparation import inspect_composite, prepare_product_canvas


def visual(**changes):
    values = dict(
        concept="studio", style_preset_id="test", style_preset_version="1",
        layout_template_id="split", layout_template_version="1",
        generation_width=256, generation_height=256,
        product_box=Rect(x=.5, y=.125, width=.375, height=.75),
        text_safe_zone=Rect(x=0, y=0, width=.375, height=1),
    )
    values.update(changes)
    return VisualBrief(**values)


def test_alpha_crop_and_aspect_fit_leave_original_unchanged():
    product = Image.new("RGBA", (80, 80), (255, 0, 255, 0))
    product.paste((20, 90, 140, 255), (20, 10, 60, 70))
    before = product.tobytes()
    result = prepare_product_canvas(product, visual())
    assert result.source_bounds == (20, 10, 60, 70)
    # A 40x60 product fitted to a 96x192 box becomes 96x144, centered vertically.
    assert result.product_bounds == (128, 56, 224, 200)
    assert result.product_rgba.size == result.preserve_mask.size == (256, 256)
    assert result.product_rgba.mode == "RGBA"
    assert result.preserve_mask.mode == "L"
    assert result.preserve_mask.histogram()[0] > 0
    assert sum(result.preserve_mask.histogram()[1:255]) == 0
    assert result.preserve_mask.getbbox() == result.product_bounds
    assert product.tobytes() == before
    assert result.needs_approval is True
    with pytest.raises(FrozenInstanceError):
        result.needs_approval = False


def test_tall_product_is_centered_and_its_ratio_is_not_stretched():
    result = prepare_product_canvas(Image.new("RGBA", (10, 40), (20, 90, 140, 255)), visual())
    # 48x192 fits within the 96x192 box.
    assert result.product_bounds == (152, 32, 200, 224)


def test_inside_rounding_keeps_alpha_inside_fractional_product_box():
    brief = visual(product_box=Rect(x=.503, y=.123, width=.37, height=.7))
    result = prepare_product_canvas(Image.new("RGBA", (20, 20), (1, 2, 3, 255)), brief)
    left, top, right, bottom = result.product_bounds
    assert left >= 129 and right <= 223
    assert top >= 32 and bottom <= 210
    assert result.product_rgba.getchannel("A").crop((0, 0, 96, 256)).getbbox() is None


def test_shared_boundary_is_half_open_and_mutated_geometry_is_revalidated():
    brief = visual(
        product_box=Rect(x=.5, y=0, width=.5, height=1),
        text_safe_zone=Rect(x=0, y=0, width=.5000000000000000, height=1),
    )
    # Exact half-canvas boundary is safe, because rectangles are half-open.
    safe = prepare_product_canvas(Image.new("RGBA", (128, 256), (1, 2, 3, 255)), brief)
    assert safe.product_bounds == (128, 0, 256, 256)
    # Model constructors may bypass the shape validator; revalidation must catch it.
    brief.text_safe_zone = Rect(x=0, y=0, width=.501, height=1)
    with pytest.raises(ValueError, match="overlap"):
        prepare_product_canvas(Image.new("RGBA", (128, 256), (1, 2, 3, 255)), brief)


def test_semitransparent_product_is_not_alpha_multiplied_and_mask_is_binary():
    result = prepare_product_canvas(Image.new("RGBA", (2, 2), (20, 40, 60, 128)), visual())
    x, y, _, _ = result.product_bounds
    assert result.product_rgba.getpixel((x, y))[3] == 128
    assert result.preserve_mask.getpixel((x, y)) == 255
    assert to_diffusers_inpaint_mask(result.preserve_mask).getpixel((x, y)) == 0
    assert to_diffusers_inpaint_mask(result.preserve_mask).getpixel((0, 0)) == 255


@pytest.mark.parametrize("product", [Image.new("RGB", (10, 10)), Image.new("RGBA", (10, 10))])
def test_missing_or_empty_alpha_is_rejected(product):
    with pytest.raises(ValueError):
        prepare_product_canvas(product, visual())


def test_product_area_without_a_whole_pixel_is_rejected():
    brief = visual(product_box=Rect(x=.5001, y=.5, width=.0001, height=.1))
    with pytest.raises(ValueError, match="whole pixels"):
        prepare_product_canvas(Image.new("RGBA", (2, 2), (1, 2, 3, 255)), brief)


def test_integrity_checks_opaque_pixels_only_and_reports_partial_transparency():
    foreground = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
    foreground.putpixel((0, 0), (20, 40, 60, 255))
    foreground.putpixel((1, 0), (20, 40, 60, 128))
    preview = composite_approved_foreground(Image.new("RGB", (3, 1), "white"), foreground)
    report = inspect_composite(preview, foreground)
    assert report.opaque_pixels == 1
    assert report.partial_alpha_pixels == 1
    assert report.mismatched_pixels == 0
    assert report.opaque_preserved
    assert report.needs_manual_check
    preview.putpixel((0, 0), (21, 40, 60, 255))
    damaged = inspect_composite(preview, foreground)
    assert damaged.mismatched_pixels == 1
    assert not damaged.opaque_preserved
    assert damaged.needs_manual_check


def test_transparent_preview_cannot_pass_using_hidden_rgb_values():
    foreground = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
    hidden = Image.new("RGBA", (2, 2), (10, 20, 30, 0))
    report = inspect_composite(hidden, foreground)
    assert report.opaque_pixels == report.mismatched_pixels == 4
    assert not report.opaque_preserved


def test_rgb_preview_is_supported_and_transparent_background_is_not_compared():
    foreground = Image.new("RGBA", (2, 1), (10, 20, 30, 255))
    foreground.putpixel((1, 0), (255, 0, 0, 0))
    preview = Image.new("RGB", (2, 1), (10, 20, 30))
    report = inspect_composite(preview, foreground)
    assert report.opaque_pixels == 1
    assert report.mismatched_pixels == 0
    assert report.opaque_preserved
    assert not report.needs_manual_check


def test_no_opaque_pixels_is_not_a_vacuous_pixel_preservation_pass():
    foreground = Image.new("RGBA", (2, 2), (10, 20, 30, 128))
    report = inspect_composite(Image.new("RGB", (2, 2), "white"), foreground)
    assert report.opaque_pixels == 0
    assert report.partial_alpha_pixels == 4
    assert not report.opaque_preserved
    assert report.needs_manual_check


@pytest.mark.parametrize("preview,foreground", [
    (Image.new("RGB", (2, 1)), Image.new("RGBA", (1, 1), (1, 2, 3, 255))),
    (Image.new("RGB", (1, 1)), Image.new("RGB", (1, 1))),
    (Image.new("RGB", (1, 1)), Image.new("RGBA", (1, 1))),
    (Image.new("L", (1, 1)), Image.new("RGBA", (1, 1), (1, 2, 3, 255))),
])
def test_integrity_rejects_invalid_image_modes_empty_alpha_or_canvas_mismatch(preview, foreground):
    with pytest.raises(ValueError):
        inspect_composite(preview, foreground)


def test_prepared_candidate_can_be_recomposited_without_changing_approved_opaque_pixels():
    prepared = prepare_product_canvas(Image.new("RGBA", (10, 30), (45, 76, 130, 255)), visual())
    preview = composite_approved_foreground(Image.new("RGB", (256, 256), "yellow"), prepared.product_rgba)
    report = inspect_composite(preview, prepared.product_rgba)
    assert report.opaque_preserved
    assert report.opaque_pixels == 64 * 192
    assert not report.needs_manual_check
