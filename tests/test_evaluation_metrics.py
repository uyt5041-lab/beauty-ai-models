from dataclasses import FrozenInstanceError, asdict

from PIL import Image
import pytest

from beauty_ai.evaluation_metrics import MaskMetrics, mask_metrics


def points(size, coordinates):
    image = Image.new("L", size, 0)
    for coordinate in coordinates:
        image.putpixel(coordinate, 255)
    return image


def test_shifted_squares_have_analytic_iou_and_exact_boundary_scores():
    predicted = Image.new("L", (5, 4), 0)
    predicted.paste(255, (1, 1, 3, 3))
    reference = Image.new("L", (5, 4), 0)
    reference.paste(255, (2, 1, 4, 3))
    exact = mask_metrics(predicted, reference, tolerance=0)
    # Two overlapping pixels out of six in the union; each 2x2 square has
    # four boundary pixels, of which two coincide.
    assert exact.iou == pytest.approx(1 / 3)
    assert exact.boundary_precision == exact.boundary_recall == exact.boundary_f1 == .5
    tolerant = mask_metrics(predicted, reference, tolerance=1)
    assert tolerant.iou == exact.iou
    assert tolerant.boundary_precision == tolerant.boundary_recall == tolerant.boundary_f1 == 1


def test_precision_and_recall_have_distinct_denominators():
    predicted = points((7, 3), [(1, 1), (3, 1), (5, 1)])
    reference = points((7, 3), [(1, 1), (3, 1)])
    result = mask_metrics(predicted, reference, tolerance=0)
    assert result.iou == pytest.approx(2 / 3)
    assert result.boundary_precision == pytest.approx(2 / 3)
    assert result.boundary_recall == 1
    assert result.boundary_f1 == pytest.approx(.8)
    reversed_result = mask_metrics(reference, predicted, tolerance=0)
    assert reversed_result.boundary_precision == 1
    assert reversed_result.boundary_recall == pytest.approx(2 / 3)
    assert reversed_result.boundary_f1 == result.boundary_f1


def test_full_canvas_keeps_border_boundary_with_zero_outside():
    full = Image.new("L", (3, 3), 255)
    frame = Image.new("L", (3, 3), 255)
    frame.putpixel((1, 1), 0)
    result = mask_metrics(full, frame, tolerance=0)
    assert result.iou == pytest.approx(8 / 9)
    assert result.boundary_precision == result.boundary_recall == result.boundary_f1 == 1


@pytest.mark.parametrize("size", [(1, 1), (1, 5), (5, 1)])
def test_single_pixel_and_thin_full_canvases_have_boundaries(size):
    full = Image.new("L", size, 255)
    result = mask_metrics(full, full, tolerance=0)
    assert result.iou == result.boundary_precision == result.boundary_recall == result.boundary_f1 == 1


def test_tolerance_is_square_and_does_not_change_overlap():
    predicted = points((7, 7), [(1, 1)])
    reference = points((7, 7), [(3, 3)])
    assert mask_metrics(predicted, reference, tolerance=1).boundary_f1 == 0
    result = mask_metrics(predicted, reference, tolerance=2)
    assert result.iou == 0
    assert result.boundary_precision == result.boundary_recall == result.boundary_f1 == 1
    assert mask_metrics(predicted, reference, tolerance=8).boundary_f1 == 1


def test_two_empty_masks_do_not_produce_a_vacuous_perfect_score():
    empty = Image.new("L", (3, 3), 0)
    result = mask_metrics(empty, empty)
    assert asdict(result) == {
        "iou": None, "boundary_precision": None, "boundary_recall": None,
        "boundary_f1": None, "threshold": 128, "tolerance": 2,
    }


def test_one_empty_mask_has_zero_f1_and_undefined_absent_side_rate():
    empty = Image.new("L", (3, 3), 0)
    foreground = points((3, 3), [(1, 1)])
    absent_prediction = mask_metrics(empty, foreground)
    assert absent_prediction == MaskMetrics(0, None, 0, 0, 128, 2)
    absent_reference = mask_metrics(foreground, empty)
    assert absent_reference == MaskMetrics(0, 0, None, 0, 128, 2)


def test_threshold_is_inclusive_and_reports_its_configuration():
    predicted = Image.new("L", (4, 1))
    predicted.putdata([0, 127, 128, 255])
    reference = points((4, 1), [(2, 0), (3, 0)])
    assert mask_metrics(predicted, reference, threshold=128, tolerance=0).iou == 1
    assert mask_metrics(predicted, reference, threshold=127).iou == pytest.approx(2 / 3)
    assert mask_metrics(predicted, reference, threshold=255).iou == .5
    result = mask_metrics(predicted, reference, threshold=1, tolerance=3)
    assert result.iou == pytest.approx(2 / 3)
    assert result.threshold == 1 and result.tolerance == 3


@pytest.mark.parametrize("mode", ["1", "L", "RGB", "RGBA"])
def test_grayscale_modes_are_equivalent_and_inputs_are_unchanged(mode):
    reference = points((3, 3), [(0, 0), (1, 1), (2, 2)])
    predicted = reference.convert(mode)
    predicted.info["source"] = "fixture"
    before = (predicted.mode, predicted.size, predicted.tobytes(), predicted.info.copy())
    reference_before = reference.tobytes()
    result = mask_metrics(predicted, reference, tolerance=0)
    assert result.iou == result.boundary_f1 == 1
    assert (predicted.mode, predicted.size, predicted.tobytes(), predicted.info) == before
    assert reference.tobytes() == reference_before
    with pytest.raises(FrozenInstanceError):
        result.iou = 0


@pytest.mark.parametrize("mode,color", [
    ("RGB", (128, 128, 127)), ("RGBA", (0, 1, 0, 255)),
])
def test_colored_masks_are_rejected_even_if_conversion_would_match(mode, color):
    colored = Image.new(mode, (2, 2), color)
    with pytest.raises(ValueError, match="grayscale"):
        mask_metrics(colored, Image.new("L", (2, 2), 128))


@pytest.mark.parametrize("alpha", [0, 1, 254])
def test_rgba_transparency_cannot_hide_foreground(alpha):
    mask = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
    mask.putpixel((1, 1), (255, 255, 255, alpha))
    with pytest.raises(ValueError, match="opaque"):
        mask_metrics(Image.new("L", (2, 2), 255), mask)


@pytest.mark.parametrize("mode", ["P", "LA", "I", "I;16", "F", "CMYK"])
def test_unsupported_modes_are_not_silently_clipped_or_interpreted(mode):
    with pytest.raises(ValueError, match="Masks must use"):
        mask_metrics(Image.new(mode, (2, 2)), Image.new("L", (2, 2)))


def test_canvas_mismatch_and_zero_dimensions_are_rejected():
    with pytest.raises(ValueError, match="same canvas"):
        mask_metrics(Image.new("L", (2, 2)), Image.new("L", (2, 3)))
    with pytest.raises(ValueError, match="nonzero"):
        mask_metrics(Image.new("L", (0, 2)), Image.new("L", (0, 2)))


def test_oversized_mask_is_rejected_before_loading_pixels(monkeypatch):
    oversized = Image.new("L", (4001, 4000))
    def unexpected_load():
        pytest.fail("Oversized masks must be rejected before pixel loading")
    monkeypatch.setattr(oversized, "load", unexpected_load)
    with pytest.raises(ValueError, match="pixel limit"):
        mask_metrics(oversized, oversized)


@pytest.mark.parametrize("parameter,value", [
    ("threshold", 0), ("threshold", 256), ("threshold", -1),
    ("threshold", True), ("threshold", 128.0), ("threshold", "128"),
    ("tolerance", -1), ("tolerance", 9), ("tolerance", False),
    ("tolerance", 2.0), ("tolerance", "2"),
])
def test_parameters_require_bounded_integers(parameter, value):
    image = Image.new("L", (1, 1), 255)
    with pytest.raises(ValueError, match="integer"):
        mask_metrics(image, image, **{parameter: value})


@pytest.mark.parametrize("predicted,reference", [(None, Image.new("L", (1, 1))),
                                                    (Image.new("L", (1, 1)), b"mask")])
def test_non_images_are_rejected(predicted, reference):
    with pytest.raises(ValueError, match="Pillow"):
        mask_metrics(predicted, reference)
